from __future__ import annotations
import math
import random
import time
from typing import Iterable

from .base import MarketSource
from .sim_params import SEED, TickerParams, synth_params

SECONDS_PER_TRADING_YEAR = 252 * 6.5 * 3600
VOL_SCALE = 8.0


class SimulatedSource(MarketSource):
    """GBM-based price simulator. Default source when no MASSIVE_API_KEY is set."""

    def __init__(self, seed: int | None = None, tick_interval: float = 0.5) -> None:
        self.tick_interval = tick_interval
        self._rng = random.Random(seed)
        self._params: dict[str, TickerParams] = dict(SEED)
        self._price: dict[str, float] = {t: p.seed_price for t, p in SEED.items()}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def register_ticker(self, ticker: str) -> str:
        t = ticker.upper()
        if t not in self._params:
            params = synth_params(t)
            self._params[t] = params
            self._price[t] = params.seed_price
        return t

    async def fetch_prices(self, tickers: Iterable[str]) -> dict[str, float]:
        wanted = [t.upper() for t in tickers]
        for t in wanted:
            if t not in self._params:
                await self.register_ticker(t)

        sectors = {self._params[t].sector for t in wanted}
        shocks = {
            "market": self._rng.gauss(0, 1),
            "sector": {s: self._rng.gauss(0, 1) for s in sectors},
        }
        dt = self.tick_interval / SECONDS_PER_TRADING_YEAR

        out: dict[str, float] = {}
        for t in wanted:
            p = self._params[t]
            z = self._combined_z(shocks, p.sector)
            sigma = p.volatility * VOL_SCALE
            drift_term = (p.drift - 0.5 * sigma ** 2) * dt
            shock_term = sigma * math.sqrt(dt) * z
            new_price = self._price[t] * math.exp(drift_term + shock_term)
            new_price *= self._maybe_event()
            new_price = max(0.01, round(new_price, 2))
            self._price[t] = new_price
            out[t] = new_price
        return out

    async def history(self, ticker: str) -> list[tuple[float, float]]:
        return self.synth_history(ticker)

    def synth_history(self, ticker: str, points: int = 600, step: float = 0.5
                      ) -> list[tuple[float, float]]:
        """Generate a backward GBM walk for an instant non-blank chart."""
        t = ticker.upper()
        if t not in self._params:
            return []
        p = self._params[t]
        sigma = p.volatility * VOL_SCALE
        dt = step / SECONDS_PER_TRADING_YEAR
        price = self._price[t]
        series: list[tuple[float, float]] = []
        now = time.time()
        for i in range(points):
            ts = now - i * step
            series.append((ts, round(price, 2)))
            z = self._rng.gauss(0, 1)
            price = price / math.exp((p.drift - 0.5 * sigma ** 2) * dt
                                     + sigma * math.sqrt(dt) * z)
            price = max(0.01, price)
        return list(reversed(series))

    def _combined_z(self, shocks: dict, sector: str,
                    w_m: float = 0.4, w_s: float = 0.3) -> float:
        z_idio = self._rng.gauss(0, 1)
        return ((w_m ** 0.5) * shocks["market"]
                + (w_s ** 0.5) * shocks["sector"].get(sector, 0.0)
                + ((1 - w_m - w_s) ** 0.5) * z_idio)

    def _maybe_event(self) -> float:
        if self._rng.random() < 0.002:
            mag = self._rng.uniform(0.02, 0.05)
            return 1.0 + (mag if self._rng.random() < 0.5 else -mag)
        return 1.0
