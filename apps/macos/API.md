# The dots cloud API

The contract between the dots app and the dots cloud. The cloud has three parts:

- **The hub** runs the pools: which Macs lend, the layers each one holds, the work it measures, and the pools' answers.
- **The server** keeps people, invites, the credit ledger, badges and weekly notes.
- **The gateway** answers with frontier models, paid in credits.

The app talks to all three over HTTP. Until they exist, a mock cloud inside the app answers every call below (`dots/Cloud/MockCloud.swift`). Each part can become a real service by answering the same calls.

Try it with curl:

```bash
build/Build/Products/Debug/dots.app/Contents/MacOS/dots --mock-cloud 8790
```

```bash
curl -H "Authorization: Bearer dots_mock_local" http://127.0.0.1:8790/v1/state
```

The second half of this page covers the API dots serves to your own apps on `localhost:8787`.

## Conventions

- One base URL and one token for all three parts, sent as `Authorization: Bearer <token>`. The mock's token is `dots_mock_local`. Without it, every call is a 401.
- Bodies are JSON with ISO 8601 dates and camelCase fields. The hub's and the gateway's answers use OpenAI's and Anthropic's own formats.
- Errors are `{"error": {"message": "…", "type": "…"}}`. The types are `authentication_error` (401), `invalid_request_error` (400), `insufficient_credits` (402), `permission_error` (403) and `not_found_error` (404). The gateway's `/messages` uses Anthropic's error shape instead.
- Every change answers with the whole sync (below), so the app sees the result without a second call.
- Credits are whole numbers. A credit is worth `terms.creditUSD`, which is $0.001 in the mock.

## Sync

`GET /v1/state` returns everything the app shows from the cloud:

```json
{
  "state":   { "credits": 1245, "pools": [], "creditLog": [], "badges": [] },
  "terms":   { "creditUSD": 0.001, "discount": 0.3, "typicalInput": 1500, "typicalOutput": 600,
               "prices": { "claude-opus-5-5": { "input": 4, "output": 20 }, "claude-sonnet-5-5": { "input": 2, "output": 10 } } },
  "note":    { "headline": "Your Mac lent 31 hours this week and earned 62 credits." },
  "worker":  { "poolID": "studio-9", "poolName": "Studio 9", "model": "Qwen3 235B",
               "firstLayer": 86, "lastLayer": 93, "layers": 94, "working": false, "network": false },
  "invites": [ { "code": "LATE7Q", "poolName": "Late Night Lab", "fromName": "Sam", "addGB": 16, "unlocks": "Qwen3 235B" } ],
  "mock": true
}
```

`note` is the latest weekly note and `worker` is there while this Mac lends. `invites` are invites waiting for you.

### state

| Field | |
| --- | --- |
| `meID`, `thisMacID` | You, and this Mac |
| `people` | `{id, name}` |
| `devices` | `{id, ownerID, kind, memoryGB}`. A Mac keeps a quarter of its memory, and at least 8 GB. |
| `pools` | Pools, below |
| `credits`, `earnedAllTime` | Your balance, and everything earned |
| `creditLog` | The ledger, newest first, below |
| `badges` | `{id, symbol, title, detail, date, isNew}`. The ids are `hours-100`, `unlock-<pool>-<model>`, `backer-<pool>` and `starter`. |
| `questionsHelped`, `hoursLent`, `computePF` | Your Mac's totals: the runs it took part in, the hours it lent, and `computePF`, the compute the hub measured, in petaFLOPs. |
| `nights` | The nights this Mac lent, oldest first: `{date, poolID, minutes, runs, from, to}`. Nights run from noon to noon; `date` is the noon one began. Tonight's grows as the Mac lends. |
| `lendingShares` | The pools this Mac lends to, an amount to each: `{poolID, gb, since, hubPool}`. `hubPool` is the pool's name at the hub, which that pool's worker gives (`--pool`); the hub's own pool is `""`. The app runs one worker per share, held to its `gb`. |
| `networkLendingNow`, `endorsedBefore` | Macs lending across the network, and endorsements from before this history |

### A pool

```json
{
  "id": "late-night-lab", "name": "Late Night Lab", "isPublic": false, "blurb": "Sam and Ana's Macs, for code at night.",
  "memberIDs": ["sam", "ana"], "hostID": "sam",
  "macs": [ { "macID": "mac-sam", "lendGB": 96, "state": "lending", "joinedAt": "2026-10-07T00:24:45Z" },
            { "macID": "mac-ana", "lendGB": 24, "state": "lending", "joinedAt": "2026-10-10T00:24:45Z" } ],
  "crowdMacs": 0, "crowdLending": 0, "crowdGB": 0,
  "endorsements": 0, "endorsedByMe": false, "offersIdleToNetwork": false,
  "invite": { "code": "LATE7Q", "expires": "2026-10-26T00:24:45Z" },
  "unlocks": [], "tags": [], "phase": 0.7, "speedFactor": 1, "demand": 0.5, "createdAt": "2026-10-07T00:24:45Z",
  "boost": { "title": "DeepSeek Boost", "modelID": "deepseek-v3.1", "starts": "2026-10-24T12:00:00Z", "hours": 4, "plannedBy": "sam",
             "pledges": [ { "personID": "sam", "gb": 96, "guest": false }, { "personID": "theo", "gb": 96, "guest": true } ] },
  "activity": [ { "id": "lnl-million", "date": "2026-10-18T00:24:45Z", "kind": "milestone", "text": "Passed a million tokens" } ]
}
```

- A Mac's `state` is `lending`, `online` or `asleep`.
- A public pool counts its Macs in `crowdMacs`, `crowdLending` and `crowdGB` rather than listing them.
- What a pool runs follows from the memory its lending Macs pool together. The ladder is in `dots/Model/Models.swift`: Qwen3 235B at 136 GB, DeepSeek V3.1 at 396 and Kimi K2 at 620. `servesModelID` lets a pool prefer a model, such as a coding model.
- A pool started from a link runs that one model, whatever its memory: `servesModelID`, `modelName`, `kind` (`text` or `pictures`) and `modelGB`, the size of its weights, read from the repo. A pool that writes text needs that much across its Macs; a pool that draws needs it on one Mac, so a Mac with less can join it and ask it but not lend to it.
- Each line of the ladder says whether its model reads images (`readsImages`): a question with an image only goes
  to a pool whose model does.
- `unlocks` records each model the pool reached: `{id, modelID, byMacID, fromGB, toGB, date}`. `byMacID` is the Mac that tipped it over.
- `phase` shapes the pool's emblem. `speedFactor` scales the speed you get, and `demand` scales what a lending Mac earns.
- `boost` is a window, at any time of day, when the pool's members commit their Macs at once, to reach a bigger model for a few hours. Its target is that model's line on the ladder. A pledge from someone outside the pool is a `guest`, boosting just that once. Through the window, the app keeps a pledged Mac awake and lending its pledge to that pool, then puts its lending back as it was.
- `activity` is what happened in the pool lately. `kind` is `thanks` (a friend thanked your Mac, once it has done 20 runs there in a night), `away` (a Mac stepped away, with the `gb` it paused), `boost` (a boost was planned) or `milestone` (with its `text`). The app adds unlocks and new members from the pool itself.
- The pool's art is drawn from the pool on the Mac, the same way every time, so the cloud sends no art.

### A ledger entry

```json
{ "id": "F60862DB-…", "date": "2026-10-19T00:24:47Z", "amount": 2, "note": "Your Mac lent to Studio 9",
  "kind": "lend", "poolID": "studio-9", "minutes": 60, "computePF": 0.3 }
```

`kind` is one of three:

- `lend` is work, or waiting for work, in your pool.
- `network` is the network's work, through a friends' pool that offers its idle hours.
- `spend` is an answer you paid for. Its `amount` is negative.

Lending adds one entry per session. The entry grows as the session goes on.

## The hub

### POST /hub/v1/heartbeat

The app's worker reports every couple of seconds. `active` is the result of this Mac's own rules: switched on, plugged in, idle, not paused. They apply to all its shares at once; the time counts in each pool it lends to.

```json
{ "active": true, "seconds": 2.0, "macKind": "MacBook Air", "memoryGB": 24 }
```

`seconds` is the time since the last report. The demo speed in Settings scales it.

The answer is `{sync, unlock}`. `unlock` is set when this report took a pool over a line; its `poolID` says which.

While a Mac lends, the hub does three things:

- It decides when there's work. `worker.working` means work for its own pool, and `worker.network` means the network's work through a friends' pool that offers idle hours.
- It credits the time, at the pool's rate for this Mac's memory. The rate is higher while the Mac works.
- It measures the compute: two FLOPs per active parameter for each token, at the pool's speed, times this Mac's share of the pool.

### POST /hub/v1/pools/:id/chat/completions

A pool's answer, in OpenAI's chat completions format. Public pools charge the model's credits. Your own pools are free.

`X-Dots-Privacy: mine` asks for Only my Macs: the hub runs it only when every Mac holding the model is yours, and otherwise answers 409 without running it anywhere. The same header on a pool's pictures draws only on a Mac of yours. The app sends it for your documents and photos; questions that look personal stay in your pools by the app's routing, and the rest may go anywhere.

A streamed answer carries `dots` events beside the usual chunks:

```
data: {"choices":[{"delta":{},"index":0}],"dots":{"event":"macs","macs":[{"id":"mac-maya","initial":"M","name":"Maya"}],"model":"Qwen3 235B","pool":"Studio 9","simulated":true},…}
data: {"choices":[{"delta":{"content":"This"},"index":0}],…}
data: {"dots":{"event":"asleep","mac_id":"mac-priya","name":"Priya"},…}
data: {"dots":{"event":"resumed"},…}
data: {"choices":[{"delta":{},"finish_reason":"stop","index":0}],"usage":{"prompt_tokens":7,"completion_tokens":65,"total_tokens":72},"dots":{"credits":0,"pool":"Studio 9","simulated":true},…}
data: [DONE]
```

- A pool draws pictures at `POST /hub/v1/pools/:id/images/generations` and changes them at `.../images/edits`, in
  OpenAI's image API, streamed as they form (`image_generation.partial_image` after each step, then `...completed`).
  Ask sends a question there when it asks for a picture, and shows each step as it comes.
- A question can carry images as OpenAI-style parts, `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`,
  sent inline. The app shrinks them to `imageMaxPixels` first.
- `macs` comes first and lists the Macs answering.
- `asleep` and `resumed` mark a Mac that went to sleep mid-answer while the others carried on.
- The last chunk has the usage and what the answer cost.

A whole answer carries the same `dots` block, with `macs` in it.

The errors are 403 for a private pool you're not in, 402 without enough credits, and 404 when the pool runs nothing.

The mock simulates the words, unless pools are given a model to come from. The app does that when Settings has a model on this Mac.

## The server

| Call | Body | Answer |
| --- | --- | --- |
| `POST /v1/pools` | `{id, name, isPublic}` | `{pool, sync}`. The app picks the id, so its pool and the cloud's are the same one. |
| `POST /v1/pools/:id/join` | `{gb}` | `{macNumber, poolID, unlock, sync}`. Joining lets you ask the pool; `gb` above 0 also lends it that much. A Mac can lend to several pools, an amount to each. |
| `POST /v1/pools/:id/share` | `{gb}` | sync. How much this Mac lends a pool it is in; 0 stops lending there and stays in it. |
| `POST /v1/pools/:id/leave` | | sync |
| `POST /v1/pools/:id/endorse` | | `{badge, sync}`. Once per person. Under 20 Macs, it earns Early backer. |
| `POST /v1/pools/:id/invite` | `{code}` | `{invite, sync}`. Good for a week. |
| `DELETE /v1/pools/:id/invite` | | sync |
| `PATCH /v1/pools/:id` | `{offersIdleToNetwork}` | sync |
| `POST /v1/pools/:id/boost/pledge` | `{gb}` | sync. Counts you in for the pool's boost, as a guest if you're not in the pool. |
| `DELETE /v1/pools/:id/boost/pledge` | | sync. Counts you out. |
| `GET /v1/invites/:code?memory_gb=24` | | The invite, and what a Mac with that memory would add and unlock (below) |
| `POST /v1/invites/:code/accept` | `{gb}` | As join |
| `PATCH /v1/me` | `{name}` | sync |
| `POST /v1/badges/seen` | | sync |
| `GET /v1/pricing` | | `terms` |
| `GET /v1/notes/latest` | | The latest weekly note, or 404 |

An invite, opened before joining:

```json
{ "code": "LATE7Q", "poolID": "late-night-lab", "poolName": "Late Night Lab", "fromName": "Sam",
  "blurb": "Sam and Ana's Macs, for code at night.", "macs": 2, "pooledGB": 120, "modelNow": "Llama 3.3 70B",
  "addGB": 16, "unlocks": "Qwen3 235B", "isMember": false }
```

When the Mac wouldn't cross a line, `unlocks` is missing. `nextModel` and `shortGB` say how far the pool would still be.

A weekly note:

```json
{ "id": "note-1792369487886", "issued": "2026-10-19T00:24:47Z", "headline": "Your Mac lent 1 hour this week and earned 2 credits.",
  "hoursLent": 1, "computePF": 0, "creditsEarned": 2, "creditsSpent": 0, "questionsHelped": 0,
  "pools": [ { "name": "Studio 9", "answered": 840, "sharePercent": 8 } ], "badges": [] }
```

The mock sends a note once a week of the app's time, which the demo speed in Settings can speed up. It has two more calls for demos: `POST /v1/mock/note` sends this week's note now, and `POST /v1/mock/reset` goes back to the sample network.

## The gateway

There are two calls, for Claude models named by id (`claude-opus-5-5`, `claude-sonnet-5-5`):

- `POST /gateway/v1/chat/completions` takes OpenAI's format.
- `POST /gateway/v1/messages` takes Anthropic's.

Each answer costs list price less the discount, at what a credit is worth, rounded up to a whole credit (at least one). For example, 1,500 tokens in and 600 out on Claude Opus 5.5 is $0.018 at list price, which is 13 credits through the gateway. When the balance can't cover an answer, the gateway answers 402 `insufficient_credits`.

What it charged comes back in three ways:

- whole answers carry `"dots": {"credits": 13, "list_credits": 18, "discount": 0.3, "simulated": true}`;
- OpenAI-format streams carry the same block on their last chunk;
- Anthropic-format streams carry `x-dots-credits` and `x-dots-simulated` headers.

The mock writes a stand-in answer. The real gateway calls Anthropic.

## What dots serves your apps

The app serves one API on `localhost:8787`, in OpenAI's format and Anthropic's. It takes the keys you make on the API page, as `Authorization: Bearer` or `x-api-key`.

| Call | |
| --- | --- |
| `GET /v1/models` | `auto`, the model on this Mac, the pools' models and Claude, in a shape both SDKs read |
| `POST /v1/chat/completions` | OpenAI's format |
| `POST /v1/messages` | Anthropic's format |
| `POST /v1/messages/count_tokens` | An estimate, about four characters a token |

### Routing

`model: "auto"` lets dots pick where each request runs. It considers places in this order:

1. **Privacy.** A personal question, or any request from a private key, stays on this Mac or in your own pools.
2. **Tools and images.** Tools only go to a frontier model. Images go to a model that reads them: Claude, or a pool
   whose model has `readsImages` on its line of the ladder.
3. **This Mac.** It takes everyday questions that fit its 32,000-token context.
4. **Your own pools.** They're free, when their model is big enough for the request.
5. **Paid places.** Public pools, then Claude. When the request is hard or you prefer quality, Claude comes first.

A model id pins the place:

- `claude-opus-5-5` goes to Claude.
- `kimi-k2` runs in your own pool if one has it, or else in the fastest public pool that does.
- The id of the model on this Mac runs it there.

Claude is paid in credits through the gateway, or billed to your own key, as Settings says. If a place doesn't answer, the request goes to the next place allowed by the same rules.

### The receipt

Every answer says where it ran, why and what it cost:

```json
"dots": {
  "ran_on": "Studio 9 · 4 Macs", "model": "Qwen3 235B",
  "why": ["You're in Studio 9, so it ran there free.", "Qwen3 235B is enough for this.", "Answered through the dots hub."],
  "credits": 0, "usd": 0, "saved_usd": 0.0013, "compared_with": "claude-opus-5-5",
  "private": false, "simulated": true, "input_tokens": 12, "output_tokens": 61,
  "fallback": "This Mac didn't answer, so it went to Studio 9."
}
```

`fallback` is only there when a place didn't answer.

- **Whole answers:** the receipt is in the body.
- **OpenAI-format streams:** it's in a last chunk with `choices: []`, before `data: [DONE]`.
- **Anthropic-format streams:** it's a last `event: dots`, after `message_stop`. Anthropic's SDKs skip event names they don't know.
- **Headers:** `x-dots-route`, `x-dots-private` and `x-dots-fallback`.

`usd` is what your own key was billed. `saved_usd` is what the compared model would have cost at list price, less what this answer cost.

### Key rules

Each key can have any of these rules:

- **Reach:** your pools, public pools, Claude.
- **Private:** this Mac and your own pools only.
- **A monthly cap in credits.**
- **A monthly cap in dollars on your own key.** Past it, Claude goes through the gateway in credits.

### Errors

Errors come back in the shape of the format you used:

| Code | When |
| --- | --- |
| 401 | No valid key |
| 400 | A bad body, or a model the key can't reach |
| 402 | Not enough credits, and no key of your own to fall back on |
| 409 | Nothing the key can reach can take the request, such as tools from a private key |
| 429 | Past the key's monthly credits |
| 503 | Every place it could go didn't answer |
