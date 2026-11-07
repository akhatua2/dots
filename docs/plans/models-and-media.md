# Models and media

| | |
|---|---|
| **Status** | in-progress |
| **Effort** | L |
| **Complexity** | high |
| **Risk** | medium |
| **Depends on** | [[decode-over-the-network]] |
| **Unlocks** | the newest open models, image input, image generation and editing |
| **Branch** | dev |
| **PR** | none yet |

## Why

People run the newest models. Most of them aren't plain transformers any more: linear attention
(Qwen3.5/3.6/3.8), convolution (LFM2.5), sliding windows and experts (gpt-oss), DeepSeek-style sparse
attention (GLM 5.3, Kimi K2.5). Many can read images, and people want to make and edit images too,
personal photos on their own Mac.

## What runs today

Every architecture on the cloud's ladder runs split across Macs as it runs whole, checked on random
tiny copies of the real configs (`core/tests/backends/test_mlx_architectures.py`): Qwen3.5, Qwen3.6
(MoE), Qwen3.8, LFM2.5 (and its MoE), gpt-oss, MiniMax M2.7, GLM 5.3, Kimi K2.5. The engine finds a
model's parts however mlx-lm nests them (`backends/mlx/layout.py`), gives each layer the mask for the
state it keeps, and follows the model's own choice of array masks. Qwen3.5 2B was also run split over
two real workers.

Not yet: Kimi K3 (its layers share state across blocks of layers, more than the one hidden vector that
crosses between Macs) and DeepSeek V4.1 (mlx-lm itself can't run it). Gemma 4's small models (per-layer
inputs) fail too.

Testing uses Qwen3.5 0.8B, Qwen3.5 2B to split, and Qwen3.5 4B for the draft head.

**Guessing ahead works on them too.** A running state (linear attention, convolution) can't be rolled
back by dropping tokens, so for a pass short enough to be a check those layers go a token at a time and
keep the state after each (a reference, not a copy); a rejected guess puts the state back. Sliding-window
models (gpt-oss) still decode one token at a time: their window is overwritten in place once full. Qwen3.5
2B checked by Qwen3.5 0.8B (`bench_decode.py`, adaptive, up to four guesses):

| Prompts | Round trip | Draft model | Lookup only |
|---|---|---|---|
| edit | 0 ms | 0.85× | 1.44× |
| edit | 25 ms | 2.06× | 1.48× |
| general | 0 ms | 0.93× | 0.97× |
| general | 25 ms | 1.30× | 0.99× |

**Their own drafts (MTP).** Qwen3.5 and its successors were trained with one extra layer that reads the
model's last hidden state and the next token and predicts the token after. It runs on the head Mac, which
already has the embedding, final norm and output layer, from the hidden states the pool just computed: a
guess costs one layer and the output layer, and no network (`backends/mlx/mtp.py`). The weights come from
mlx-community's `*-MTP-*` repos, which hold the head alone (67 MB for Qwen3.5 4B, 239 MB for Qwen3.6 27B),
or out of Qwen's original release, whose norms are stored centred on zero and get +1 as mlx-lm does for
the rest of the model. At prefill the last Mac sends back the prompt's last 32 hidden states instead of
one: on Qwen3.5 0.8B those were as good as the whole prompt (78% of first guesses kept) and none at all
cost five points.

Qwen3.5 4B with its 4-bit head (`bench_decode.py`, adaptive):

| Repetition penalty | Guesses a check | Round trip | Kept | Speedup |
|---|---|---|---|---|
| none | up to 1 | 0 ms | 77% | 1.48× |
| none | up to 2 | 0 ms | 66% | 1.30× |
| none | up to 2 | 25 ms | 66% | 1.31× |
| none | up to 3 | 25 ms | 60% | 1.39× |
| 1.3, the pool's | up to 2 | 0 ms | 67% | 1.45× |
| 1.3, the pool's | up to 2 | 25 ms | 67% | 1.36× |

Unlike a separate draft model it pays on one Mac too, so it is on for every turn. Its guesses avoid
repeats as the big model does; on Qwen3.5 0.8B through the app's worker that took the guesses kept on the
same question from 32% to 41%, and 1.4 to 1.6 tokens a pass. The first guess past each check comes from
the pool's own hidden state and most hold; later ones read the head's own output and fewer do. Up to one
was fastest on one Mac and up to three at 25 ms, so it guesses up to two (`DOTS_DRAFT_TOKENS`).

Outputs match one-token decoding up to rounding: wherever they differed, the two tokens' logits were within
one bf16 step of each other (0.125 or less). Checking several tokens at once sums in a different order, so
such near-ties can go either way; looking guesses up, with no draft at all, shows the same.

**Images in.** Qwen3.5 reads images natively, and mlx-community's 4-bit builds keep the vision encoder
(unquantized, 153 tensors on 0.8B). The head Mac runs it, loaded the first time an image comes
(`backends/mlx/vision.py`, following mlx-vlm's port, which pulls in torch, opencv and an audio stack, too
much for a worker). Each image becomes one row per 32x32 pixels, in the place of its placeholder tokens, and
from there the prompt crosses Macs like any other: split and whole gave the same answer token for token.

Qwen3.5 was trained with 3D positions for image tokens (frame, row, column), where mlx-lm runs one sequence.
On 0.8B, five questions about a screenshot and a photo (the pool's name and models, credits and Macs
lending, which way a road curves and where the sun is, what is in a corner) got the same facts with plain
positions as with 3D ones in mlx-vlm, so the Macs running later layers need nothing new. Bigger models and
tasks that point at places in an image (boxes, coordinates) may need the 3D positions; that would mean
sending them along with the hidden state.

Images are capped at a million pixels (`DOTS_IMAGE_MAX_PIXELS`): a 3200x2000 screenshot was 6229 tokens
whole and took 16 s on 0.8B; at the cap it was 1029 tokens, 2 s, and the same answer. The encoder matched
mlx-vlm's to the bit on the same pixels, and the pixels matched its preprocessing within 1e-7.

**In the app.** Ask takes a picture by the paperclip or by dropping it on the window, shrinks it to the
pool's cap on this Mac (`imageMaxPixels`), and sends it as a JPEG part of the question. A photo is personal,
like a document, so it stays on this Mac and in your own pools, and only goes to a pool whose model reads
images: the cloud marks those on the ladder (`readsImages`). With none, Ask says so and asks first.

**Images out, and editing.** A diffusion model runs every layer many times over one image, so splitting it
would cross the network at every step, and the image models people use fit on one Mac: the hub sends each
job whole to a worker in a pool that draws (its model is the pool's, its runner from `image_tools/runners.toml`), through OpenAI's image API
(`/v1/images/generations`, `/v1/images/edits`). The worker runs mflux in its own uv environment
(`images.py`): mflux brings torch and opencv, which a worker doesn't otherwise need.

The model was picked from the open-weights leaderboards in November 2026. Qwen-Image-2.1 led text to image
on Artificial Analysis (Elo 1,044) with FLUX 3 Image Klein one point behind (1,043); both were among the top
three for editing. Klein is half the size (5.3 GB in 4 bits against 10.6), its license (FLUX Kommunity) is
free for commercial use below $5M a year where Qwen's is research-only, and it edits by instruction from up
to ten reference pictures. It is the default; FLUX.2 klein 4B is the Apache-licensed choice.

On this M1 Pro (16 GB), each picture a fresh run of the tool, model load included (`scripts/bench_images.py`):

| Model | Picture | Steps | Seconds | Peak GB |
|---|---|---|---|---|
| FLUX 3 Image Klein, 4-bit | 512x512 | 16 (its default) | 81 | 3.8 |
| FLUX 3 Image Klein, 4-bit | 512x512 | 8 | 44 | 3.8 |
| FLUX 3 Image Klein, 4-bit | 1024x1024 | 16 | 325 | 4.3 |
| FLUX 3 Image Klein, 4-bit | edit, 1024x1024 | 16 | 744 | 5.7 |
| FLUX 3 Image Klein, 4-bit | edit, 512x512, through the hub | 8 | 221 | |
| FLUX.2 klein 4B, 4-bit | 512x512 | its default | 34 | 4.4 |
| Z-Image Turbo, 4-bit | 512x512 | its default | 68 | 4.6 |

Eight steps drew nearly the same pictures as sixteen, so workers default to 8 (`DOTS_IMAGE_STEPS`). Loading
the model is about 10 s of a picture's time, so keeping it loaded between jobs would save little yet. All
three spelled a sign's words right. Z-Image Turbo can only restyle a picture; Klein follows edit
instructions ("make it a snowy winter evening" kept the road and the mountains). An edit costs about three
times a new picture, because the starting picture's tokens join the image's own.

**Pictures in Ask.** "Draw…", or a photo with "make it…", gets a picture in the conversation instead of
words (`Drawing.wantsPicture`; a question about a picture is still answered in words). It sharpens as the
pool draws it: the worker sends a preview after every step and the hub and cloud stream them as OpenAI's
`partial_image` events. mflux's own step pictures are the noisy work in progress, static until the last
steps, so FLUX 3 Klein runs through dots' own driver (`image_tools/flux3_draw.py`) that decodes what the model
is heading for instead (x - s v, from two steps of its flow): the subject shows from the first step. The
preview decodes cost about a third more time (65 s against 44 s at 512).

A step can take half a minute on a laptop, so the forming picture moves under film grain that thins with each
step, with the steps as the app's dots and the time left on the picture. A photo you edit is changed in any
pool that draws, edited as a one-megapixel copy, and put back into your full-size photo on your Mac
(`Compositing.swift`): only where the edit changed the copy, so the rest keeps its own pixels and definition.

## Next

1. **A hero run.** The largest model the pooled Macs can hold, measured over real wifi, once a second Mac
   is set up as a worker (`infra/remote-worker.sh`).
