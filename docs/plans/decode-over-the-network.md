# Decode over the network

| | |
|---|---|
| **Status** | in-progress |
| **Effort** | M |
| **Complexity** | high |
| **Risk** | medium |
| **Depends on** | [[hybrid-pipeline]], [[resource-scheduling]] |
| **Unlocks** | big models across friends' Macs at a usable speed |
| **Branch** | dev |
| **PR** | none yet |

## Why

When a model's layers span Macs, every token pays a network round trip per hop. Over the internet that
round trip, not the GPUs, sets the speed. Each change here has to be measured, not guessed.

## What is in place

- **Speculative decoding** (`generation/speculative.py`). A draft model on the head Mac proposes `k`
  tokens, and the pool checks them all in one pass. Verification samples the big model at each position
  and keeps a proposal only if it matches, so the output is what the big model would have written. It
  runs only when a turn spans Macs and `DOTS_DRAFT_MODEL` names a draft with the same tokenizer.
  Rejected tokens are trimmed from every cache in the chain (`trim_cache`).
- **Per-pass timing** (`distributed/chain_timing.py`). The head times its own layers and each hop's round
  trip, and the peer reports its compute. The hub logs it on every turn's "done" line, together with how
  many draft tokens were kept.
- **Draft display**. The draft's proposal streams to the app as a `dots` draft event and shows greyed
  out until the pool confirms it.

## Measurements

The simulated two-Mac pool (`scripts/bench_decode.py`): Qwen2.5 1.5B split in two with an injected round
trip, Qwen2.5 0.5B as the draft, four draft tokens.

| Round trip | One token at a time | Speculative | Same output |
|---|---|---|---|
| 0 ms | 1.00× | 0.60× | yes |
| 25 ms | 1.00× | 1.12× | yes |
| 50 ms | 1.00× | 1.35× | yes |
| 100 ms | 1.00× | 1.76× | yes |

At six draft tokens the output drifted, from batched floating-point numerics; four is the default.

Two real workers on one Mac, Qwen2.5 1.5B at temperature 0. The output was identical with and without the
draft, and the draft kept 23 of 68 proposals (2.4 tokens a pass). Per pass, without the draft:

| Part | Time |
|---|---|
| Head, 1 layer plus embeddings | 1.1 ms |
| Peer, 27 layers | 15.4 ms |
| Wire and copies, loopback | 3.7 ms |

The ping between the two is 0.7 ms, so about 3 ms of the wire is the message path (framing, QUIC,
asyncio handoffs). Hidden states already go as raw bytes.

## Next

1. **Keep a model on one Mac when it fits.** Placement already prefers this. Check it holds when a
   second Mac joins mid-session.
2. **Balance the split by time, not memory.** The head holds the embeddings and output layer (150K rows
   for Qwen), so a memory-balanced split gave it 1 layer and the peer 27. The per-pass timing gives
   the numbers to balance on.
3. **Ring instead of star.** Today the head calls each peer in turn and every hidden state comes back
   through it: two network legs per peer. Passing it peer to peer and back to the head once saves a leg
   per extra peer.
4. **Trim the message path.** About 3 ms a hop on loopback. It matters on a LAN and not over the internet.
5. **Measure over a real WAN** with two Macs on different networks before deciding between 2 and 3.
