//! Native macOS shell for dots.
//!
//! The heavy lifting (iroh transport + mlx compute) stays in Python. This shell
//! launches the local bridge (`backend/bridge.py`, packaged as the `dots-bridge`
//! sidecar in release) and talks to it over **stdio** with newline-delimited
//! JSON — the LSP model. The UI is bundled in the app (loaded via `tauri://`,
//! not a localhost server): request/response calls go through the `call`
//! command, and chat tokens stream back as `chat` events.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{oneshot, Mutex};

/// Handle to the bridge subprocess: write requests to its stdin, await replies
/// correlated by id, and keep the child so we can shut it down.
struct Bridge {
    stdin: Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>,
    next_id: AtomicU64,
    child: Mutex<Option<Child>>,
}

impl Bridge {
    /// Spawn the bridge process, wire up the stdout reader, and return a handle.
    async fn spawn(app: AppHandle) -> Result<Arc<Bridge>, String> {
        let mut child = build_command()
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("failed to start dots bridge: {e}"))?;

        let stdin = child.stdin.take().ok_or("bridge stdin unavailable")?;
        let stdout = child.stdout.take().ok_or("bridge stdout unavailable")?;

        let bridge = Arc::new(Bridge {
            stdin: Mutex::new(Some(stdin)),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            child: Mutex::new(Some(child)),
        });

        // Reader task: one JSON object per line. {id,result|error} resolves a
        // pending call; {chat:...} is forwarded to the UI as an event.
        let reader_bridge = bridge.clone();
        tauri::async_runtime::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let Ok(msg) = serde_json::from_str::<Value>(&line) else {
                    continue;
                };
                if msg.get("chat").is_some() {
                    let _ = app.emit("chat", msg);
                } else if let Some(id) = msg.get("id").and_then(Value::as_u64) {
                    if let Some(tx) = reader_bridge.pending.lock().await.remove(&id) {
                        let result = match msg.get("error") {
                            Some(e) => Err(e.as_str().unwrap_or("bridge error").to_string()),
                            None => Ok(msg.get("result").cloned().unwrap_or(Value::Null)),
                        };
                        let _ = tx.send(result);
                    }
                }
            }
        });

        Ok(bridge)
    }

    async fn write_line(&self, line: String) -> Result<(), String> {
        let mut guard = self.stdin.lock().await;
        let stdin = guard.as_mut().ok_or("bridge is shutting down")?;
        stdin
            .write_all(line.as_bytes())
            .await
            .map_err(|e| e.to_string())?;
        stdin.flush().await.map_err(|e| e.to_string())
    }

    /// Send an id'd request and await its reply.
    async fn request(&self, method: &str) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id, tx);
        self.write_line(format!("{}\n", json!({ "id": id, "method": method }))).await?;
        rx.await.map_err(|_| "bridge closed".to_string())?
    }

    /// Fire a chat request; replies arrive as `chat` events, not here.
    async fn send_chat(&self, prompt: &str, max_tokens: u32) -> Result<(), String> {
        self.write_line(format!(
            "{}\n",
            json!({ "method": "chat", "prompt": prompt, "max_tokens": max_tokens })
        ))
        .await
    }

    /// Graceful shutdown: closing stdin makes the bridge exit and terminate its
    /// worker child; kill is a backstop if it doesn't.
    async fn shutdown(&self) {
        self.stdin.lock().await.take(); // drop ChildStdin → EOF on the bridge
        if let Some(mut child) = self.child.lock().await.take() {
            let _ = child.wait().await.is_ok() || child.kill().await.is_ok();
        }
    }
}

/// `DOTS_BRIDGE_CMD` (space-separated argv) overrides everything — `tauri dev`
/// points it at the repo's venv python + bridge.py. Otherwise run the
/// `dots-bridge` sidecar bundled next to this executable (release).
fn build_command() -> Command {
    if let Ok(cmd) = std::env::var("DOTS_BRIDGE_CMD") {
        let mut parts = cmd.split_whitespace();
        let prog = parts.next().expect("DOTS_BRIDGE_CMD is set but empty");
        let mut c = Command::new(prog);
        c.args(parts);
        return c;
    }
    let dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .expect("cannot locate executable dir");
    Command::new(dir.join("dots-bridge"))
}

#[tauri::command]
async fn call(method: String, bridge: State<'_, Arc<Bridge>>) -> Result<Value, String> {
    bridge.request(&method).await
}

#[tauri::command]
async fn chat(
    prompt: String,
    max_tokens: Option<u32>,
    bridge: State<'_, Arc<Bridge>>,
) -> Result<(), String> {
    bridge.send_chat(&prompt, max_tokens.unwrap_or(256)).await
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            let bridge = tauri::async_runtime::block_on(Bridge::spawn(handle))?;
            app.manage(bridge);
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                if let Some(bridge) = window.try_state::<Arc<Bridge>>() {
                    let bridge = bridge.inner().clone();
                    tauri::async_runtime::block_on(bridge.shutdown());
                }
            }
        })
        .invoke_handler(tauri::generate_handler![call, chat])
        .run(tauri::generate_context!())
        .expect("error while running the dots app");
}
