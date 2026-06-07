# Market Data Backend — Summary

> Status: **complete and tested.** This is the as-built summary of FinAlly's market
> data subsystem (`PLAN.md §6/§8`). Design references: `MARKET_DATA_DESIGN.md`,
> `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, `MASSIVE_API.md`. Review:
> `MARKET_DATA_REVIEW.md` (all findings now addressed).

## What it does

Streams live prices for a watchlist over SSE, from one of two interchangeable sources
selected purely by the `MASSIVE_API_KEY` env var:

- **Simulator (default, no key):** geometric Brownian motion with market/sector
  correlation, occasional dramatic 2–5% events, and per-ticker seed prices. Accepts any
  symbol (synthesizes params). Seedable for deterministic tests.
- **Massive/Polygon (optional, with key):** REST polling (unified snapshot) on a slow
  internal loop; the fast driver loop reads the freshest poll result so SSE latency stays
  ~`tick_interval` while API calls stay ~4/min on the free cadence. Validates symbols and
  can seed the "Chg %" baseline from the prior trading day's close.

Both implement the `MarketSource` ABC and are verified by a shared conformance suite.

## Architecture

```
MarketSource (ABC)            sim_source.SimulatedSource | massive_source.MassiveSource
       │ fetch_prices / register_ticker / history
       ▼
MarketDataService (facade, single owner)
  • one driver loop (the only cache writer)        service.py
  • PriceCache  — latest/previous/reference + change detection + snapshot   cache.py
  • HistoryBuffer — bounded per-ticker ring buffer (in-memory, ~30 min)     history.py
  • SSE fan-out — bounded per-subscriber queues, drop-oldest backpressure
       │
       ▼
FastAPI routes (api/)
  GET  /api/stream/prices          full snapshot on connect, change-only after, heartbeat
  GET  /api/prices/{ticker}/history backfills the main chart
  GET  /api/watchlist               initial paint (snapshot + watched tickers)
  POST /api/watchlist               validate/register, add to watchlist
  DELETE /api/watchlist/{ticker}    remove + evict from cache/history
  GET  /api/health
```

## Key behaviors

- **Change detection:** the cache emits a `PriceUpdate` only when a price actually
  changes; the first observation of a ticker is a single `FLAT` event.
- **Reference ("Chg %") price** is distinct from `previous_price` (last tick). It is the
  first price observed this session, or — on the Massive path — the prior close, seeded
  authoritatively so a driver tick can't overwrite it with a momentary price.
- **Resilience:** neither the driver loop nor the Massive poll loop ever crashes on a
  transient/source error; the cache keeps last-known values.
- **Reconnect:** every (re)connect's first SSE event is a full snapshot; a heartbeat
  comment keeps idle connections alive.

## Configuration (env)

`MASSIVE_API_KEY`, `MASSIVE_BASE_URL`, `MASSIVE_POLL_INTERVAL`, `MARKET_TICK_INTERVAL`,
`MARKET_HISTORY_MAX_POINTS`, `MARKET_HEARTBEAT_INTERVAL`, `SIM_SEED` — all read in
`MarketConfig.from_env()`.

## Tests

`backend/tests/` — 125 tests, all passing under `uv run pytest`. Covers the cache,
history buffer, both sources, the facade/driver loop, the Massive client, interface
conformance (both sources), the SSE route generator + heartbeat + teardown, and the
REST routes (`test_api.py`, `test_stream.py`).

## Known integration dependency

Watchlist persistence currently uses an **in-memory** fallback on `app.state.watchlist`
so add/remove affect streaming today. When the SQLite/persistence component lands, it
sets `app.state.db` and transparently takes over (the routes already prefer it). Cache
eviction on ticker removal is wired via `MarketDataService.remove_ticker`.
