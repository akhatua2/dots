# Pools from a link

| | |
|---|---|
| **Status** | planned |
| **Effort** | L |
| **Complexity** | medium |
| **Risk** | medium |
| **Depends on** | [[models-and-media]] |
| **Unlocks** | anyone running any model in a pool of friends' Macs, text or pictures |
| **Branch** | dev |
| **PR** | none yet |

## Why

Anyone can start a pool, make it public or keep it to people they invite, and choose what it runs. Today a
pool's model comes from a list in the cloud's code, and a Mac's image model from its own settings. A pool
should run whatever its creator links to, and every Mac that joins should learn from the pool what to run.

## How it works for people

Starting a pool, inside the main window like every other flow:

```
Start a Pool
Name          Prism
Who can join  ( ) People I invite   (•) Anyone, in Explore
What it runs  [ huggingface.co/mflux-community/flux2-klein-4b-mflux-q4 ]
              FLUX.2 klein 4B · draws and changes pictures
              Each picture runs on one Mac with about 5 GB free
              Macs on macOS 13.5 or newer can lend to it
                                                    [ Start Prism ]
```

The card under the link is the backend reading the repo. When it can't run the model it says why, and Start
stays off. Popular models to pick from come from the backend too.

In Explore every public pool shows what it runs. Anyone can join and ask it. Lending is offered only when this
Mac can run the pool's model; otherwise "Your Mac can't lend here: it needs macOS 14", and asking still works.
Ask uses pools that answer in words, Draw uses pools that draw.

## One hub, many pools, one model each

There is one hub. Each pool runs exactly one model, the one its creator chose. Every Mac's worker tells the hub
which pool it lends to, and every request names its pool: the hub runs it on that pool's Macs with that pool's
model (text layers split across them, or a picture drawn whole on one), and never on another pool's Macs.

A Mac can lend to several pools, an amount to each: 4 GB to Home and 6 GB to Prism, say. It runs one worker for
each, both connected to the hub, each saying its pool and held to that pool's share, so the pools never compete
for the same memory. The app shows each pool's share beside it, and what this Mac has left to lend.

## What the backend can tell from a link

Read from the repo itself (`HfApi.model_info`, `config.json`, `model_index.json`), checked on the models dots
runs today:

| Kind | Signal | Works for |
|---|---|---|
| Text | `config.json` names its architecture (`model_type`) | every MLX text model; the engine runs the type or doesn't |
| Pictures | `library_name` is `mflux`, with `transformer`, `text_encoder`, `vae` | every mflux build |
| Picture family | the card's `base_model`, whose `model_index.json` names its pipeline class (`Flux2KleinPipeline`) | FLUX.2 klein; not FLUX 3 Klein (its original is gated) nor Z-Image Turbo (names no original) |
| Size | the weights' file sizes | every repo |

So the family of an image model can't always be read. When it can't, the form asks what kind of model it is,
from the kinds dots knows how to run, rather than guessing.

## Who can see what

On the network, everything is encrypted: Macs and the hub talk over iroh (QUIC with TLS 1.3, each side proven by
its key), so no one in between, relays included, can read prompts, answers, pictures or hidden states.

On the Macs, a Mac sees what it computes. The head (the first layers and the output, which turn words into tokens
and back) sees the question and the answer in plain form. A middle Mac sees hidden states, which can in principle
be decoded back into much of the text. A Mac drawing a picture sees the prompt and the picture. The worker doesn't
log or keep any of it, but its owner could read it on purpose. Nothing on a consumer Mac can stop that: containers
run in a Linux VM the owner controls (and without the GPU), the Secure Enclave holds keys but runs no models, and
the hardware that proves a machine can't look (confidential computing) isn't offered to apps on Macs.

The only real guarantee, then, is where things run: what you mark private never reaches another person's Mac.
Every request carries a privacy level, and dots holds to it without falling back:

| Level | Runs on | Who could see it |
|---|---|---|
| Only my Macs | Macs signed into your account, and the hub | only you |
| My pools | your pools' Macs, friends' included | pool members, in principle |
| Anywhere | public pools and frontier models | others |

Your documents, and pictures you ask about, default to Only my Macs; ordinary questions can use your pools. A photo
you change in Draw goes to any pool that draws, public ones included: that is fine for the pictures people edit. When none of your Macs can run something marked Only my Macs, the
app says so and asks, rather than sending it to a friend's Mac. The receipt says the level an answer ran at.

Within the shared levels, dots also keeps the plain-text parts where the asker trusts them:

1. **Your question's head runs on your own Mac** when you lend to the pool, so the other Macs only see hidden
   states. A model that fits on one Mac already works this way: every Mac that can hold it keeps a whole copy, and
   your turn runs on your own copy whenever you lend (only Macs in the pool that runs the model are considered).
   A model split across Macs is laid out again for a question kept in your pools, with your Mac at the front,
   when you lend to the pool and your Mac can hold the head (the layout stays until someone else asks privately;
   ordinary questions don't move it). Checked live with Qwen3.5 2B split across two workers of different accounts.
2. **Private material only goes where you trust**: your documents and photos to your own Mac or pools of people you
   invited, never public pools. Ask and Draw already route this way.
3. **Nothing kept**: workers drop a conversation's working memory after each request (the hub already resets it).
4. **Each Mac's key in the Secure Enclave**, so a Mac can't be impersonated, and the app remembers each Mac's key
   and warns if it changes.
5. **The receipt says who could read it**: which Macs handled the question, and for public pools that they could
   read it.

End-to-end encryption past the hub (the asker sealing the question to the head Mac's key, so the hub only routes)
is possible on top of this; with the head on the asker's own Mac, the hub sees nothing either.

## Safety

A worker never runs a command it receives. The pool names a model (and, for pictures, its family); each worker
runs it with the runner dots ships for that kind of model, the way it runs text through MLX. A kind it doesn't
know means it doesn't lend to that pool. Remote Login is only for setting a Mac up by hand; everything at
inference time travels over iroh, encrypted, between Macs that hold the pool's ticket.

## Steps

1. The backend reads a link: kind, family where it can tell, size, what Macs need, whether dots runs it. Done.
2. A pool stores its model (and kind). Done: the cloud keeps it and says it in each pool's sync.
3. The hub knows each worker's pool and runs each request on its pool's Macs with its pool's model. Done.
4. Lending per pool: an amount to each pool, one worker each. Done: the cloud keeps a share per pool, the app runs a
   worker for each with its pool and memory, and each worker has its own identity at the hub.
5. Workers learn what to run from their pool; runners by kind, no models in code. Done: a pool that draws keeps the original its
   model was made from (its card says); each picture names the pool's model and that original; a Mac draws with the
   runner `image_tools/runners.toml` has for it, or not at all, and never runs a command from a job.
6. Start a Pool takes a link; Explore shows what each pool runs, what it needs, and whether this Mac can lend. Done:
   a pool says its model, kind and size; its page shows what it runs, needs and has in place of the ladder; a Mac
   that can't hold a drawing pool's model joins it to ask, lending nothing.
7. For people beyond one Wi-Fi: the cloud and the hub on a public server. The cloud's part is done: it serves its
   owner and every friend who joins through an invite, each with a token of their own and their own lending,
   badges and spends; invite links name the cloud, and the app joins a friend's cloud from one. `infra/README.md`
   says how to run it beside the hub; deploying it is left to the owner.
8. Privacy levels on every request (Only my Macs, My pools, Anywhere), never falling back to a wider one; your
   question's head on your own Mac within the shared levels (see Who can see what). Levels done: the app routes by
   level and the receipt names it; Only my Macs travels to the hub, which refuses a model held partly on someone
   else's Mac, and draws only on yours. The head on the asker's Mac: done for both kinds of model.

## Not now

Splitting one image model across Macs. Whole pictures per Mac scale the number of pictures, not the size of the
model. When a pool's image model won't fit one Mac, the next step is to put its parts on different Macs (text
encoder, transformer, decoder), which hand over little data once a picture; patch parallelism (DistriFusion,
PipeFusion) is research for later.
