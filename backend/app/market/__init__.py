from __future__ import annotations

from .config import MarketConfig
from .base import MarketSource, SymbolError
from .service import MarketDataService
from .types import PriceUpdate, PricePoint, Direction


def build_market_source(config: MarketConfig) -> MarketSource:
    """The single branch point: real data iff a Massive key is present."""
    if config.use_massive:
        from .massive_source import MassiveSource
        return MassiveSource(
            api_key=config.massive_api_key,
            base_url=config.massive_base_url,
            poll_interval=config.poll_interval,
            tick_interval=config.tick_interval,
        )
    from .sim_source import SimulatedSource
    return SimulatedSource(seed=config.sim_seed, tick_interval=config.tick_interval)


__all__ = [
    "MarketConfig", "MarketSource", "MarketDataService", "SymbolError",
    "PriceUpdate", "PricePoint", "Direction", "build_market_source",
]
