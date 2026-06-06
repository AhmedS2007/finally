# Market Data Interface — Unified Price Source for FinAlly

> The single abstraction the rest of the backend programs against. Downstream code
> (SSE streaming, REST handlers, the frontend) never knows whether prices come from
> the **simulator** or the **Massive API** — it only talks to this interface.
>
> Companion docs: `MASSIVE_API.md` (real source) and `MARKET_SIMULATOR.md` (default
> source). Spec authority: `PLAN.md §6, §8`.

---

## 1. Design goals

1. **One interface, two implementations.** `SimulatedSource` and `MassiveSource`
   conform to the same `MarketSource` ABC. Selection is purely env-driven.
2. **Source-agnostic downstream.** The SSE layer, the price cache, the history buffer,
   and the REST endpoints depend only on the `MarketDataService` facade — swapping
   sources changes nothing above this layer.
3. **In-process, single background task.** Exactly one task writes the shared cache
   (a simulator tick loop or a Massive poll loop). This matches `PLAN.md §6`'s
   "Shared Price Cache" and keeps the door open for multi-user later.
4. **Never crash the stream.** Source errors degrade to stale-but-present data, never
   an exception that propagates to clients.

---

## 2. Selection rule (env-driven)

```
MASSIVE_API_KEY set and non-empty   ->  MassiveSource   (real data, REST polling)
MASSIVE_API_KEY absent or empty     ->  SimulatedSource (GBM simulator)   [default]
```

This is the **only** branch point. It happens once, at service construction.

```python
def build_market_source() -> "MarketSource":
    key = os.getenv("MASSIVE_API_KEY", "").strip()
    if key:
        from .massive_source import MassiveSource
        return MassiveSource(api_key=key)
    from .sim_source import SimulatedSource
    return SimulatedSource()
```

---

## 3. Core data types

```python
# backend/app/market/types.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """One price observation. Emitted by the cache; serialized into SSE events."""
    ticker: str
    price: float
    previous_price: float          # prior tick — drives the green/red flash
    timestamp: float               # epoch seconds (server-stamped)
    direction: Direction

    def to_event(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": round(self.price, 4),
            "previous_price": round(self.previous_price, 4),
            "timestamp": self.timestamp,
            "direction": self.direction.value,
        }


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A single (time, price) sample for the rolling history ring buffer."""
    timestamp: float
    price: float
```

Note `direction` and `previous_price` are about consecutive **ticks**; the
watchlist's "Chg %" instead uses the **reference price** (§6) — a separate concept.

---

## 4. The `MarketSource` interface

A source's only job is to produce the latest prices for a set of tickers, optionally
provide historical bars for backfill, and validate/normalize new symbols. It does
**not** own the cache, the history buffer, or the SSE fan-out — those live in the
`MarketDataService` facade so both sources share identical plumbing.

```python
# backend/app/market/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterable


class SymbolError(Exception):
    """Raised when a ticker cannot be used (surfaced as an add-watchlist error)."""


class MarketSource(ABC):
    """Abstract price source. Either a simulator or the Massive REST client."""

    #: Nominal cadence (seconds) at which the driver loop should pull/produce prices.
    tick_interval: float = 0.5

    @abstractmethod
    async def start(self) -> None:
        """Initialize state (seed prices, open HTTP client, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Release resources (close HTTP client, cancel timers)."""

    @abstractmethod
    async def fetch_prices(self, tickers: Iterable[str]) -> dict[str, float]:
        """Return {ticker: price} for the requested tickers for this tick.

        - Simulator: advances its GBM state one step and returns new prices.
        - Massive: returns the latest snapshot prices (may be a cached poll result).
        Must NOT raise on transient errors — return what it has (possibly empty).
        """

    @abstractmethod
    async def register_ticker(self, ticker: str) -> str:
        """Validate/normalize a new symbol; return the canonical ticker.

        - Simulator: synthesizes a seed price + GBM params, always succeeds
          (uppercased symbol).  (PLAN.md §6 "Unknown tickers".)
        - Massive: verifies the symbol returns data; raises SymbolError if invalid.
        Returns the normalized (upper-cased) ticker on success.
        """

    @abstractmethod
    async def history(self, ticker: str) -> list[tuple[float, float]]:
        """Return [(epoch_seconds, price)] for backfilling the main chart.

        - Simulator: returns its own in-memory recent points (or synthesizes a
          short plausible series from the seed).
        - Massive: pulls recent aggregate bars (close prices).
        May return [] if no history is available yet.
        """
```

### Why `fetch_prices(tickers)` rather than a callback?

The **driver loop** (in the facade) owns cadence and the watched-ticker set. It calls
`fetch_prices` every `tick_interval`. This keeps both sources as *pure producers* and
puts all the shared concerns (caching, change-detection, history, SSE) in one place.
For `MassiveSource`, `fetch_prices` returns the most recent **polled** snapshot — the
source runs its own slower (15 s) poll internally and the fast driver loop just reads
the freshest values, so SSE latency stays ~500 ms while API calls stay ~4/min.

---

## 5. The shared facade: `MarketDataService`

This is what the rest of the backend imports. One instance, created at app startup.

```python
# backend/app/market/service.py
from __future__ import annotations
import asyncio
import time
from typing import AsyncIterator, Callable

from .base import MarketSource, SymbolError
from .cache import PriceCache          # latest + previous + reference per ticker
from .history import HistoryBuffer     # bounded ring buffer per ticker
from .types import PriceUpdate


class MarketDataService:
    def __init__(
        self,
        source: MarketSource,
        get_watched_tickers: Callable[[], list[str]],
    ) -> None:
        self._source = source
        self._get_watched = get_watched_tickers
        self._cache = PriceCache()
        self._history = HistoryBuffer(max_points=3600)   # ~30 min @ 0.5s
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await self._source.start()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._source.stop()

    # --- driver loop (the single writer) -----------------------------------

    async def _run(self) -> None:
        interval = self._source.tick_interval
        while True:
            t0 = time.time()
            tickers = self._get_watched()
            try:
                prices = await self._source.fetch_prices(tickers)
            except Exception:                  # belt-and-braces: never die
                prices = {}
            updates: list[PriceUpdate] = []
            for ticker, price in prices.items():
                update = self._cache.set(ticker, price)   # builds PriceUpdate, sets ref on first sight
                if update is not None:                    # None => unchanged price
                    self._history.append(ticker, update.timestamp, price)
                    updates.append(update)
            if updates:
                self._broadcast(updates)
            await asyncio.sleep(max(0.0, interval - (time.time() - t0)))

    # --- SSE fan-out -------------------------------------------------------

    async def subscribe(self) -> AsyncIterator[list[PriceUpdate]]:
        """Yield batches of changed prices. First yields a full snapshot."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        try:
            yield self._cache.snapshot_updates()    # full snapshot on connect (PLAN.md §6)
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)

    def _broadcast(self, updates: list[PriceUpdate]) -> None:
        for q in self._subscribers:
            q.put_nowait(updates)

    # --- pass-throughs used by REST handlers -------------------------------

    async def add_ticker(self, ticker: str) -> str:
        canonical = await self._source.register_ticker(ticker)  # may raise SymbolError
        # prime the cache so the next tick / snapshot includes it immediately
        return canonical

    async def get_history(self, ticker: str) -> list[tuple[float, float]]:
        local = self._history.points(ticker)
        if local:
            return local
        return await self._source.history(ticker)   # backfill from source if buffer cold

    def current_snapshot(self) -> dict[str, dict]:
        return self._cache.as_dict()                 # for GET /api/watchlist initial paint
```

### What lives where

| Concern | Owner | Notes |
|---------|-------|-------|
| Latest price, previous price, reference price | `PriceCache` | reference set on first observation (PLAN.md §6) |
| Change detection (emit only on change) | `PriceCache.set` returns `None` if unchanged | version/value compare |
| Full snapshot on (re)connect | `MarketDataService.subscribe` first yield | PLAN.md §6, §8 |
| Heartbeat every ~15 s | SSE endpoint (route layer) | comment frame `: keep-alive` |
| Rolling history for main chart | `HistoryBuffer` | in-memory, not persisted (PLAN.md §6) |
| Producing prices | `MarketSource` impl | sim or Massive |

---

## 6. Reference price ("Chg %")

Per `PLAN.md §6`, "Chg %" = `(current - reference) / reference`, where the reference
is the **first price observed** for a ticker this server session (the seed price for
tickers present at boot, the first streamed price for ones added later). The cache
holds it:

```python
class PriceCache:
    def set(self, ticker: str, price: float) -> PriceUpdate | None:
        entry = self._data.get(ticker)
        if entry is None:
            # first sight -> reference = this price
            self._data[ticker] = _Entry(price=price, previous=price,
                                        reference=price, ts=time.time())
            return PriceUpdate(ticker, price, price, time.time(), Direction.FLAT)
        if price == entry.price:
            return None                       # unchanged -> no event (change detection)
        prev = entry.price
        entry.previous, entry.price, entry.ts = prev, price, time.time()
        direction = Direction.UP if price > prev else Direction.DOWN
        return PriceUpdate(ticker, price, prev, entry.ts, direction)
```

The reference is **distinct** from `previous_price` (last tick) and is reset on
restart. On the Massive path it can optionally be seeded from `previous_close()`
(see `MASSIVE_API.md §6`) for a more meaningful baseline.

---

## 7. How the API layer consumes this

```python
# SSE endpoint
@router.get("/api/stream/prices")
async def stream_prices(request: Request):
    async def gen():
        async for updates in market.subscribe():
            yield f"data: {json.dumps([u.to_event() for u in updates])}\n\n"
            # heartbeat handled by a parallel timer emitting ": keep-alive\n\n"
    return EventSourceResponse(gen())

# History backfill
@router.get("/api/prices/{ticker}/history")
async def price_history(ticker: str):
    return {"ticker": ticker.upper(),
            "points": [{"t": t, "p": p} for t, p in await market.get_history(ticker)]}

# Add ticker (validation differs by source, handler is identical)
@router.post("/api/watchlist")
async def add_watchlist(body: AddTicker):
    try:
        canonical = await market.add_ticker(body.ticker)
    except SymbolError as exc:
        raise HTTPException(422, detail=str(exc))
    # ...persist to watchlist table, return
```

The handler code is **identical** regardless of source — exactly the point of the
abstraction.

---

## 8. Testing the interface (PLAN.md §12)

- **Conformance tests** run the *same* test suite against both `SimulatedSource` and a
  `MassiveSource` backed by a stubbed `httpx` transport (recorded JSON fixtures from
  `MASSIVE_API.md`). Both must satisfy the `MarketSource` contract:
  - `fetch_prices` returns floats for known tickers and never raises on transient error.
  - `register_ticker` uppercases & returns canonical; sim always succeeds, Massive
    raises `SymbolError` on an unknown symbol fixture.
  - `history` returns ascending `(t, p)` tuples or `[]`.
- **Facade tests** (source-agnostic, use a trivial fake source): change-detection emits
  only on change; first `subscribe()` yields a full snapshot; reference price is set
  once and survives subsequent ticks; history buffer is bounded.

---

## 9. File layout

```
backend/app/market/
├── __init__.py          # build_market_source(), exports MarketDataService
├── types.py             # PriceUpdate, PricePoint, Direction
├── base.py              # MarketSource ABC, SymbolError
├── cache.py             # PriceCache (latest/previous/reference + change detection)
├── history.py           # HistoryBuffer (bounded ring buffer)
├── service.py           # MarketDataService facade + driver loop + SSE fan-out
├── sim_source.py        # SimulatedSource  (see MARKET_SIMULATOR.md)
└── massive_source.py    # MassiveSource    (wraps MassiveClient, see MASSIVE_API.md)
```
