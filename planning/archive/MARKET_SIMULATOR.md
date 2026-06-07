# Market Simulator — Default Price Source for FinAlly

> The **default** price source: runs when `MASSIVE_API_KEY` is absent (the common case
> for students). It synthesizes realistic, always-moving prices so the terminal looks
> alive with zero external dependencies and no API key.
>
> Implements the `MarketSource` interface from `MARKET_INTERFACE.md`. Spec authority:
> `PLAN.md §6`.

---

## 1. Goals

1. **Realistic-looking motion** — prices wander like real stocks (Geometric Brownian
   Motion), not random noise, starting from plausible seed prices.
2. **Always moving** — updates ~every 500 ms so the UI flashes and sparklines fill in,
   even when the market is "closed" in real life.
3. **Drama** — occasional sudden 2–5 % jumps so the demo has visible events.
4. **Correlation** — related tickers (e.g. tech) move together, so the heatmap and
   charts feel coherent.
5. **Accepts any ticker** — unknown symbols get synthesized params and stream
   immediately (PLAN.md §6 "Unknown tickers"), never rejected.
6. **Deterministic when seeded** — an optional RNG seed makes tests reproducible.

---

## 2. The model: Geometric Brownian Motion

Each ticker's price evolves by GBM, the standard model for stock prices (keeps prices
positive, produces log-normal returns):

```
S(t+Δt) = S(t) · exp( (μ − σ²/2)·Δt  +  σ·√Δt · Z )
```

where:

- `S(t)` — current price
- `μ` (drift) — annualized expected return (small; e.g. 0.05 = +5 %/yr)
- `σ` (volatility) — annualized volatility (e.g. 0.3 = 30 %/yr)
- `Δt` — time step as a fraction of a trading year
- `Z` — a standard normal random draw (the per-ticker correlated shock, see §4)

### Choosing Δt

We tick every **0.5 s**. To make σ behave like a realistic *annual* volatility we
express Δt in years using trading-time (≈ 252 days × 6.5 h × 3600 s ≈ 5.9M seconds):

```python
SECONDS_PER_TRADING_YEAR = 252 * 6.5 * 3600   # ≈ 5,896,800
dt = tick_seconds / SECONDS_PER_TRADING_YEAR   # ≈ 8.48e-8 for 0.5s
```

With σ≈0.3 this yields realistic ~0.01–0.05 % moves per tick — visible flashes without
absurd swings. (We can scale σ up modestly for "demo liveliness" — see §6 tuning.)

---

## 3. Seed prices & per-ticker parameters

Ten default tickers (PLAN.md §7 seed data) get hand-set realistic seeds and params.
Everything else is synthesized.

```python
# backend/app/market/sim_params.py
from dataclasses import dataclass


@dataclass(frozen=True)
class TickerParams:
    seed_price: float
    drift: float          # μ, annualized
    volatility: float     # σ, annualized
    sector: str           # for correlation grouping (§4)


SEED: dict[str, TickerParams] = {
    "AAPL": TickerParams(190.0, 0.08, 0.28, "tech"),
    "GOOGL": TickerParams(175.0, 0.10, 0.30, "tech"),
    "MSFT": TickerParams(420.0, 0.09, 0.26, "tech"),
    "AMZN": TickerParams(185.0, 0.11, 0.34, "tech"),
    "TSLA": TickerParams(250.0, 0.05, 0.55, "auto"),   # high vol on purpose
    "NVDA": TickerParams(120.0, 0.20, 0.50, "tech"),
    "META": TickerParams(480.0, 0.12, 0.36, "tech"),
    "JPM":  TickerParams(200.0, 0.06, 0.22, "finance"),
    "V":    TickerParams(275.0, 0.07, 0.20, "finance"),
    "NFLX": TickerParams(630.0, 0.10, 0.38, "media"),
}
```

### Synthesizing unknown tickers (PLAN.md §6)

When a symbol not in `SEED` is added (manually or by the AI), the simulator invents
plausible, **stable** params so the ticker streams instantly. Params are derived
deterministically from the symbol hash so the same ticker always gets the same seed
within reason:

```python
import hashlib

def synth_params(ticker: str) -> TickerParams:
    h = int(hashlib.sha256(ticker.encode()).hexdigest(), 16)
    seed_price = 20.0 + (h % 48000) / 100.0      # $20 .. $500
    volatility = 0.20 + (h % 40) / 100.0          # 0.20 .. 0.60
    drift = 0.05 + ((h >> 8) % 15) / 100.0        # 0.05 .. 0.20
    return TickerParams(round(seed_price, 2), drift, volatility, sector="other")
```

This is the simulator's `register_ticker` behavior: uppercase, add to the live param
map if absent, **always succeed** (no `SymbolError`), return the canonical symbol.

---

## 4. Correlation across tickers

Real sectors move together. We split each ticker's shock `Z` into a **market factor**,
a **sector factor**, and an **idiosyncratic** component:

```
Z_ticker = √w_m · Z_market  +  √w_s · Z_sector[sector]  +  √(1 − w_m − w_s) · Z_idio
```

with e.g. `w_m = 0.4` (market), `w_s = 0.3` (sector). Each tick we draw **one**
`Z_market`, one `Z_sector` per sector, and a fresh `Z_idio` per ticker, then combine.
Because the weights sum to 1, `Z_ticker` stays ~N(0,1), so GBM math is unchanged — but
tech names now rise and fall together, and the whole market drifts as one on big ticks.

```python
import random

def draw_shocks(rng: random.Random, sectors: set[str]) -> dict:
    return {
        "market": rng.gauss(0, 1),
        "sector": {s: rng.gauss(0, 1) for s in sectors},
    }

def combined_z(rng: random.Random, shocks: dict, sector: str,
               w_m: float = 0.4, w_s: float = 0.3) -> float:
    z_idio = rng.gauss(0, 1)
    return (
        (w_m ** 0.5) * shocks["market"]
        + (w_s ** 0.5) * shocks["sector"].get(sector, 0.0)
        + ((1 - w_m - w_s) ** 0.5) * z_idio
    )
```

---

## 5. Random "events" (drama)

On each tick, with small probability, a single ticker takes a sudden 2–5 % jump
(direction random). This produces the occasional dramatic candle the demo wants.

```python
EVENT_PROB_PER_TICK = 0.002     # ~1 event every ~500 ticks ≈ every ~4 min per ticker

def maybe_event(rng: random.Random) -> float:
    """Return a multiplicative shock (1.0 = none, else 0.95..1.05)."""
    if rng.random() < EVENT_PROB_PER_TICK:
        magnitude = rng.uniform(0.02, 0.05)
        return 1.0 + (magnitude if rng.random() < 0.5 else -magnitude)
    return 1.0
```

Applied as a multiplier on top of the GBM step.

---

## 6. Putting it together — `SimulatedSource`

```python
# backend/app/market/sim_source.py
from __future__ import annotations
import math
import random
from typing import Iterable

from .base import MarketSource
from .sim_params import SEED, TickerParams, synth_params

SECONDS_PER_TRADING_YEAR = 252 * 6.5 * 3600
VOL_SCALE = 8.0   # demo liveliness multiplier on σ (tune for visible-but-sane motion)


class SimulatedSource(MarketSource):
    tick_interval = 0.5

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)            # seedable for deterministic tests
        self._params: dict[str, TickerParams] = dict(SEED)
        self._price: dict[str, float] = {t: p.seed_price for t, p in SEED.items()}

    async def start(self) -> None:
        # nothing to open; prices already seeded
        return

    async def stop(self) -> None:
        return

    async def register_ticker(self, ticker: str) -> str:
        t = ticker.upper()
        if t not in self._params:
            params = synth_params(t)
            self._params[t] = params
            self._price[t] = params.seed_price     # streams immediately
        return t

    async def fetch_prices(self, tickers: Iterable[str]) -> dict[str, float]:
        wanted = [t.upper() for t in tickers]
        # ensure any newly-watched ticker exists
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
            new_price = max(0.01, round(new_price, 2))   # never non-positive
            self._price[t] = new_price
            out[t] = new_price
        return out

    async def history(self, ticker: str) -> list[tuple[float, float]]:
        # The HistoryBuffer in the facade accumulates live points; the simulator has
        # no persistent past. Return [] and let the buffer fill from the stream, OR
        # synthesize a short backward GBM walk from the current price for an instant
        # non-blank chart (see §7).
        return []

    # --- helpers ---
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
```

### On `VOL_SCALE`

Raw annual σ over a 0.5 s step produces *technically correct* but visually dull motion
(sub-basis-point ticks). `VOL_SCALE` (≈8×) inflates per-tick movement to something the
eye sees flashing, while keeping the GBM shape. This is a **demo knob**, documented as
such — it trades statistical fidelity for visual liveliness, which is the right call
for a teaching/demo terminal. Tune by watching the watchlist: aim for visible but
believable ticks (~0.05–0.3 % typical, with occasional event spikes).

---

## 7. History backfill option (instant non-blank chart)

`PLAN.md §6/§8` wants the main chart to render immediately. Two strategies, in order of
preference:

1. **Buffer-first (default):** the facade's `HistoryBuffer` accumulates every live tick.
   After the page has been open a little while, `GET /api/prices/{ticker}/history`
   returns real accumulated points. For a *freshly added* ticker the buffer is briefly
   thin.
2. **Synthetic backfill (optional polish):** to avoid any blank-chart moment, the
   simulator can generate a **backward GBM walk** from the current price — e.g. 600
   points spaced 0.5 s apart over the last ~5 min — purely for first render:

```python
def synth_history(self, ticker: str, points: int = 600, step: float = 0.5):
    t = ticker.upper()
    if t not in self._params:
        return []
    p = self._params[t]
    sigma = p.volatility * VOL_SCALE
    dt = step / SECONDS_PER_TRADING_YEAR
    price = self._price[t]
    series: list[tuple[float, float]] = []
    now = time.time()
    # walk backwards: invert the GBM step with fresh shocks
    for i in range(points):
        ts = now - i * step
        series.append((ts, round(price, 2)))
        z = self._rng.gauss(0, 1)
        price = price / math.exp((p.drift - 0.5 * sigma ** 2) * dt
                                 + sigma * math.sqrt(dt) * z)
        price = max(0.01, price)
    return list(reversed(series))
```

Whether to enable synthetic backfill is the implementer's call; buffer-first satisfies
the spec, synthetic backfill is the extra polish.

---

## 8. Concurrency & lifecycle

- `SimulatedSource` is pure in-process state mutated only inside `fetch_prices`, which
  the facade's single driver loop calls serially — **no locking needed**.
- No threads, no external connections, no `start/stop` work beyond seeding.
- All randomness flows through one `random.Random`; pass a seed for deterministic tests.

---

## 9. Testing (PLAN.md §12)

- **GBM validity:** over many ticks prices stay positive; mean log-return ≈ μ·Δt and
  return std ≈ σ·√Δt (within tolerance) when `VOL_SCALE = 1`.
- **Determinism:** two `SimulatedSource(seed=42)` instances produce identical price
  sequences for the same ticker set.
- **Unknown ticker:** `register_ticker("ZZZZ")` succeeds, returns `"ZZZZ"`, and the
  next `fetch_prices(["ZZZZ"])` yields a positive price (never raises).
- **Correlation:** with `w_m`/`w_s` > 0, same-sector tickers' tick-to-tick returns are
  positively correlated over a long run; cross-sector less so.
- **Events:** with a forced high `EVENT_PROB`, jumps of 2–5 % appear at expected rate.
- **Interface conformance:** runs the shared `MarketSource` contract suite from
  `MARKET_INTERFACE.md §8` — the same suite the Massive source must pass.

---

## 10. Parameter summary (tuning cheat-sheet)

| Knob | Default | Effect |
|------|---------|--------|
| `tick_interval` | `0.5 s` | Update cadence (drives flash/sparkline rate) |
| `VOL_SCALE` | `8.0` | Visual liveliness multiplier on σ (demo knob) |
| `w_m` (market weight) | `0.4` | How much tickers move together overall |
| `w_s` (sector weight) | `0.3` | How much same-sector tickers co-move |
| `EVENT_PROB_PER_TICK` | `0.002` | Frequency of 2–5 % dramatic jumps |
| event magnitude | `2–5 %` | Size of dramatic jumps |
| per-ticker `seed_price`/`drift`/`volatility` | see `SEED` | Realistic starting points & character |
```
