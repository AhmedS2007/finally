from __future__ import annotations
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class TickerParams:
    seed_price: float
    drift: float
    volatility: float
    sector: str


SEED: dict[str, TickerParams] = {
    "AAPL": TickerParams(190.0, 0.08, 0.28, "tech"),
    "GOOGL": TickerParams(175.0, 0.10, 0.30, "tech"),
    "MSFT": TickerParams(420.0, 0.09, 0.26, "tech"),
    "AMZN": TickerParams(185.0, 0.11, 0.34, "tech"),
    "TSLA": TickerParams(250.0, 0.05, 0.55, "auto"),
    "NVDA": TickerParams(120.0, 0.20, 0.50, "tech"),
    "META": TickerParams(480.0, 0.12, 0.36, "tech"),
    "JPM":  TickerParams(200.0, 0.06, 0.22, "finance"),
    "V":    TickerParams(275.0, 0.07, 0.20, "finance"),
    "NFLX": TickerParams(630.0, 0.10, 0.38, "media"),
}


def synth_params(ticker: str) -> TickerParams:
    """Derive stable, deterministic GBM params for any unknown ticker."""
    h = int(hashlib.sha256(ticker.encode()).hexdigest(), 16)
    seed_price = 20.0 + (h % 48000) / 100.0
    volatility = 0.20 + (h % 40) / 100.0
    drift = 0.05 + ((h >> 8) % 15) / 100.0
    return TickerParams(round(seed_price, 2), drift, volatility, sector="other")
