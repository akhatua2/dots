# dots

Your Macs, pooled, pay for your AI. You lend a Mac to a pool, earn compute credits, and ask anything. dots sends each question to the best place for it: your own pools, the wider network, or a frontier model like Claude, at a discount paid in credits.

This is a native macOS app (SwiftUI, macOS 15+). The network is simulated on this Mac. Everything about this Mac is real, and answers are real once you point dots at a model.

## What's real and what's simulated

| Real | Simulated |
| --- | --- |
| This Mac: its model name, memory, power source and idle time | The other Macs, people and pools |
| The lending rules: only when plugged in, pause while you're using it | Credits, badges and endorsements |
| Answers, once an endpoint is set in Settings (LM Studio, Ollama, Anthropic or any OpenAI-compatible server) | Answers when no endpoint is set. The receipt says so. |
| The local API on `localhost:8787`, with keys you create in the app | Macs falling asleep mid-answer |

The simulation lives in `~/Library/Application Support/dots/network.json`, so it survives a relaunch. Settings live in the app's preferences, and endpoint keys live in your login Keychain.

## Three places

- **The menu bar.** A ring when your Mac isn't lending, an orange ring while it lends, filled orange while it works on someone's question. Its menu has the lending switch, which pool to lend to, how much memory to lend, the plugged-in and in-use rules, your credits, and Pause for an hour.
- **The window.** Explore public pools, open a pool to see who's lending and what it can run, join, endorse, invite friends. Your profile has your credits, badges and history. The API page has your endpoint, keys and code samples.
- **The ask panel.** ⌥ Space from any app, or ⌘K in dots. Type or drop a file. Every answer carries a receipt: where it ran, on which Macs, why it went there, and what it cost.

## The 16 stories, and where they live

Pools
1. Start a pool in one step: **Start a pool** in the sidebar, or ⌘N.
2. Invite friends with a link that shows what their Mac would unlock: a friends' pool page, **Share link**.
3. Browse public pools, with endorsements and the friends inside: **Explore**.
4. Preview a pool, then join by adding your Mac: the card at the top of a pool page.
5. Joining is a moment: "You're in.", with your Mac as the orange dot in the pool's swirl.
6. The pool unlocks bigger models as Macs join: the ladder on the pool page, the unlock moment, and the banner naming the Mac that tipped it over.
7. Endorse pools you trust, and be remembered as an early backer: **Endorse**, and the Early backer badge.

Lending
8. Lend in one click from the menu bar, and see the dot fill while your Mac works.
9. Lend on your terms: how much memory, only when plugged in, pause while you're using it, pause for an hour.
10. Earn compute credits whenever your Mac works for the network. A friends' pool can offer its idle hours to the network, so lending only to friends earns too.
11. A profile of what your Macs have done: badges, pools, endorsements and credit history. Click the pencil to change your name.

Asking
12. Ask from anywhere, and dots routes each question to your pools, the network or a frontier model, within your rules and credits. Pick a route yourself from **Auto**.
13. Every answer carries a receipt, with a second opinion from Claude one click away.
14. Your questions stay yours: a personal question or a document stays in pools you're in, and dots asks before sending it anywhere else.
15. Answers keep going when a Mac sleeps: another Mac in the pool picks up.
16. One endpoint and one key for your apps, with `auto` routing: the **API** page.

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

Open Settings (⌘,) and set an endpoint under **Real answers**:

- **Your pools**: stands in for the pool until dots has a real hub. Use a model on this Mac. In LM Studio, load a model and start the server (it listens on `http://localhost:1234/v1`), or run Ollama (`http://localhost:11434/v1`). Press **Test** to list the models and pick one.
- **Frontier models**: any OpenAI-compatible provider. Anthropic works at `https://api.anthropic.com/v1` with your Anthropic API key and a model such as `claude-opus-5-5` or `claude-sonnet-5`.

A route without an endpoint is simulated, and its receipt says so.

## The local API

dots serves the OpenAI API on this Mac, and routes every request the way the ask panel does. Make a key on the API page. It's shown once.

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

- `GET /v1/models` lists `auto`, the pool models and the frontier models.
- `POST /v1/chat/completions` takes `model` (`auto`, or a model id to pick where it runs), `messages` and `stream`.
- Each response carries a `dots` block with where it ran, why, what it cost, and whether it was private or simulated.
- Errors: 401 without a valid key, 400 for a bad body or a model the key can't reach, 402 without enough credits, 409 when nothing it can reach is running, 429 past the key's monthly limit.

## Checks

```bash
build/Build/Products/Debug/dots.app/Contents/MacOS/dots --selftest
```

Starts the local API on a spare port against a fresh, in-memory network and checks it end to end: keys, routing, privacy, credits, streaming and limits. Set `DOTS_SELFTEST_ENDPOINT` to an OpenAI-compatible URL to check real answers too. Nothing is saved.

```bash
build/Build/Products/Debug/dots.app/Contents/MacOS/dots --snapshot ~/Desktop/dots-shots
```

Walks a fresh network through the main moments (Explore, joining, the unlock, the menu bar, an answer, the profile, the API, the sheets) and saves each screen as a PNG.

## Start over

Settings, **Simulation**, **Reset…** puts back the sample network with your real Mac in it. The **Speed** chips there make credits and sleeping Macs move faster, for demos.

## Code

```
dots/
  App/        the app, the menu bar icon, ⌥ Space, the ask panel window, this Mac's signals
  Design/     colors and type, the swirl and pool emblems, shared controls
  Model/      pools, the model ladder, sample data, the app model
  Service/    the simulated network, the router, endpoints and streaming, the local API, settings
  Views/      Main (sidebar, Explore, pool, profile, API, sheets, moments), MenuBar, Ask, Settings
  Snapshot/   --snapshot and --selftest
tools/        the app icon (icon.html, rendered by make-icon.mjs)
```

The design has one accent, electric orange (#FF5A1F), used only for you, for what's live, and for the one main action. The swirl shows up only in moments and pool emblems.

`tools/make-icon.mjs` renders `icon.html` with Playwright from `~/Desktop/niva-showreel` and writes the icon sizes into the asset catalog.
