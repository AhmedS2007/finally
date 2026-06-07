from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketConfig:
    massive_api_key: str = ""
    massive_base_url: str = "https://api.polygon.io"
    poll_interval: float = 15.0
    tick_interval: float = 0.5
    history_max_points: int = 3600
    heartbeat_interval: float = 15.0
    sim_seed: int | None = None

    @property
    def use_massive(self) -> bool:
        return bool(self.massive_api_key.strip())

    @classmethod
    def from_env(cls) -> "MarketConfig":
        sim_seed = os.getenv("SIM_SEED")
        return cls(
            massive_api_key=os.getenv("MASSIVE_API_KEY", "").strip(),
            massive_base_url=os.getenv("MASSIVE_BASE_URL", "https://api.polygon.io"),
            poll_interval=float(os.getenv("MASSIVE_POLL_INTERVAL", "15")),
            tick_interval=float(os.getenv("MARKET_TICK_INTERVAL", "0.5")),
            history_max_points=int(os.getenv("MARKET_HISTORY_MAX_POINTS", "3600")),
            heartbeat_interval=float(os.getenv("MARKET_HEARTBEAT_INTERVAL", "15")),
            sim_seed=int(sim_seed) if sim_seed not in (None, "") else None,
        )
