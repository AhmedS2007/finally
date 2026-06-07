from __future__ import annotations
import asyncio
import time
from typing import AsyncGenerator, Callable

from .base import MarketSource
from .cache import PriceCache
from .history import HistoryBuffer
from .types import PriceUpdate


class MarketDataService:
    """Facade that owns the driver loop, cache, history buffer, and SSE fan-out."""

    def __init__(
        self,
        source: MarketSource,
        get_watched_tickers: Callable[[], list[str]],
        history_max_points: int = 3600,
    ) -> None:
        self._source = source
        self._get_watched = get_watched_tickers
        self._cache = PriceCache()
        self._history = HistoryBuffer(max_points=history_max_points)
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._source.start()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._source.stop()

    async def _run(self) -> None:
        interval = self._source.tick_interval
        while True:
            t0 = time.time()
            tickers = self._get_watched()
            try:
                prices = await self._source.fetch_prices(tickers)
            except Exception:
                prices = {}
            updates: list[PriceUpdate] = []
            for ticker, price in prices.items():
                update = self._cache.set(ticker, price)
                if update is not None:
                    self._history.append(ticker, update.timestamp, price)
                    updates.append(update)
            if updates:
                self._broadcast(updates)
            await asyncio.sleep(max(0.0, interval - (time.time() - t0)))

    async def subscribe(self) -> AsyncGenerator[list[PriceUpdate], None]:
        """Yield batches of changed prices. First yields a full snapshot."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        try:
            yield self._cache.snapshot_updates()
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)

    def _broadcast(self, updates: list[PriceUpdate]) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(updates)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                q.put_nowait(updates)

    async def add_ticker(self, ticker: str) -> str:
        canonical = await self._source.register_ticker(ticker)
        await self._maybe_seed_reference(canonical)
        return canonical

    async def _maybe_seed_reference(self, ticker: str) -> None:
        """Massive: seed 'Chg %' baseline from prior close. Simulator: no-op."""
        ref_fn = getattr(self._source, "reference_price", None)
        if ref_fn is None:
            return
        ref = await ref_fn(ticker)
        if ref is not None:
            self._cache.seed_reference(ticker, ref)

    def remove_ticker(self, ticker: str) -> None:
        """Evict a ticker from the cache and history (on watchlist removal).

        Without this, a removed ticker would linger in the cache forever and be
        re-emitted in the full snapshot on every reconnect.
        """
        self._cache.remove(ticker)
        self._history.remove(ticker)

    async def get_history(self, ticker: str) -> list[tuple[float, float]]:
        local = self._history.points(ticker)
        if local:
            return local
        return await self._source.history(ticker)

    def current_snapshot(self) -> dict[str, dict]:
        return self._cache.as_dict()
