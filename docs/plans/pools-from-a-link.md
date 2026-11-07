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

## One pool, one model, one hub

Each pool is served by its own hub, which serves that pool's one model. A Mac that lends to a pool connects to
its hub (the ticket comes with the pool in the cloud's sync), and the hub tells it what to run: text layers of
the pool's model, or the pool's image model. A Mac lends to one pool at a time.

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

## Safety

A worker never runs a command it receives. The pool names a model (and, for pictures, its family); each worker
runs it with the runner dots ships for that kind of model, the way it runs text through MLX. A kind it doesn't
know means it doesn't lend to that pool. Remote Login is only for setting a Mac up by hand; everything at
inference time travels over iroh, encrypted, between Macs that hold the pool's ticket.

## Steps

1. The backend reads a link: kind, family where it can tell, size, what Macs need, whether dots runs it.
2. A pool stores its model (and kind); the hub for a pool serves only that model.
3. Workers learn what to run from the pool's hub; runners by kind, no models in code.
4. Start a Pool takes a link; Explore shows what each pool runs and whether this Mac can lend.
5. For people beyond one Wi-Fi: the cloud and the hubs on a public server.

## Not now

Splitting one image model across Macs. Whole pictures per Mac scale the number of pictures, not the size of the
model. When a pool's image model won't fit one Mac, the next step is to put its parts on different Macs (text
encoder, transformer, decoder), which hand over little data once a picture; patch parallelism (DistriFusion,
PipeFusion) is research for later.
