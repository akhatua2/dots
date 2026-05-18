"""Simulate the dots scheduler across several pool sizes and plot the
Pareto frontiers.

Run:
    .venv/bin/pip install -e ".[sim]"
    .venv/bin/python scripts/sim_scheduler.py

Outputs PNGs to scripts/sim_results/. Every plot shows:
  * gray: all enumerated feasible placements
  * blue line: Pareto frontier
  * red star: the placement scheduler.schedule() picks for the configured mode

This is the empirical confirmation that the scheduler hits the frontier.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly with `python scripts/sim_scheduler.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print('matplotlib not installed. Run: .venv/bin/pip install -e ".[sim]"',
          file=sys.stderr)
    sys.exit(1)

from dots.scheduler import (  # noqa: E402  (sys.path needs the manipulation first)
    Placement,
    enumerate_placements,
    non_dominated_placements,
    schedule,
)
from dots.sim import (  # noqa: E402
    LLAMA_70B,
    QWEN_05B,
    QWEN_7B,
    make_multi_cluster_pool,
    make_random_pool,
    make_simple_pool,
    make_unit_graph,
)


OUT_DIR = Path(__file__).resolve().parent / "sim_results"


def plot_scenario(
    title: str,
    candidates: list[Placement],
    frontier: list[Placement],
    chosen: Placement | None,
    filename: str,
) -> None:
    """Render all candidates with the frontier highlighted."""
    fig, ax = plt.subplots(figsize=(10, 6))

    frontier_ids = {id(p) for p in frontier}
    dom_x = [p.cu_paid for p in candidates if id(p) not in frontier_ids]
    dom_y = [p.tps for p in candidates if id(p) not in frontier_ids]
    if dom_x:
        ax.scatter(dom_x, dom_y, c="lightgray", s=22, alpha=0.55,
                   label=f"dominated ({len(dom_x)})")

    frontier_sorted = sorted(frontier, key=lambda p: p.cu_paid)
    ax.plot(
        [p.cu_paid for p in frontier_sorted],
        [p.tps for p in frontier_sorted],
        "b-o", markersize=8, linewidth=2,
        label=f"Pareto frontier ({len(frontier)})",
    )

    if chosen is not None:
        ax.scatter([chosen.cu_paid], [chosen.tps],
                   c="red", s=260, marker="*", zorder=10, label="schedule() pick")

    ax.set_xlabel("CU paid per token (FLOPs to non-self workers)")
    ax.set_ylabel("Tokens / second (estimated)")
    ax.set_title(title)
    ax.legend(loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    OUT_DIR.mkdir(exist_ok=True)
    fig.savefig(OUT_DIR / filename, dpi=110)
    plt.close(fig)


def run(
    label: str,
    units,
    workers,
    latency,
    querier_account: str,
    mode: str = "cheap",
    max_segments: int = 4,
) -> None:
    print(f"\n[{label}]")
    print(f"  pool: {len(workers)} workers across "
          f"{len(set(w.cluster_id for w in workers))} cluster(s)")
    total_ram = sum(u.ram_required_gb for u in units)
    print(f"  model: {len(units)} units, total RAM {total_ram:.2f} GB")

    candidates = list(enumerate_placements(
        units, workers, latency,
        querier_account=querier_account,
        max_segments=max_segments,
    ))
    if not candidates:
        print("  no feasible placements; skipping")
        return

    frontier = non_dominated_placements(candidates)
    chosen = schedule(
        units, workers, latency,
        querier_account=querier_account,
        mode=mode,
        max_segments=max_segments,
    )

    print(f"  candidates: {len(candidates)}  |  frontier: {len(frontier)}"
          f"  |  mode: {mode}")
    if chosen is not None:
        print(f"  chosen: {chosen}")
        # Sanity: the chosen placement must be on the frontier.
        assert chosen in frontier, "scheduler picked a dominated point — bug"

    plot_scenario(
        title=f"{label}\n{len(workers)} workers · mode={mode} · "
              f"{len(units)} units",
        candidates=candidates,
        frontier=frontier,
        chosen=chosen,
        filename=f"{label}.png",
    )
    print(f"  → wrote {OUT_DIR / (label + '.png')}")


def main() -> None:
    # ── Scenario 1: small pool, small model that fits locally.
    workers, lat = make_simple_pool()
    run(
        "1_simple_local_fits",
        make_unit_graph(QWEN_05B, layers_per_block=4),
        workers, lat, querier_account="acc-alice",
        mode="cheap",
    )

    # ── Scenario 2: small pool, 7B forces a spill from carol's 8 GB Mac.
    run(
        "2_simple_7B_carol",
        make_unit_graph(QWEN_7B, layers_per_block=4),
        workers, lat, querier_account="acc-carol",
        mode="cheap",
    )

    # ── Scenario 3: 10-machine multi-cluster pool, mid-sized query.
    workers, lat = make_multi_cluster_pool()
    run(
        "3_multicluster_7B_arpan_cheap",
        make_unit_graph(QWEN_7B, layers_per_block=4),
        workers, lat, querier_account="acc-arpan",
        mode="cheap",
    )

    # ── Scenario 4: same pool, fast mode — should pick a different frontier point.
    run(
        "4_multicluster_7B_arpan_fast",
        make_unit_graph(QWEN_7B, layers_per_block=4),
        workers, lat, querier_account="acc-arpan",
        mode="fast",
    )

    # ── Scenario 5: same pool, balanced mode.
    run(
        "5_multicluster_7B_arpan_balanced",
        make_unit_graph(QWEN_7B, layers_per_block=4),
        workers, lat, querier_account="acc-arpan",
        mode="balanced",
    )

    # ── Scenario 6: a chat-only client querying. carol's 8GB can't fit 70B.
    run(
        "6_simple_70B_carol_forced_pool",
        make_unit_graph(LLAMA_70B, layers_per_block=16),  # 5 blocks
        *make_simple_pool(),
        querier_account="acc-carol",
        mode="balanced", max_segments=3,
    )

    # ── Scenario 7: random 12-worker pool, 70B model, requires multi-worker placement.
    workers, lat = make_random_pool(12, n_clusters=3, seed=7)
    run(
        "7_random12_70B",
        make_unit_graph(LLAMA_70B, layers_per_block=16),
        workers, lat, querier_account=workers[0].account_id,
        mode="balanced", max_segments=5,
    )

    print(f"\n✓ Done. Plots in: {OUT_DIR}")


if __name__ == "__main__":
    main()
