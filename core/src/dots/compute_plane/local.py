"""Single-machine compute plane.

This plane is intentionally backend-agnostic. A caller passes an already
constructed BackendRuntime; the plane owns routing policy, not model load
or backend-specific work.
"""

from __future__ import annotations

from ..backends.base import BackendRuntime
from ..generation.base import GenerationRequest
from ..generation.registry import get_generation_strategy
from .base import ComputePlane, GenerationStats, VirtualResources


class LocalComputePlane(ComputePlane):
    """Single-machine plane.

    The caller supplies the backend runtime and resource view. This keeps
    compute-plane routing independent from model formats and backend
    loading.
    """

    def __init__(self, runtime: BackendRuntime, resources: VirtualResources):
        self.runtime = runtime
        self._resources = resources
        self.generation = get_generation_strategy()
        self.n_layers = self.runtime.n_layers
        self._closed = False

    @property
    def virtual_resources(self) -> VirtualResources:
        return self._resources

    async def reset_conversation(self, conv_id: str) -> None:
        self.runtime.reset_conversation(conv_id)

    async def shutdown(self) -> None:
        self._closed = True

    async def generate(
        self,
        history: list[dict],
        *,
        max_tokens: int = 256,
        temperature: float = 0.7,
        on_token=None,
        conv_id: str = "default",
    ) -> tuple[str, GenerationStats]:
        if self._closed:
            raise RuntimeError("plane has been shut down")

        result = await self.generation.generate(
            GenerationRequest(
                runtime=self.runtime,
                conv_id=conv_id,
                history=history,
                max_tokens=max_tokens,
                temperature=temperature,
                on_token=on_token,
            )
        )
        stats = GenerationStats(
            prefill_tokens=int(result.stats.get("prefill_tokens", 0)),
            decode_tokens=int(result.stats.get("decode_tokens", 0)),
            elapsed_ms=float(result.stats.get("elapsed_ms", 0.0)),
            placement_summary=f"local·{self.n_layers}L",
        )
        return result.text, stats
