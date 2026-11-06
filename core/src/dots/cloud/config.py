"""Settings for the cloud, read once from the environment, each with a documented default."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CloudConfig:
    # 8790 is taken by the Mac app's built-in mock cloud.
    port: int = 8800
    host: str = "127.0.0.1"
    # The hub's HTTP API, where pools, Macs, credits and answers come from.
    hub_url: str = "http://127.0.0.1:8080"
    # Sent to the hub when it gates /v1 with a join token.
    hub_token: str = ""
    # When set, every call must carry it as a Bearer token. Unset means open, for a cloud on localhost.
    token: str = ""
    # The name the hub's pool goes by in the apps.
    pool_name: str = "dots pool"
    # The model the pool asks the hub for. The hub serves whatever it is asked for, loading it on the
    # first request, so the cloud names one.
    pool_model: str = "mlx-community/Qwen3.5-0.8B-4bit"
    # The hub's iroh join ticket, which a Mac's worker dials to lend. Read from the hub's home when unset.
    hub_ticket: str = ""
    # Credits per compute unit (1 CU = 1 TFLOP) the hub measured. A credit is worth terms.credit_usd.
    credits_per_cu: float = 1000.0

    @classmethod
    def from_env(cls) -> "CloudConfig":
        env = os.environ
        return cls(
            port=int(env.get("DOTS_CLOUD_PORT", cls.port)),
            host=env.get("DOTS_CLOUD_HOST", cls.host),
            hub_url=env.get("DOTS_CLOUD_HUB", cls.hub_url),
            hub_token=env.get("DOTS_JOIN_TOKEN", cls.hub_token),
            token=env.get("DOTS_CLOUD_TOKEN", cls.token),
            pool_name=env.get("DOTS_POOL_NAME", cls.pool_name),
            pool_model=env.get("DOTS_POOL_MODEL", cls.pool_model),
            hub_ticket=env.get("DOTS_HUB_TICKET", cls.hub_ticket),
            credits_per_cu=float(env.get("DOTS_CREDITS_PER_CU", cls.credits_per_cu)),
        )
