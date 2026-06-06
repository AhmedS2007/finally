from __future__ import annotations
import asyncio
import logging
import time
from typing import Iterable

import httpx

from .base import MarketSource, SymbolError
from .massive import MassiveClient, MassiveError

log = logging.getLogger("finally.market.massive")


class MassiveSource(MarketSource):
    """MarketSource backed by Massive REST polling.

    An internal background task polls every poll_interval seconds and stores
    results in self._latest. The driver loop's fast fetch_prices reads that
    cache without network I/O, keeping SSE latency at ~tick_interval.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        poll_interval: float = 15.0,
        tick_interval: float = 0.5,
    ) -> None:
        self.tick_interval = tick_interval
        self._poll_interval = poll_interval
        self._client = MassiveClient(api_key=api_key, base_url=base_url)
        self._latest: dict[str, float] = {}
        self._watched: list[str] = []
        self._known: set[str] = set()
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()

    async def fetch_prices(self, tickers: Iterable[str]) -> dict[str, float]:
        """Return most recent polled snapshot — no network I/O."""
        self._watched = [t.upper() for t in tickers]
        return {t: self._latest[t] for t in self._watched if t in self._latest}

    async def _poll_loop(self) -> None:
        while True:
            started = time.time()
            watched = list(self._watched)
            if watched:
                try:
                    prices = await self._client.latest_prices(watched)
                    if prices:
                        self._latest = {**self._latest, **prices}
                except MassiveError as exc:
                    log.error("Massive auth/plan error: %s", exc)
                except (httpx.HTTPError, httpx.TimeoutException) as exc:
                    log.warning("Massive poll failed, keeping last cache: %s", exc)
            await asyncio.sleep(max(0.0, self._poll_interval - (time.time() - started)))

    async def register_ticker(self, ticker: str) -> str:
        t = ticker.upper()
        if t in self._known:
            return t
        try:
            prices = await self._client.latest_prices([t])
        except MassiveError as exc:
            raise SymbolError(str(exc)) from exc
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            raise SymbolError(f"Could not verify {t}: {exc}") from exc
        if t not in prices:
            raise SymbolError(f"Unknown or unsupported symbol: {t}")
        self._latest[t] = prices[t]
        self._known.add(t)
        return t

    async def history(self, ticker: str) -> list[tuple[float, float]]:
        t = ticker.upper()
        to = time.strftime("%Y-%m-%d", time.gmtime())
        frm = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 5 * 86400))
        try:
            return await self._client.history(
                t, multiplier=1, timespan="minute", from_=frm, to=to, limit=5000
            )
        except (MassiveError, httpx.HTTPError, httpx.TimeoutException) as exc:
            log.warning("Massive history fetch failed for %s: %s", t, exc)
            return []

    async def reference_price(self, ticker: str) -> float | None:
        """Prior trading day's close for seeding 'Chg %' baseline."""
        try:
            return await self._client.previous_close(ticker)
        except (MassiveError, httpx.HTTPError, httpx.TimeoutException):
            return None
