# Market Data Backend — Detailed Design

> The complete, implementation-ready design for FinAlly's market data subsystem.
> This document consolidates and **fills the gaps between** the three companion
> references so an engineer (or agent) can build the whole subsystem end-to-end:
>
> - `MARKET_INTERFACE.md` — the `MarketSource` ABC + `MarketDataService` facade
> - `MARKET_SIMULATOR.md` — the GBM `SimulatedSource` (default, no API key)
> - `MASSIVE_API.md` — the `MassiveClient` REST wrapper (optional real data)
>
> Spec authority: `PLAN.md §6` (Market Data), `§8` (API), `§10` (Frontend
> consumption), `§12` (Testing). Where this doc and a companion overlap, the
> companion is authoritative for its own component; this doc is authoritative for
> how the pieces **fit together** (cache, history buffer, driver loop, SSE route,
> lifecycle, config).

---

## 0. What this document adds

The companion docs specify the two sources and the interface shape, but several
load-bearing components are *referenced* without being *defined*. This document
provides the missing concrete code and the assembly:

| Component | Status before | Defined here |
|-----------|---------------|--------------|
| `PriceCache` (latest/previous/reference + change detection + snapshot) | referenced only | §4 — full impl |
| `HistoryBuffer` (bounded per-ticker ring buffer) | referenced only | §5 — full impl |
| `MassiveSource` **adapter** (slow internal poll → fast cache reads) | sketch only | §7 — full impl |
| SSE route + heartbeat + disconnect handling | one-liner | §8 — full impl |
| REST routes (`/history`, `/watchlist`) wiring to the service | partial | §8 |
| App lifecycle / dependency wiring (FastAPI lifespan) | not covered | §9 |
| Config & env resolution | scattered | §3 |
| End-to-end data-flow walkthroughs | not covered | §2, §10 |
| Consolidated test plan with fixtures | per-doc | §11 |

---

## 1. Architecture at a glance

```
                         ┌──────────────────────────────────────────────┐
                         │                FastAPI app                     │
                         │                                                │
  EventSource  ────────▶ │  GET /api/stream/prices  ──┐                   │
  (browser)              │  GET /api/prices/{t}/history│                  │
                         │  GET /api/watchlist         │  read            │
  fetch()      ────────▶ │  POST /api/watchlist ───────┼──────┐          │
                         │                             ▼      ▼          │
                         │              ┌────────────────────────────┐    │
                         │              │     MarketDataService       │    │
                         │              │  (facade — single owner)    │    │
                         │              │                             │    │
                         │              │  • driver loop (1 task) ────┼──┐ │
                         │              │  • PriceCache               │  │ │
                         │              │  • HistoryBuffer            │  │ │
                         │              │  • SSE subscriber fan-out   │  │ │
                         │              └──────────────┬──────────────┘  │ │
                         │                             │ fetch_prices()  │ │
                         │                             ▼                 │ │
                         │              ┌────────────────────────────┐   │ │
                         │              │   MarketSource (one of)     │◀──┘ │
                         │              │  • SimulatedSource (GBM)    │     │
                         │              │  • MassiveSource (REST)     │     │
                         │              └────────────────────────────┘     │
                         └────────────────────────────────────────────────┘
```

**Invariants** (hold for both sources):

1. **One writer.** Exactly one asyncio task (the driver loop in the facade) mutates
   the cache and history. No locks needed for in-process state.
2. **Source-agnostic above the source.** Routes and the facade never branch on
   "sim vs Massive". The only branch is `build_market_source()` at startup (§3).
3. **The stream never dies.** A source error degrades to stale-but-present data;
   it never propagates to a connected client.
4. **~500 ms client latency regardless of source.** The driver loop ticks every
   `tick_interval` (0.5 s). Massive's slow 15 s REST poll happens *inside* the
   source; the driver loop just reads the freshest cached snapshot (§7).

---

## 2. End-to-end data flow

### 2.1 A price tick (simulator path)

```
driver loop (every 0.5s)
  └─ tickers = get_watched()                      # from watchlist table
  └─ prices  = await source.fetch_prices(tickers) # GBM advances one step
  └─ for ticker, price in prices:
        update = cache.set(ticker, price)         # None if unchanged
        if update:
            history.append(ticker, ts, price)
            batch.append(update)
  └─ if batch: broadcast(batch)                   # push to every SSE queue
```

### 2.2 A price tick (Massive path)

```
Massive internal poll task (every 15s)            # lives inside MassiveSource
  └─ snapshot = await client.latest_prices(watched)
  └─ self._latest = snapshot                       # atomic dict swap

driver loop (every 0.5s)                           # same loop as simulator
  └─ prices = await source.fetch_prices(tickers)   # returns self._latest copy
  └─ ...identical cache/history/broadcast as above
```

The cache's change-detection means that between Massive polls (when `_latest`
is unchanged) `fetch_prices` returns the same values, `cache.set` returns `None`,
and **no redundant SSE events** are emitted. The stream is naturally quiet until
real data moves — exactly the desired behavior.

### 2.3 A new SSE client connects

```
GET /api/stream/prices
  └─ service.subscribe() yields:
       1st: cache.snapshot_updates()   # full snapshot — every known ticker
       then: await queue.get()         # change-only batches forever
  └─ route serializes each batch as `data: [...]\n\n`
  └─ a parallel 15s timer writes `: keep-alive\n\n` heartbeat comments
```

### 2.4 First click on a ticker (chart backfill)

```
GET /api/prices/AAPL/history
  └─ service.get_history("AAPL")
       └─ local = history.points("AAPL")     # accumulated live points
       └─ if local: return local
       └─ else: return await source.history("AAPL")   # cold buffer → source backfill
  └─ route returns {"ticker": "AAPL", "points": [{"t":..., "p":...}, ...]}
```

---

## 3. Configuration & source selection

All market-data config is resolved once, at startup, into a small frozen object.
This keeps `os.getenv` out of the hot path and makes the wiring testable.

```python
# backend/app/market/config.py
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketConfig:
    massive_api_key: str = ""            # empty => simulator
    massive_base_url: str = "https://api.polygon.io"
    poll_interval: float = 15.0          # Massive REST poll cadence (free tier safe)
    tick_interval: float = 0.5           # driver-loop cadence (SSE latency)
    history_max_points: int = 3600       # ~30 min @ 0.5s per ticker
    heartbeat_interval: float = 15.0     # SSE keep-alive comment cadence
    sim_seed: int | None = None          # deterministic simulator for tests

    @property
    def use_massive(self) -> bool:
        return bool(self.massive_api_key.strip())

    @classmethod
    def from_env(cls) -> "MarketConfig":
        return cls(
            massive_api_key=os.getenv("MASSIVE_API_KEY", "").strip(),
            massive_base_url=os.getenv("MASSIVE_BASE_URL", "https://api.polygon.io"),
            poll_interval=float(os.getenv("MASSIVE_POLL_INTERVAL", "15")),
        )
```

```python
# backend/app/market/__init__.py
from __future__ import annotations

from .config import MarketConfig
from .base import MarketSource, SymbolError
from .service import MarketDataService
from .types import PriceUpdate, PricePoint, Direction


def build_market_source(config: MarketConfig) -> MarketSource:
    """The ONE branch point: real data iff a Massive key is present."""
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
```

> **Selection rule (PLAN.md §5, §6):** `MASSIVE_API_KEY` set & non-empty →
> `MassiveSource`; otherwise `SimulatedSource`. Nothing else in the codebase
> inspects this variable.

---

## 4. `PriceCache` — latest / previous / reference + change detection

This is the in-memory source of truth for "current state". It owns three values
per ticker and the change-detection logic that keeps SSE payloads minimal. It is
referenced everywhere in `MARKET_INTERFACE.md` but never fully written out; here
it is.

```python
# backend/app/market/cache.py
from __future__ import annotations
import time
from dataclasses import dataclass

from .types import Direction, PriceUpdate


@dataclass(slots=True)
class _Entry:
    price: float          # latest price
    previous: float       # prior tick (drives green/red flash)
    reference: float      # first price seen this session ("Chg %" baseline)
    ts: float             # server timestamp of latest price


class PriceCache:
    """In-memory latest/previous/reference price store with change detection.

    Mutated only by the facade's single driver loop, so no locking is required.
    Reads (snapshot, as_dict) are plain dict reads and safe to call from request
    handlers because Python dict access is atomic w.r.t. the event loop.
    """

    def __init__(self) -> None:
        self._data: dict[str, _Entry] = {}

    # --- write path (driver loop only) -------------------------------------

    def set(self, ticker: str, price: float, *, reference: float | None = None
            ) -> PriceUpdate | None:
        """Record a new price. Returns a PriceUpdate, or None if unchanged.

        Returning None on an unchanged price IS the change-detection mechanism
        (PLAN.md §6): the driver loop only broadcasts non-None results.
        """
        ticker = ticker.upper()
        now = time.time()
        entry = self._data.get(ticker)

        if entry is None:
            # First observation → reference is this price (or an injected baseline,
            # e.g. Massive previous_close). previous == price so the first flash is flat.
            ref = reference if reference is not None else price
            self._data[ticker] = _Entry(price=price, previous=price,
                                        reference=ref, ts=now)
            return PriceUpdate(ticker, price, price, now, Direction.FLAT)

        if price == entry.price:
            return None  # unchanged → no event

        prev = entry.price
        entry.previous = prev
        entry.price = price
        entry.ts = now
        direction = Direction.UP if price > prev else Direction.DOWN
        return PriceUpdate(ticker, price, prev, now, direction)

    def seed_reference(self, ticker: str, reference: float) -> None:
        """Pre-set a reference baseline before the first tick (Massive previous_close).

        No-op if the ticker is already known (reference is immutable once set).
        """
        ticker = ticker.upper()
        if ticker not in self._data:
            # Park the reference; first real set() will still create the entry, so
            # we create a provisional entry whose price will be overwritten on tick.
            self._data[ticker] = _Entry(price=reference, previous=reference,
                                        reference=reference, ts=time.time())

    # --- read path (request handlers + snapshot) ---------------------------

    def get(self, ticker: str) -> _Entry | None:
        return self._data.get(ticker.upper())

    def change_pct(self, ticker: str) -> float | None:
        """(current - reference) / reference  — the watchlist 'Chg %' (PLAN.md §6)."""
        e = self._data.get(ticker.upper())
        if e is None or e.reference == 0:
            return None
        return (e.price - e.reference) / e.reference

    def snapshot_updates(self) -> list[PriceUpdate]:
        """Full snapshot as PriceUpdate list — first SSE event on every (re)connect."""
        return [
            PriceUpdate(t, e.price, e.previous, e.ts,
                        Direction.UP if e.price > e.previous
                        else Direction.DOWN if e.price < e.previous
                        else Direction.FLAT)
            for t, e in self._data.items()
        ]

    def as_dict(self) -> dict[str, dict]:
        """Plain-dict view for GET /api/watchlist initial paint (PLAN.md §8)."""
        return {
            t: {
                "ticker": t,
                "price": round(e.price, 4),
                "previous_price": round(e.previous, 4),
                "reference": round(e.reference, 4),
                "change_pct": round((e.price - e.reference) / e.reference, 6)
                              if e.reference else None,
                "timestamp": e.ts,
            }
            for t, e in self._data.items()
        }
```

**Key decisions**

- **Equality-based change detection** (`price == entry.price`). Prices are rounded
  by the source before they reach the cache (simulator rounds to 2 dp; Massive
  returns exchange-rounded values), so float equality is stable in practice. If a
  source ever emits unrounded floats, round at `set()` entry.
- **Reference is immutable once set.** It is the session baseline; resetting it
  would make "Chg %" jump. It is intentionally lost on restart (PLAN.md §6).
- **`seed_reference`** lets the Massive path inject `previous_close()` as a more
  meaningful baseline *before* the first live tick. The simulator never calls it
  (its seed price is the natural reference).

---

## 5. `HistoryBuffer` — bounded rolling history for the main chart

A per-ticker ring buffer of recent `(timestamp, price)` points. In-memory only,
not persisted (PLAN.md §6). Backfills `GET /api/prices/{ticker}/history` so the
detailed chart renders immediately instead of starting blank.

```python
# backend/app/market/history.py
from __future__ import annotations
from collections import defaultdict, deque


class HistoryBuffer:
    """Bounded per-ticker ring buffer of (timestamp, price) points.

    max_points caps memory: at 0.5s ticks, 3600 points ≈ 30 minutes per ticker.
    deque(maxlen=...) drops the oldest point automatically on overflow — O(1).
    """

    def __init__(self, max_points: int = 3600) -> None:
        self._max = max_points
        self._buf: dict[str, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=max_points)
        )

    def append(self, ticker: str, timestamp: float, price: float) -> None:
        self._buf[ticker.upper()].append((timestamp, price))

    def points(self, ticker: str) -> list[tuple[float, float]]:
        """Oldest-first list of (t, p). Empty if nothing buffered yet."""
        return list(self._buf.get(ticker.upper(), ()))

    def has(self, ticker: str) -> bool:
        return bool(self._buf.get(ticker.upper()))

    def prime(self, ticker: str, points: list[tuple[float, float]]) -> None:
        """Seed the buffer with backfill points (e.g. Massive aggregates) so the
        first chart render is rich even before live ticks accumulate."""
        dq = self._buf[ticker.upper()]
        for t, p in points[-self._max:]:
            dq.append((t, p))
```

**Why `deque(maxlen)`?** O(1) append and automatic eviction of the oldest point —
no manual trimming, no unbounded growth. A 250-ticker watchlist at 3600 points ×
~32 bytes/tuple ≈ a few MB; comfortably in budget.

---

## 6. The simulator source (summary + the one missing constructor arg)

`MARKET_SIMULATOR.md §6` is authoritative for the GBM math, correlation, events,
and synthetic backfill. The only adjustment for clean wiring is making
`tick_interval` injectable (so `MarketConfig` drives it) instead of a class
constant:

```python
# backend/app/market/sim_source.py  (delta vs MARKET_SIMULATOR.md §6)
class SimulatedSource(MarketSource):
    def __init__(self, seed: int | None = None, tick_interval: float = 0.5) -> None:
        self.tick_interval = tick_interval          # instance attr, config-driven
        self._rng = random.Random(seed)
        self._params: dict[str, TickerParams] = dict(SEED)
        self._price: dict[str, float] = {t: p.seed_price for t, p in SEED.items()}
```

Everything else — `start`/`stop`/`fetch_prices`/`register_ticker`/`history`,
`VOL_SCALE`, correlation, events — is exactly as written in `MARKET_SIMULATOR.md`.
For `history`, prefer **buffer-first** (return `[]`, let the facade's
`HistoryBuffer` fill from the stream); enable the optional `synth_history`
backward-walk (§7 of that doc) only if a guaranteed non-blank first render of a
*freshly added* ticker is desired.

---

## 7. `MassiveSource` — the adapter that makes REST look like a tick source

`MASSIVE_API.md §5` gives the low-level `MassiveClient` (HTTP calls + parsing).
What's missing is the **adapter** that implements the `MarketSource` interface on
top of it: an internal slow poll task that refreshes a cached snapshot, while
`fetch_prices` (called every 0.5 s by the driver loop) returns that cache
instantly. This is what keeps SSE at ~500 ms while staying under 5 calls/min.

```python
# backend/app/market/massive_source.py
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

    Design: a private background task polls the unified snapshot every
    `poll_interval` seconds and stores the result in `self._latest`. The driver
    loop's fast `fetch_prices` calls just read `self._latest` — no network I/O on
    the hot path — so client SSE latency stays ~tick_interval while API usage
    stays at ~ (60 / poll_interval) calls/min.
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
        self._latest: dict[str, float] = {}        # ticker -> price (atomic swap)
        self._watched: list[str] = []              # set by driver loop each tick
        self._known: set[str] = set()              # validated symbols
        self._poll_task: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------------

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

    # --- hot path (driver loop, every tick_interval) -----------------------

    async def fetch_prices(self, tickers: Iterable[str]) -> dict[str, float]:
        """Return the most recent polled snapshot. Never does network I/O here;
        never raises. Records the watched set so the poll loop knows what to fetch."""
        self._watched = [t.upper() for t in tickers]
        # Return only watched tickers from the freshest snapshot.
        return {t: self._latest[t] for t in self._watched if t in self._latest}

    # --- slow path (private poll task, every poll_interval) ----------------

    async def _poll_loop(self) -> None:
        while True:
            started = time.time()
            watched = self._watched or []
            if watched:
                try:
                    prices = await self._client.latest_prices(watched)
                    if prices:                       # swap in fresh snapshot
                        self._latest = {**self._latest, **prices}
                except MassiveError as exc:
                    # auth/plan problem: log, keep last cache (don't crash loop)
                    log.error("Massive auth/plan error: %s", exc)
                except (httpx.HTTPError, httpx.TimeoutException) as exc:
                    log.warning("Massive poll failed, keeping last cache: %s", exc)
            # steady cadence regardless of request duration
            await asyncio.sleep(max(0.0, self._poll_interval - (time.time() - started)))

    # --- symbol validation -------------------------------------------------

    async def register_ticker(self, ticker: str) -> str:
        """Validate a symbol by confirming Massive returns a usable price for it.

        Unlike the simulator (which accepts anything), the real source must reject
        symbols the API doesn't recognize — surfaced as an add-watchlist error
        (PLAN.md §6, §8)."""
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
        # prime caches so the new ticker streams on the next driver tick
        self._latest[t] = prices[t]
        self._known.add(t)
        return t

    # --- history backfill --------------------------------------------------

    async def history(self, ticker: str) -> list[tuple[float, float]]:
        """Recent intraday bars for the main chart. Best-effort: [] on failure."""
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
        """Prior trading day's close — a meaningful 'Chg %' baseline (PLAN.md §6).
        The facade may call this once per ticker to seed the cache reference."""
        try:
            return await self._client.previous_close(ticker)
        except (MassiveError, httpx.HTTPError, httpx.TimeoutException):
            return None
```

**Why the two-loop split matters**

| Loop | Cadence | Does | Lives in |
|------|---------|------|----------|
| Driver loop | 0.5 s | reads `self._latest`, change-detects, broadcasts | `MarketDataService` |
| Poll loop | 15 s | one REST call for all watched tickers, swaps `self._latest` | `MassiveSource` |

Between polls the snapshot is identical, so change-detection emits nothing — the
SSE stream is correctly quiet until real prices move, with no wasted bandwidth.

**`reference_price` hook (optional polish):** `MarketDataService.add_ticker` /
startup can call `source.reference_price(t)` and `cache.seed_reference(t, ref)` so
"Chg %" is measured against the real prior close rather than the first observed
tick. The simulator has no such method; the facade guards with `hasattr` or an
optional protocol method that defaults to `None` (see §9).

---

## 8. API layer — SSE, history, watchlist

### 8.1 SSE stream with heartbeat and clean disconnect

The route owns two concerns the facade does not: serializing to the SSE wire
format and emitting a periodic heartbeat comment so idle-but-alive connections
aren't mistaken for dead ones (PLAN.md §6).

```python
# backend/app/api/stream.py
from __future__ import annotations
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


def _sse(data: str, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {data}\n\n"


@router.get("/api/stream/prices")
async def stream_prices(request: Request):
    market = request.app.state.market           # MarketDataService
    heartbeat = request.app.state.market_config.heartbeat_interval

    async def gen():
        updates_iter = market.subscribe()       # async generator
        last_beat = asyncio.get_event_loop().time()
        try:
            # subscribe() yields the full snapshot first, then change batches.
            async for batch in _merge_with_heartbeat(updates_iter, heartbeat):
                if await request.is_disconnected():
                    break
                if batch is None:                # heartbeat tick
                    yield ": keep-alive\n\n"
                    continue
                payload = json.dumps([u.to_event() for u in batch])
                yield _sse(payload)
        finally:
            await updates_iter.aclose()          # remove this subscriber's queue

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",           # disable proxy buffering
        },
    )


async def _merge_with_heartbeat(updates, interval: float):
    """Yield update batches as they arrive; yield None every `interval` seconds
    of silence so the route can emit a keep-alive comment."""
    pending = asyncio.ensure_future(updates.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if pending in done:
                try:
                    yield pending.result()
                except StopAsyncIteration:
                    return
                pending = asyncio.ensure_future(updates.__anext__())
            else:
                yield None                       # timeout → heartbeat
    finally:
        pending.cancel()
```

> **Note on libraries.** `MARKET_INTERFACE.md §7` sketched `EventSourceResponse`
> from `sse-starlette`. Either approach works; the plain `StreamingResponse`
> above keeps dependencies minimal and gives explicit control over the heartbeat
> and disconnect path. Pick one and stay consistent. If `sse-starlette` is
> already a dependency, its `EventSourceResponse(ping=15)` handles the heartbeat
> for you and you can drop `_merge_with_heartbeat`.

### 8.2 History backfill endpoint

```python
# backend/app/api/prices.py
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/prices/{ticker}/history")
async def price_history(ticker: str, request: Request):
    market = request.app.state.market
    points = await market.get_history(ticker)
    return {
        "ticker": ticker.upper(),
        "points": [{"t": t, "p": round(p, 4)} for t, p in points],
    }
```

### 8.3 Watchlist add (source-agnostic validation)

```python
# backend/app/api/watchlist.py  (market-data-relevant parts)
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..market import SymbolError

router = APIRouter()


class AddTicker(BaseModel):
    ticker: str


@router.post("/api/watchlist")
async def add_watchlist(body: AddTicker, request: Request):
    market = request.app.state.market
    try:
        canonical = await market.add_ticker(body.ticker)   # raises SymbolError if invalid
    except SymbolError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # persist `canonical` to the watchlist table (db layer, omitted here)
    request.app.state.db.add_watchlist_ticker(canonical)
    return {"ticker": canonical}


@router.get("/api/watchlist")
async def get_watchlist(request: Request):
    market = request.app.state.market
    snapshot = market.current_snapshot()                   # {ticker: {...}}
    tickers = request.app.state.db.list_watchlist()        # ordered tickers
    return {
        "watchlist": [
            snapshot.get(t, {"ticker": t, "price": None, "change_pct": None})
            for t in tickers
        ]
    }
```

The handler is **identical** whether the source is the simulator or Massive — the
only difference is whether `register_ticker` ever raises `SymbolError`. That is
the entire payoff of the abstraction.

---

## 9. App lifecycle & wiring

The service is created once at startup, started (which starts the source + driver
loop), and stopped on shutdown. FastAPI's `lifespan` is the right hook.

```python
# backend/app/main.py  (market-data wiring; db/llm wiring omitted)
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .market import MarketConfig, MarketDataService, build_market_source
from .api import stream, prices, watchlist


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = MarketConfig.from_env()
    app.state.market_config = config

    db = app.state.db                       # initialized elsewhere (lazy init, PLAN.md §7)
    source = build_market_source(config)

    market = MarketDataService(
        source=source,
        get_watched_tickers=db.list_watchlist,    # callable -> list[str]
        history_max_points=config.history_max_points,
    )
    app.state.market = market
    await market.start()                    # starts source + driver loop
    try:
        yield
    finally:
        await market.stop()                 # cancels loop, closes HTTP client


app = FastAPI(lifespan=lifespan)
app.include_router(stream.router)
app.include_router(prices.router)
app.include_router(watchlist.router)
# ... static file mount for the Next.js export (PLAN.md §3)
```

### Facade additions for wiring

`MARKET_INTERFACE.md §5` defined `MarketDataService` with a fixed history size and
no reference-seeding. The production constructor takes `history_max_points` and
optionally seeds references on the Massive path:

```python
# backend/app/market/service.py  (delta vs MARKET_INTERFACE.md §5)
class MarketDataService:
    def __init__(self, source, get_watched_tickers, history_max_points=3600):
        self._source = source
        self._get_watched = get_watched_tickers
        self._cache = PriceCache()
        self._history = HistoryBuffer(max_points=history_max_points)
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    async def add_ticker(self, ticker: str) -> str:
        canonical = await self._source.register_ticker(ticker)   # may raise SymbolError
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
```

The driver loop, `subscribe`, `_broadcast`, and `get_history` are exactly as in
`MARKET_INTERFACE.md §5`. One robustness tweak to `subscribe` — bound the
per-subscriber queue so a slow client can't grow memory without limit, dropping
the oldest batch on overflow (the next snapshot/tick self-heals state):

```python
    async def subscribe(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        try:
            yield self._cache.snapshot_updates()   # full snapshot on connect
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)

    def _broadcast(self, updates: list[PriceUpdate]) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(updates)
            except asyncio.QueueFull:
                _ = q.get_nowait()                 # drop oldest, keep newest
                q.put_nowait(updates)
```

---

## 10. Worked examples

### 10.1 SSE wire output (what the browser receives)

On connect (full snapshot, abbreviated to 2 tickers):

```
data: [{"ticker":"AAPL","price":190.12,"previous_price":190.12,"timestamp":1749250000.0,"direction":"flat"},{"ticker":"TSLA","price":250.40,"previous_price":250.40,"timestamp":1749250000.0,"direction":"flat"}]

```

A subsequent change-only tick (only AAPL moved):

```
data: [{"ticker":"AAPL","price":190.18,"previous_price":190.12,"timestamp":1749250000.5,"direction":"up"}]

```

A 15 s idle heartbeat:

```
: keep-alive

```

### 10.2 `GET /api/prices/AAPL/history`

```json
{
  "ticker": "AAPL",
  "points": [
    {"t": 1749249700.0, "p": 190.02},
    {"t": 1749249700.5, "p": 190.05},
    {"t": 1749249701.0, "p": 190.04}
  ]
}
```

### 10.3 `GET /api/watchlist` initial paint

```json
{
  "watchlist": [
    {"ticker": "AAPL", "price": 190.18, "previous_price": 190.12,
     "reference": 190.00, "change_pct": 0.000947, "timestamp": 1749250000.5},
    {"ticker": "GOOGL", "price": 175.30, "previous_price": 175.31,
     "reference": 175.00, "change_pct": 0.001714, "timestamp": 1749250000.5}
  ]
}
```

### 10.4 Adding an invalid symbol on the Massive path

```
POST /api/watchlist  {"ticker": "NOTREAL"}
→ 422  {"detail": "Unknown or unsupported symbol: NOTREAL"}
```

On the simulator path the same request succeeds (`synth_params` invents a seed),
returning `{"ticker": "NOTREAL"}` and streaming immediately — the documented
"accepts any ticker" behavior (PLAN.md §6).

---

## 11. Test plan (consolidated, PLAN.md §12)

### 11.1 Unit — `PriceCache` (§4)

- `set` on first sight → `PriceUpdate(direction=flat)`, reference == price.
- `set` same price → returns `None` (change detection).
- `set` higher/lower → direction up/down, `previous_price` == prior value.
- `change_pct` == `(price - reference) / reference`; `None` when ref is 0/unknown.
- `seed_reference` sets baseline before first tick; immutable once a real entry
  exists.
- `snapshot_updates` returns one entry per known ticker.

### 11.2 Unit — `HistoryBuffer` (§5)

- Appends accumulate oldest-first; length capped at `max_points` (oldest evicted).
- `points` on unknown ticker → `[]`; `prime` seeds and respects the cap.

### 11.3 Unit — `SimulatedSource` (MARKET_SIMULATOR.md §9)

- Determinism: two `SimulatedSource(seed=42)` produce identical sequences.
- GBM validity at `VOL_SCALE=1`: prices stay positive; return mean/std within
  tolerance of `μ·Δt` / `σ·√Δt`.
- `register_ticker("ZZZZ")` succeeds; next `fetch_prices` yields a positive price.
- Same-sector tickers' returns positively correlated over a long run.

### 11.4 Unit — `MassiveSource` / `MassiveClient` (§7, MASSIVE_API.md)

Use `httpx.MockTransport` with recorded JSON fixtures (snapshot, aggregates,
prev-day, 429, NOT_AUTHORIZED):

- `latest_prices` parses `last_trade.price`, falls back to `session.close`, then
  quote mid (`_extract_price`).
- A `429` / network error in the poll loop does **not** raise out and keeps the
  last `self._latest` (stale-but-present).
- `register_ticker` raises `SymbolError` when the symbol is absent from the
  snapshot response or on `NOT_AUTHORIZED`.
- `fetch_prices` does zero network I/O (assert the mock transport sees no call
  during a `fetch_prices` invocation — only the poll loop calls out).
- `history` maps bars to ascending `(t/1000, c)` tuples; `[]` on error.

### 11.5 Unit — `MarketDataService` facade (source-agnostic, fake source)

A trivial `FakeSource` returning scripted dicts:

- First `subscribe()` yield is the full snapshot; later yields are change-only.
- Driver loop broadcasts only on change; unchanged ticks produce no events.
- Reference price set once, survives subsequent ticks.
- History buffer fills from the stream; `get_history` falls back to
  `source.history()` when the buffer is cold.
- `_maybe_seed_reference` calls `reference_price` only when the source defines it.
- Subscriber queue overflow drops oldest, never raises.

### 11.6 Interface conformance (the shared contract suite)

Run the **same** parametrized suite against `SimulatedSource` and a fixture-backed
`MassiveSource` (MARKET_INTERFACE.md §8):

- `fetch_prices` returns floats for known tickers and never raises on transient
  error.
- `register_ticker` returns an upper-cased canonical symbol; sim always succeeds,
  Massive raises `SymbolError` on the unknown-symbol fixture.
- `history` returns ascending `(t, p)` tuples or `[]`.

### 11.7 E2E (test/, `LLM_MOCK=true`, simulator source)

- Fresh start: default 10 tickers stream; prices flash; sparklines fill.
- Add/remove a ticker via `/api/watchlist`; new ticker appears in the stream.
- Select a ticker: `/api/prices/{t}/history` returns points; chart is non-blank.
- SSE resilience: drop the connection, reconnect, assert a full snapshot arrives
  as the first event.

---

## 12. File layout (final)

```
backend/app/market/
├── __init__.py          # MarketConfig, build_market_source(), facade exports   [§3]
├── config.py            # MarketConfig (env resolution)                         [§3]
├── types.py             # PriceUpdate, PricePoint, Direction                    [INTERFACE §3]
├── base.py              # MarketSource ABC, SymbolError                         [INTERFACE §4]
├── cache.py             # PriceCache (latest/previous/reference + change detect)[§4]
├── history.py           # HistoryBuffer (bounded ring buffer)                   [§5]
├── service.py           # MarketDataService facade + driver loop + SSE fan-out  [§9 + INTERFACE §5]
├── sim_params.py        # SEED table + synth_params                             [SIMULATOR §3]
├── sim_source.py        # SimulatedSource (GBM)                                 [SIMULATOR §6, §6 here]
├── massive.py           # MassiveClient (low-level REST wrapper)                [MASSIVE §5]
└── massive_source.py    # MassiveSource adapter (poll loop → cache reads)       [§7]

backend/app/api/
├── stream.py            # GET /api/stream/prices (SSE + heartbeat)              [§8.1]
├── prices.py            # GET /api/prices/{ticker}/history                      [§8.2]
└── watchlist.py         # GET/POST/DELETE /api/watchlist                        [§8.3]
```

---

## 13. Implementation order (suggested)

1. `types.py`, `base.py`, `config.py` — contracts first, no logic.
2. `cache.py` + `history.py` with unit tests (pure, fast to verify).
3. `sim_params.py` + `sim_source.py` with determinism/GBM tests.
4. `service.py` (facade + driver loop) against a `FakeSource`; SSE fan-out tests.
5. `stream.py` / `prices.py` / `watchlist.py` routes; wire `lifespan` in `main.py`.
   Run the app with the simulator — full demo works with **no API key**.
6. `massive.py` + `massive_source.py` last (optional path), behind `httpx`
   fixtures; run the conformance suite against both sources.

This ordering yields a working, demo-able simulator-backed stream after step 5;
the Massive path is purely additive and never on the critical path.
```

