# Mac LaunchAgent hub (always-on at home)

| | |
|---|---|
| **Status** | proposed |
| **Effort** | XS |
| **Complexity** | low |
| **Risk** | low |
| **Depends on** | none |
| **Unlocks** | always-on hub without [[mlx-decoupled-hub]] / [[cloud-hub-deploy]] effort |

## Why

If you have a Mac at home that's mostly plugged in (or a Mac mini), it makes
a perfectly good always-on hub right now — no refactor, no cloud cost. This
plan is the *quickest path* to "hub is always reachable, friends can connect
any time" using existing dots.

Useful as a stepping stone before [[mlx-decoupled-hub]] + [[cloud-hub-deploy]]
land.

## What changes

Pure ops + scripts. No `dots/` source changes.

- `scripts/run-hub.sh` — wrapper script that `git pull`s before `exec`-ing
  the hub. Captures HF_TOKEN from env or `.env`.
- `scripts/com.dots.hub.plist` — a LaunchAgent template the user customizes
  with paths.
- `scripts/install-launchagent.sh` — installs the plist into
  `~/Library/LaunchAgents/` and loads it via `launchctl`.
- `scripts/auto-deploy.sh` — cron helper that pulls and kickstarts the
  agent on every minute if there's new code on `main`.
- `docs/launchagent-hub.md` — user-facing setup walkthrough.

## Approach

LaunchAgent runs `run-hub.sh` at user login, restarts on crash, redirects
output to `~/Library/Logs/dots-hub.{log,err}`.

```xml
<plist>
  <dict>
    <key>Label</key><string>com.dots.hub</string>
    <key>ProgramArguments</key><array>
      <string>/Users/<user>/Desktop/dots/scripts/run-hub.sh</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>.../Logs/dots-hub.log</string>
    <key>StandardErrorPath</key><string>.../Logs/dots-hub.err</string>
    <key>EnvironmentVariables</key><dict>
      <key>HF_TOKEN</key><string>hf_xxx</string>
    </dict>
  </dict>
</plist>
```

Auto-deploy via cron (`crontab -e`):

```
* * * * * cd ~/Desktop/dots && git fetch --quiet \
          && git diff HEAD origin/main --quiet \
          || (git pull --ff-only \
              && launchctl kickstart -k gui/$(id -u)/com.dots.hub)
```

Friends connect via Tailscale to the Mac's tailnet IP. No public exposure
needed unless you want it (`sudo tailscale funnel 5555 on`).

## Key design decisions

| Option | Decision |
|---|---|
| **LaunchAgent (user-level)** | **chosen** — runs at login, no sudo |
| LaunchDaemon (system-level) | considered — runs even before login; needs sudo. Use if you want pre-login hub |
| `tmux` + manual restart | rejected — no auto-restart |

| Option | Decision |
|---|---|
| **Cron poll for auto-deploy** | **chosen** — 1-line cron, simple |
| GitHub webhook | better latency, more setup |
| Self-hosted runner | overkill |

## Open questions

- **Sleep behavior.** A laptop closing its lid sleeps. The agent comes
  back on wake. If you need 24/7, plug in + disable sleep:
  ```
  sudo pmset -a sleep 0
  ```
- **HF_TOKEN secrecy.** The plist is a plaintext file on disk. macOS
  keychain integration is possible but adds complexity. Document the
  trade-off.
- **Logs rotation.** `~/Library/Logs/dots-hub.log` grows forever. Add a
  weekly logrotate alternative. For v1: tell users to `truncate -s 0` if
  it gets big.

## Test plan

- Install the agent on a Mac. Reboot. Hub comes back up at login.
- Kill the hub process. Within ~1 s, launchctl restarts it.
- Push a commit to `main`. Within 60 s, the hub restarts on the latest
  code. Verify via a sentinel string in the log.
- Disconnect Tailscale, reconnect. Friends still connect after
  reconnection — no manual intervention needed.

## Out of scope

- Cloud deployment ([[cloud-hub-deploy]] is the destination).
- HTTPS termination.
- Auto-installing the LaunchAgent from inside `dots` (manual script for now).

## Followups

- `dots daemon install` CLI subcommand to wrap the install steps.
- Once [[mlx-decoupled-hub]] lands, identical script logic ports to
  systemd on Linux for [[cloud-hub-deploy]].
