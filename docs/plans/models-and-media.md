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

Testing uses Qwen3.5 0.8B, and Qwen3.5 2B to split.

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

## Next

1. **Their own drafts (MTP).** Qwen3.5/3.6, DeepSeek V4.1 and MiniMax M2.7 ship multi-token-prediction
   layers: the model guesses its next tokens itself, no draft model, which is how the Project Maya fork
   gets 38-42 tok/s across two PCs. mlx-community has MTP builds (Qwen3.8-27B-MTP).
2. **Images in.** Qwen3.5 is natively multimodal; we skip its vision encoder today. The head Mac would
   run the encoder and feed image features in with the text; the rest of the split is unchanged.
3. **Images out, and editing.** Diffusion models don't split by layers well, and one fits on one Mac,
   so the pool sends the whole job to a Mac that has the model (MLX ports such as mflux). Personal
   photos stay private by default, the way Ask keeps personal documents in your own pools: an edit runs
   on your own Mac unless you say otherwise.
4. **A hero run.** The largest model the pooled Macs can hold, measured over real wifi, once a second Mac
   is set up as a worker (`infra/remote-worker.sh`).
