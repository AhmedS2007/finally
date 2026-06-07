# Massive API (formerly Polygon.io) — Reference for FinAlly

> Research notes + code examples for the **optional** real-data source. FinAlly uses
> this only when `MASSIVE_API_KEY` is set; otherwise the built-in simulator runs
> (see `MARKET_SIMULATOR.md`). The unified abstraction that selects between them is in
> `MARKET_INTERFACE.md`.

---

## 1. What Massive is

**Massive** is the new name for **Polygon.io** (rebrand effective **October 30, 2025**).
It provides US stock, options, crypto, forex, and index market data via **REST**,
**WebSocket**, and **flat-file** interfaces. Data covers all 19 US exchanges, dark
pools, FINRA facilities, and OTC, going back to 2003.

For FinAlly we only need **stocks**, and only **REST polling** — per `PLAN.md §6`,
we deliberately avoid WebSockets (the simulator is the default experience; the real
source just needs to fill the same price cache on a timer).

### Host & rebrand notes

- **API host:** `https://api.polygon.io` — the original host remains the live API
  endpoint through the transition; **existing API keys continue to work unchanged**.
- **Docs host:** `https://massive.com/docs` (the old `polygon.io/docs` content moved here).
- We treat the base URL as **configurable** (`MASSIVE_BASE_URL`, default
  `https://api.polygon.io`) so that if/when the API host migrates to
  `api.massive.com` we change one env var, not code.

---

## 2. Authentication

Two equivalent methods. Both accept the same key.

**A. Query parameter** (simplest, works everywhere):

```
GET https://api.polygon.io/v3/snapshot?ticker.any_of=AAPL,MSFT&apiKey=YOUR_KEY
```

**B. Bearer header** (preferred — keeps the key out of URLs/logs):

```
GET https://api.polygon.io/v3/snapshot?ticker.any_of=AAPL,MSFT
Authorization: Bearer YOUR_KEY
```

FinAlly uses the **Bearer header** so the key never appears in request logs or the
SSE/debug surface.

---

## 3. Rate limits & plan tiers

| Plan | Requests/min | Data recency | Notes |
|------|-------------|--------------|-------|
| **Basic (free)** | **5 / min** | **15-min delayed** | Unified snapshot **not** included on Basic |
| Starter | higher | 15-min delayed | Unified snapshot included |
| Developer | higher | 15-min delayed | |
| Advanced | higher | **real-time** | |
| Business | higher | real-time | adds `fmv` (fair market value) field |

**Polling cadence implication (PLAN.md §6):**

- Free tier → **poll every 15 seconds** (4 calls/min, safely under 5/min).
- Paid tiers → poll every **2–15 s** depending on tier.

Because we poll **all watched tickers in a single request** (the unified snapshot
takes up to 250 tickers via `ticker.any_of`), one poll = one API call regardless of
watchlist size. A 10-ticker watchlist on the free tier costs 4 calls/min.

> ⚠️ The **Basic (free) plan does not include the unified snapshot endpoint.** On a
> truly free key, fall back to per-ticker **previous-day bars** or skip snapshotting.
> In practice FinAlly's "real data" path assumes at least a Starter key; the simulator
> is the no-key default, so this is an acceptable constraint. The client surfaces a
> clear error if the snapshot endpoint returns `NOT_AUTHORIZED`.

### Rate-limit / error responses

- HTTP `429 Too Many Requests` when over the limit.
- HTTP `401`/`403` with a JSON body like
  `{"status":"NOT_AUTHORIZED","message":"...","request_id":"..."}` for auth/plan issues.
- Successful bodies carry `"status":"OK"` (snapshots) or `"status":"OK"|"DELAYED"`
  (aggregates). Always check `status` in addition to the HTTP code.

The client must handle `429` by backing off (it should never crash the poll loop or
the price cache); a failed poll just means the cache keeps its last values until the
next successful tick.

---

## 4. Endpoints we use

### 4.1 Unified Snapshot — **primary** (live multi-ticker prices)

The workhorse: one request returns the latest trade/quote + session OHLC for up to
**250 tickers**.

```
GET /v3/snapshot
```

**Key query parameters**

| Param | Meaning |
|-------|---------|
| `ticker.any_of` | Comma-separated list of tickers, **max 250** (this is how we batch the watchlist) |
| `type` | Asset class filter — set to `stocks` |
| `limit` | Results per page (default 10, **max 250**) — set to 250 |
| `sort`, `order` | Optional ordering |

**Example request**

```
GET /v3/snapshot?type=stocks&ticker.any_of=AAPL,GOOGL,MSFT&limit=250
Authorization: Bearer YOUR_KEY
```

**Example response (trimmed)**

```json
{
  "request_id": "abc123",
  "status": "OK",
  "results": [
    {
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "type": "stocks",
      "market_status": "open",
      "last_trade": {
        "price": 191.23,
        "size": 100,
        "exchange": 4,
        "sip_timestamp": 1717603200000000000,
        "timeframe": "REAL-TIME"
      },
      "last_quote": {
        "bid": 191.22, "ask": 191.24,
        "bid_size": 2, "ask_size": 3,
        "last_updated": 1717603200000000000
      },
      "session": {
        "open": 189.90, "close": 191.23,
        "high": 192.10, "low": 189.50,
        "volume": 24500000,
        "change": 1.33,
        "change_percent": 0.70
      },
      "fmv": 191.20
    }
  ]
}
```

**Field we treat as "the price":** `results[i].last_trade.price`.
If `last_trade` is missing (thinly traded / pre-open), fall back to
`session.close`, then `last_quote` mid `((bid+ask)/2)`.

> **Timestamps are nanoseconds** (`sip_timestamp`, `last_quote.last_updated`) — divide
> by 1e9 for seconds. `session` timestamps are not given; we stamp updates with our own
> `time.time()` when writing to the cache, consistent with the simulator.

> **Pagination:** with ≤250 tickers and `limit=250` everything comes in one page, so we
> ignore `next_url`. (FinAlly's default watchlist is 10; a user is unlikely to exceed
> 250.) If `next_url` ever appears, follow it, appending `apiKey`/Bearer.

### 4.2 Custom Aggregate Bars — **history backfill** (main chart)

Used to seed the rolling history buffer / `GET /api/prices/{ticker}/history` so the
detailed chart renders immediately (PLAN.md §6, §8). Returns OHLC bars over a range.

```
GET /v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}
```

**Path params:** `stocksTicker` (case-sensitive, e.g. `AAPL`), `multiplier` (int),
`timespan` (`minute|hour|day|week|month|...`), `from`/`to` (`YYYY-MM-DD` or ms epoch).

**Query params:** `adjusted` (default `true`), `sort` (`asc|desc`), `limit`
(default 5000, max 50000).

**Example** — last few days of 1-minute bars for the chart:

```
GET /v2/aggs/ticker/AAPL/range/1/minute/2026-06-04/2026-06-05?adjusted=true&sort=asc&limit=5000
Authorization: Bearer YOUR_KEY
```

**Example response**

```json
{
  "ticker": "AAPL",
  "adjusted": true,
  "queryCount": 2,
  "resultsCount": 2,
  "status": "OK",
  "results": [
    { "o": 74.06, "h": 75.15, "l": 73.79, "c": 75.08, "v": 135647456, "vw": 74.60, "t": 1577941200000, "n": 1 },
    { "o": 74.28, "h": 75.14, "l": 74.12, "c": 74.35, "v": 146535512, "vw": 74.70, "t": 1578027600000, "n": 1 }
  ]
}
```

Bar fields: `o/h/l/c` OHLC, `v` volume, `vw` volume-weighted avg, `t` **ms** epoch,
`n` trade count. For our chart we map each bar to a `(t/1000, c)` point.

### 4.3 Previous Day Bar — fallback / reference price

Single-ticker prior trading day OHLC. Available on **all plans** (including free),
so it's a useful fallback for setting a reference/"open" price when the snapshot
endpoint isn't available on the key's tier.

```
GET /v2/aggs/ticker/{stocksTicker}/prev?adjusted=true
```

```json
{
  "ticker": "AAPL", "adjusted": true, "status": "OK",
  "results": [{ "o": 115.55, "h": 117.59, "l": 114.13, "c": 115.97, "v": 131704427, "vw": 116.30, "t": 1605042000000 }]
}
```

---

## 5. Python client (httpx) — reference implementation

This is the concrete `MassiveSource` body referenced by `MARKET_INTERFACE.md`. It is
async, uses a shared `httpx.AsyncClient`, and never raises out of the poll loop.

```python
# backend/app/market/massive.py
from __future__ import annotations
import asyncio
import os
import time
from typing import Iterable

import httpx

DEFAULT_BASE_URL = "https://api.polygon.io"


class MassiveError(Exception):
    """Raised for auth/plan problems the caller should surface (e.g. add-watchlist)."""


class MassiveClient:
    """Thin async wrapper over the Massive (Polygon) REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("MASSIVE_API_KEY is required for MassiveClient")
        self._base_url = (base_url or os.getenv("MASSIVE_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- live prices -------------------------------------------------------

    async def snapshot(self, tickers: Iterable[str]) -> dict[str, dict]:
        """Return {ticker: snapshot_result} for the given tickers (<=250).

        Batches into <=250-ticker chunks. Picks last_trade.price as the price,
        falling back to session.close then quote mid. Never raises on a single
        bad ticker; raises MassiveError only for auth/plan failures.
        """
        tickers = [t.upper() for t in tickers]
        out: dict[str, dict] = {}
        for chunk in _chunks(tickers, 250):
            params = {
                "type": "stocks",
                "ticker.any_of": ",".join(chunk),
                "limit": 250,
            }
            resp = await self._client.get("/v3/snapshot", params=params)
            self._raise_for_plan(resp)
            resp.raise_for_status()
            body = resp.json()
            for r in body.get("results", []):
                out[r["ticker"]] = r
        return out

    async def latest_prices(self, tickers: Iterable[str]) -> dict[str, float]:
        """Flatten snapshot() to {ticker: price}, dropping tickers with no usable price."""
        snap = await self.snapshot(tickers)
        prices: dict[str, float] = {}
        for tk, r in snap.items():
            price = _extract_price(r)
            if price is not None:
                prices[tk] = price
        return prices

    # --- history backfill --------------------------------------------------

    async def history(
        self,
        ticker: str,
        *,
        multiplier: int = 1,
        timespan: str = "minute",
        from_: str,
        to: str,
        limit: int = 5000,
    ) -> list[tuple[float, float]]:
        """Return [(epoch_seconds, close)] bars, oldest first, for the main chart."""
        path = f"/v2/aggs/ticker/{ticker.upper()}/range/{multiplier}/{timespan}/{from_}/{to}"
        resp = await self._client.get(
            path, params={"adjusted": "true", "sort": "asc", "limit": limit}
        )
        self._raise_for_plan(resp)
        resp.raise_for_status()
        body = resp.json()
        return [(bar["t"] / 1000.0, bar["c"]) for bar in body.get("results", [])]

    async def previous_close(self, ticker: str) -> float | None:
        """Prior trading day's close — works on all plans; used as a reference price."""
        resp = await self._client.get(f"/v2/aggs/ticker/{ticker.upper()}/prev",
                                      params={"adjusted": "true"})
        self._raise_for_plan(resp)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        return results[0]["c"] if results else None

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _raise_for_plan(resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            try:
                msg = resp.json().get("message", resp.text)
            except Exception:
                msg = resp.text
            raise MassiveError(f"Massive API not authorized ({resp.status_code}): {msg}")


def _extract_price(result: dict) -> float | None:
    lt = result.get("last_trade") or {}
    if lt.get("price"):
        return float(lt["price"])
    session = result.get("session") or {}
    if session.get("close"):
        return float(session["close"])
    q = result.get("last_quote") or {}
    if q.get("bid") and q.get("ask"):
        return (float(q["bid"]) + float(q["ask"])) / 2.0
    return None


def _chunks(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
```

### Polling loop sketch (lives in the source adapter, not the client)

```python
async def poll_loop(client: MassiveClient, get_watched, cache, interval: float = 15.0):
    """Single background task: poll all watched tickers, write the shared cache."""
    while True:
        started = time.time()
        try:
            prices = await client.latest_prices(get_watched())
            for ticker, price in prices.items():
                cache.set(ticker, price)        # records prev_price + timestamp internally
        except MassiveError as exc:
            log.error("Massive auth/plan error: %s", exc)   # don't crash; surface upstream
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            log.warning("Massive poll failed, keeping last cache: %s", exc)
        # keep cadence steady regardless of request duration
        await asyncio.sleep(max(0.0, interval - (time.time() - started)))
```

---

## 6. Mapping Massive → FinAlly's `PriceUpdate`

The unified interface (next doc) emits a `PriceUpdate`. From a snapshot result:

| `PriceUpdate` field | Source |
|---------------------|--------|
| `ticker` | `results[i].ticker` |
| `price` | `_extract_price(result)` (last_trade → session.close → quote mid) |
| `previous_price` | the cache's prior value for this ticker (drives green/red flash) |
| `timestamp` | our `time.time()` at write (Massive ts are ns and not always present) |
| `direction` | `"up"/"down"/"flat"` from `price` vs `previous_price` |

The **reference price** (PLAN.md §6, for "Chg %") is set the first time we observe a
ticker — optionally seeded from `previous_close()` for a more meaningful baseline on
the real-data path. The simulator instead uses its seed price as the reference.

---

## 7. Things to watch out for

- **Free tier ≠ snapshot.** Unified snapshot needs Starter+. A bare free key gets
  `NOT_AUTHORIZED` → surface clearly, suggest using the simulator (no key).
- **15-minute delay** on Starter/Developer: prices are real but lagged. Acceptable for
  a demo; the UI doesn't claim real-time.
- **Market hours:** outside RTH, `last_trade` may be stale and `market_status` is
  `closed`/`extended-hours`. Prices simply won't change much — that's fine; the cache
  holds last values and the simulator is the "always-moving" default anyway.
- **Case-sensitive tickers** in aggregate paths — always upper-case.
- **Never let a poll exception kill the loop or clear the cache.** A failed tick =
  stale-but-present data until the next success.
- **Nanosecond vs millisecond timestamps:** snapshots use **ns**, aggregates use **ms**.

---

## Sources

- [Overview | Stocks REST API — Massive](https://massive.com/docs/rest/stocks/overview)
- [Unified Snapshot | Stocks REST API — Massive](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot)
- [Custom Bars (Aggregates) | Stocks REST API — Massive](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
- [Previous Day Bar | Stocks REST API — Massive](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar)
- [Polygon.io is Now Massive](https://massive.com/blog/polygon-is-now-massive)
- [What is the request limit for Massive's RESTful APIs?](https://polygon.io/knowledge-base/article/what-is-the-request-limit-for-polygons-restful-apis)
