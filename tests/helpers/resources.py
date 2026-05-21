"""Test builders for worker resources and HELLO payloads."""

from __future__ import annotations

from dots.placement.scheduler import ResourceDecl


def resource_decl(
    worker_id: str,
    *,
    account_id: str | None = None,
    ram_gb: float = 16.0,
    disk_gb: float = 100.0,
    tflops: float = 1.0,
    backends: frozenset[str] | set[str] | tuple[str, ...] = frozenset({"mlx"}),
    cluster_id: str = "A",
    cpu_utilization: float = 0.5,
    io_read_gbps: float = 1.0,
    io_write_gbps: float = 0.5,
) -> ResourceDecl:
    """Build a complete ResourceDecl for tests.

    Tests should use this instead of constructing ResourceDecl directly so
    new worker capability fields get one central default.
    """
    return ResourceDecl(
        worker_id=worker_id,
        account_id=account_id or f"acc-{worker_id}",
        ram_gb_free=ram_gb,
        compute_tflops_per_sec=tflops,
        backends=frozenset(backends),
        cluster_id=cluster_id,
        disk_gb_free=disk_gb,
        cpu_utilization=cpu_utilization,
        io_read_gbps=io_read_gbps,
        io_write_gbps=io_write_gbps,
    )
