# dots

Your Macs, pooled, pay for your AI. You lend a Mac to a pool, earn credits for the work it does, and spend them on Claude at a discount. dots also gives every app on your Mac one address and one key. Each request goes to the cheapest place that can answer it well: this Mac, your pools, the wider network or Claude.

This is a native macOS app (SwiftUI, macOS 15+). The dots cloud doesn't exist yet, so a mock of it runs inside the app and speaks the same API the real one will ([API.md](API.md)). Everything about this Mac is real. Answers are real once you point dots at a model.

## What's real and what's mocked

| Real | Mocked by the cloud on this Mac |
| --- | --- |
| This Mac: its model name, memory, power source and idle time | The other people, Macs and pools |
| The lending rules, and the worker's heartbeat to the hub | When the hub has work, and the compute it measures |
| The local API in OpenAI's and Anthropic's formats, keys and their rules, routing, fallback, receipts and savings | Credits, the ledger, badges, endorsements, invites and weekly notes |
| Answers from a model on this Mac (LM Studio, Ollama) and from your own frontier key | Answers from pools and from the gateway. Receipts say so. |

The mock cloud keeps its world in `~/Library/Application Support/dots/mock-cloud.json`. This Mac's own part (your API keys, request log and lending rules) is in `local.json` beside it. Settings live in the app's preferences, and endpoint keys live in your login Keychain.

## Three places

- **The menu bar.** A ring when your Mac isn't lending, an orange ring while it lends, filled orange while it works on someone's question. Its menu has the lending switch, which pool to lend to, how much memory to lend, the plugged-in and in-use rules, your credits, and Pause for an hour.
- **The window.** Explore public pools, open a pool to see who's lending and what it can run, join, endorse, invite friends. Your profile has your credits, badges and history. The API page has your endpoint, keys and code samples.
- **The ask panel.** ⌥ Space from any app, or ⌘K in dots. Type or drop a file. Every answer carries a receipt: where it ran, on which Macs, why it went there, and what it cost.

## The 18 stories, and where they live

`--selftest` checks each one end to end (see Checks).

The router and the endpoint
1. One address and one key for every app, in OpenAI's format or Anthropic's: the **API** page.
2. `auto` sends each request to the cheapest place that can answer it well: this Mac, your pools, a public pool or Claude. A model id still pins it.
3. Every response says where it ran, why, and what it cost: the `dots` receipt, and the receipt in the ask panel.
4. Savings each month, against the model you'd otherwise send everything to: **Saved this month** on the API page. Pick Opus or Sonnet in Settings.
5. Rules for each key: private only, a cap in credits, a cap in dollars on your own key, and where it may go. **Create key**.
6. When a place doesn't answer, the request goes to the next, and the receipt says so.

Credits
7. Lend on your terms: dots starts and stops the worker as your rules say, and tells the hub. The menu bar shows the layers your Mac holds.
8. Credits for measured work, every entry visible: the ledger on your profile, with the time and the compute.
9. Spend credits on Claude at a discount, with no key of your own: the dots gateway, 30% off list price.
10. Credits in plain terms: "about 95 Opus prompts", on hover over your credits in the menu bar.
11. A friends' pool can offer its idle hours to the network, so its Macs earn between its own prompts: **Offer Idle Hours to the Network** in the pool's ••• menu.

Community
12. Joining a pool is a moment: "You're in."
13. An unlock names the Mac that tipped it: the Level moment, **Unlocked** on the pool's Level card, and the pool's activity.
14. Badges for showing up: 100 hours, Unlocker, Early backer, Pool starter.
15. A note each week: **This week** on your profile, and a notification.
16. An invite link shows what your Mac would unlock, for the amount you choose to lend: **Invite Link** in Explore, or a `dots://invite/CODE` link.
17. Explore shows which friends are in each pool and how many people back it, and you can endorse.
18. Lending together: a boost to plan, any time of day, with **Count Me In** on the pool's banner, which keeps your Mac awake and lending through the window; the nights your Mac lent, as your contribution fingerprint and timeline on your profile; and a recap the morning after a night of lending.

## Build and run

You need Xcode 16 or later and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`). The project file is generated from `project.yml`.

```bash
xcodegen generate
open dots.xcodeproj
```

Or from the command line:

```bash
xcodebuild -project dots.xcodeproj -scheme dots -configuration Debug -derivedDataPath build -destination 'platform=macOS,arch=arm64' build
open build/Build/Products/Debug/dots.app
```

Signing uses your Apple Development certificate (team 9P3ZR93NDF in `project.yml`).

## Real answers

Open Settings (⌘, or Settings in the sidebar) and set an endpoint under **Real answers**:

- **This Mac**: a model on this Mac answers everyday questions, privately and for free. In LM Studio, load a model and start the server (it listens on `http://localhost:1234/v1`), or run Ollama (`http://localhost:11434/v1`). Press **Test** to list the models and pick one. The mock cloud also uses it for your pools' answers, so those become real too.
- **Your frontier key**: Anthropic at `https://api.anthropic.com/v1` with your Anthropic API key, or any OpenAI-compatible provider. A key made for more than one Anthropic workspace also needs the workspace to act in: press **Test** and pick it, or paste its ID (`wrkspc_…`) from the Workspaces page in the Claude Console. dots sends it as `anthropic-workspace-id` on every request.

**Pay for Claude with** picks what pays for Claude answers. **Credits first** goes through the dots gateway and uses your key once credits run short. **My key first** uses your key and falls back to credits.

A route without an endpoint is simulated, and its receipt says so.

## The local API

dots serves OpenAI's API and Anthropic's on this Mac, and routes every request the way the ask panel does. Make a key on the API page. It's shown once.

```bash
curl http://localhost:8787/v1/chat/completions \
  -H "Authorization: Bearer $DOTS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Name three uses for a spare Mac"}]}'
```

```python
import os
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8787/v1", api_key=os.environ["DOTS_KEY"])
reply = client.chat.completions.create(model="auto", messages=[{"role": "user", "content": "Hello"}])
print(reply.choices[0].message.content)
```

```python
import os
import anthropic

client = anthropic.Anthropic(base_url="http://localhost:8787", api_key=os.environ["DOTS_KEY"])
message = client.messages.create(model="auto", max_tokens=1024, messages=[{"role": "user", "content": "Hello"}])
print(message.content[0].text)
```

- `GET /v1/models` lists `auto`, the model on this Mac, the pools' models and Claude.
- `POST /v1/chat/completions` (OpenAI) and `POST /v1/messages` (Anthropic) take `model`: `auto`, or a model id to pin where it runs.
- Each response carries a `dots` receipt: where it ran, why, what it cost and saved, whether it was private or simulated, and any fallback. Streams end with it.
- Errors come in your format: 401 without a valid key, 400 for a bad body or a model the key can't reach, 402 without enough credits, 409 when nothing the key can reach can take it, 429 past the key's monthly credits, 503 when nothing answered.

[API.md](API.md) has the routing order, the receipt's fields and the dots cloud's API.

## Checks

```bash
build/Build/Products/Debug/dots.app/Contents/MacOS/dots --selftest
```

Runs a fresh mock cloud of its own and the local API on spare ports, with stand-ins for LM Studio and for a provider behind your own key, and checks each of the 18 stories through the same HTTP calls other apps make (110 checks). A stand-in for Anthropic checks that a key made for more than one workspace names its workspace on every request. Set `DOTS_SELFTEST_ENDPOINT` to an OpenAI-compatible URL to check real answers too. Nothing is saved, and no real key is used.

```bash
build/Build/Products/Debug/dots.app/Contents/MacOS/dots --ask "Is my lease fair on repairs?"
```

Launches the app, opens the ask panel and asks the question, so the whole ask flow can be reproduced without clicking.

```bash
build/Build/Products/Debug/dots.app/Contents/MacOS/dots --snapshot ~/Desktop/dots-shots
```

Walks a fresh mock cloud through the main moments and saves each screen as a PNG: Explore with an invite, joining, the unlock, the menu bar while the Mac works, an answer, the profile with a weekly note, the API page, the sheets, an invite opened from a link, Settings, then Late Night Lab at 2 AM with its boost, your profile, the Level moment, and the morning recap.

```bash
build/Build/Products/Debug/dots.app/Contents/MacOS/dots --mock-cloud 8790
```

Runs only a fresh mock cloud, to try [API.md](API.md) with curl.

## The mock cloud

Settings, **dots cloud**:

- **Use the mock cloud** is on until there's a real cloud. Switch it off to give an address instead.
- **Send one now** sends this week's note.
- The **Speed** chips make lending time pass faster, for demos.
- **Reset…** puts back the sample network with your real Mac in it.

## Code

```
dots/
  App/        the app, the menu bar icon, ⌥ Space, the ask panel window, dots:// links, this Mac's signals
  Cloud/      the cloud's API as the app calls it, the client, pricing, and the mock cloud
  Design/     colors and type, the swirl and pool emblems, shared controls
  Model/      pools, the model ladder, sample data, the app model
  Service/    the simulation, the router, both API formats, the local API, endpoints and streaming, settings
  Views/      Main (sidebar, Explore, pool, profile, API, sheets, moments), MenuBar, Ask, Settings
  Snapshot/   --snapshot and --selftest
tools/        the app icon (icon.html, rendered by make-icon.mjs)
```

The design has one accent, electric orange (#FF5A1F), used only for you, for what's live, and for the one main action. The swirl shows up only in moments and pool emblems.

`tools/make-icon.mjs` renders `icon.html` with Playwright from `~/Desktop/niva-showreel` and writes the icon sizes into the asset catalog.
