//! Native macOS shell for dots.
//!
//! The heavy lifting (iroh transport + mlx compute) stays in Python. This shell
//! launches the local bridge (`backend/bridge.py`, packaged as the `dots-bridge`
//! sidecar in release) and talks to it over **stdio** with newline-delimited
//! JSON — the LSP model. The UI is bundled in the app (loaded via `tauri://`,
//! not a localhost server): request/response calls go through the `call`
//! command, and chat tokens stream back as `chat` events.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::image::Image;
use tauri::window::{Effect, EffectState, EffectsBuilder};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, Rect, State, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
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
                } else if let Some(name) = msg.get("event").and_then(Value::as_str) {
                    let state = msg.get("state").cloned().unwrap_or(Value::Null);
                    if name == "lend" {
                        LENDING.store(
                            state.get("lending").and_then(Value::as_bool).unwrap_or(false),
                            Ordering::SeqCst,
                        );
                    }
                    let _ = app.emit(name, state);
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
    async fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id, tx);
        self.write_line(format!(
            "{}\n",
            json!({ "id": id, "method": method, "params": params })
        ))
        .await?;
        rx.await.map_err(|_| "bridge closed".to_string())?
    }

    /// Fire a chat request; replies arrive as `chat` events, not here.
    async fn send_chat(&self, prompt: &str, max_tokens: u32, context: Value) -> Result<(), String> {
        self.write_line(format!(
            "{}\n",
            json!({ "method": "chat", "prompt": prompt, "max_tokens": max_tokens, "context": context })
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

/// True while this Mac is lending; drives the breathing menu bar dot.
static LENDING: AtomicBool = AtomicBool::new(false);
/// Millis timestamp of the last time a panel hid itself on blur, so the tray
/// click that caused the blur does not immediately reopen it.
static LAST_BLUR_HIDE: AtomicU64 = AtomicU64::new(0);

const ICON_IDLE: &[u8] = include_bytes!("../icons/tray/idle.png");
const ICON_LIT: [&[u8]; 4] = [
    include_bytes!("../icons/tray/lit0.png"),
    include_bytes!("../icons/tray/lit1.png"),
    include_bytes!("../icons/tray/lit2.png"),
    include_bytes!("../icons/tray/lit3.png"),
];

const TRAY_W: f64 = 316.0;
const TRAY_H: f64 = 360.0;
const ASK_W: f64 = 620.0;
const ASK_H: f64 = 64.0;

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[tauri::command]
async fn call(
    method: String,
    params: Option<Value>,
    bridge: State<'_, Arc<Bridge>>,
) -> Result<Value, String> {
    bridge.request(&method, params.unwrap_or(Value::Null)).await
}

#[tauri::command]
async fn chat(
    prompt: String,
    max_tokens: Option<u32>,
    context: Option<Value>,
    bridge: State<'_, Arc<Bridge>>,
) -> Result<(), String> {
    bridge
        .send_chat(&prompt, max_tokens.unwrap_or(256), context.unwrap_or(Value::Null))
        .await
}

/// Read a dropped text file so it can be attached to a question as context.
#[tauri::command]
fn read_text_file(path: String) -> Result<String, String> {
    const MAX: usize = 200_000;
    let bytes = std::fs::read(&path).map_err(|e| e.to_string())?;
    let text = String::from_utf8(bytes).map_err(|_| "not a text file".to_string())?;
    Ok(text.chars().take(MAX).collect())
}

#[tauri::command]
fn open_ask(app: AppHandle) {
    toggle_ask(&app);
}

#[tauri::command]
fn open_main(app: AppHandle) {
    show_main(&app);
}

fn show_main(app: &AppHandle) {
    if let Some(t) = app.get_webview_window("tray") {
        let _ = t.hide();
    }
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn make_panel(app: &AppHandle, label: &str, w: f64, h: f64, radius: f64) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, label, WebviewUrl::App("index.html".into()))
        .title(label)
        .inner_size(w, h)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .visible(false)
        .focused(false)
        .effects(
            EffectsBuilder::new()
                .effect(Effect::HudWindow)
                .state(EffectState::Active)
                .radius(radius)
                .build(),
        )
        .build()?;
    Ok(())
}

fn toggle_tray_panel(app: &AppHandle, rect: Rect) {
    let Some(w) = app.get_webview_window("tray") else {
        return;
    };
    if w.is_visible().unwrap_or(false) {
        let _ = w.hide();
        return;
    }
    if now_ms().saturating_sub(LAST_BLUR_HIDE.load(Ordering::SeqCst)) < 250 {
        return;
    }
    let scale = w.scale_factor().unwrap_or(2.0);
    let pos = rect.position.to_physical::<f64>(scale);
    let size = rect.size.to_physical::<f64>(scale);
    if let Ok(ws) = w.outer_size() {
        let x = pos.x + size.width / 2.0 - ws.width as f64 / 2.0;
        let y = pos.y + size.height + 6.0 * scale;
        let _ = w.set_position(PhysicalPosition::new(x as i32, y as i32));
    }
    let _ = w.show();
    let _ = w.set_focus();
    let _ = app.emit("tray-shown", ());
}

fn toggle_ask(app: &AppHandle) {
    let Some(w) = app.get_webview_window("ask") else {
        return;
    };
    if w.is_visible().unwrap_or(false) {
        let _ = w.hide();
        return;
    }
    // Upper third of the screen the cursor is on, centred horizontally.
    if let Ok(Some(m)) = w.current_monitor().or_else(|_| w.primary_monitor()) {
        let ws = w.outer_size().unwrap_or_default();
        let x = m.position().x + (m.size().width as i32 - ws.width as i32) / 2;
        let y = m.position().y + (m.size().height as f64 * 0.2) as i32;
        let _ = w.set_position(PhysicalPosition::new(x, y));
    }
    let _ = w.show();
    let _ = w.set_focus();
    let _ = app.emit("ask-shown", ());
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let menu = Menu::with_items(
        app,
        &[
            &MenuItem::with_id(app, "open", "Open dots", true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", "Quit dots", true, None::<&str>)?,
        ],
    )?;
    TrayIconBuilder::with_id("lend")
        .icon(Image::from_bytes(ICON_IDLE)?)
        .icon_as_template(false)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, e| match e.id().as_ref() {
            "open" => show_main(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                rect,
                ..
            } = event
            {
                toggle_tray_panel(tray.app_handle(), rect);
            }
        })
        .build(app)?;

    // Breathing dot while lending, hollow ring otherwise.
    let handle = app.clone();
    std::thread::spawn(move || {
        let mut i = 0usize;
        let mut was_lending = false;
        loop {
            let lending = LENDING.load(Ordering::SeqCst);
            if let Some(tray) = handle.tray_by_id("lend") {
                if lending {
                    if let Ok(img) = Image::from_bytes(ICON_LIT[i % ICON_LIT.len()]) {
                        let _ = tray.set_icon(Some(img));
                    }
                    i += 1;
                } else if was_lending || i == 0 {
                    if let Ok(img) = Image::from_bytes(ICON_IDLE) {
                        let _ = tray.set_icon(Some(img));
                    }
                    i = 1;
                }
            }
            was_lending = lending;
            std::thread::sleep(Duration::from_millis(450));
        }
    });
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle_ask(app);
                    }
                })
                .build(),
        )
        .setup(|app| {
            let handle = app.handle().clone();
            let bridge = tauri::async_runtime::block_on(Bridge::spawn(handle.clone()))?;
            app.manage(bridge);
            make_panel(&handle, "tray", TRAY_W, TRAY_H, 14.0)?;
            make_panel(&handle, "ask", ASK_W, ASK_H, 20.0)?;
            build_tray(&handle)?;
            app.global_shortcut()
                .register(Shortcut::new(Some(Modifiers::ALT), Code::Space))?;
            Ok(())
        })
        .on_window_event(|window, event| match event {
            // Closing the main window keeps the app (and lending) alive in the menu bar.
            tauri::WindowEvent::CloseRequested { api, .. } if window.label() == "main" => {
                api.prevent_close();
                let _ = window.hide();
            }
            tauri::WindowEvent::Focused(false)
                if window.label() == "tray" || window.label() == "ask" =>
            {
                LAST_BLUR_HIDE.store(now_ms(), Ordering::SeqCst);
                let _ = window.hide();
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![call, chat, read_text_file, open_main, open_ask])
        .build(tauri::generate_context!())
        .expect("error while building the dots app");

    app.run(|app, event| match event {
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen { .. } => show_main(app),
        tauri::RunEvent::Exit => {
            if let Some(bridge) = app.try_state::<Arc<Bridge>>() {
                let bridge = bridge.inner().clone();
                tauri::async_runtime::block_on(bridge.shutdown());
            }
        }
        _ => {}
    });
}
