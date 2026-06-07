# Market Data Backend — Code Review

> Reviewer: Claude (Opus 4.8) · Date: 2026-06-07
> Scope: `backend/app/market/*`, `backend/app/api/*`, `backend/app/main.py`,
> `backend/tests/*`, `backend/pyproject.toml`, measured against `PLAN.md §6/§8/§10/§12`
> and the companion design docs (`MARKET_DATA_DESIGN.md`, `MARKET_INTERFACE.md`,
> `MARKET_SIMULATOR.md`, `MASSIVE_API.md`).

## TL;DR

The implementation is **well-structured and faithful to the design docs** — the
`MarketSource` ABC, the `MarketDataService` facade (single driver loop, cache, history
buffer, SSE fan-out), the GBM simulator, and the Massive REST adapter are all present and
match the specs closely. Code is clean, typed, and documented.

However, the deliverable does **not** pass its own acceptance bar:

- **The project does not build with its documented tooling** (`uv sync` / `uv run` fail).
  This breaks the Dockerfile and the start scripts. (HIGH — trivial fix.)
- **5 of 103 tests fail, consistently** (98 pass). 3 are test bugs (wrong assumptions, not
  product defects); 2 expose a real modeling/test tension in the simulator.
- **Zero API/route tests** exist, despite `PLAN.md §12` requiring them.

None of the findings indicate a deep architectural problem; they are fixable without
redesign.

---

## How tests were run

`uv` was not preinstalled; installed it, but `uv run` fails at the build step (see
Finding 1). Tests were therefore executed two ways to rule out environment effects:

1. A clean venv with the runtime + test deps, `PYTHONPATH=. pytest`.
2. After applying the one-line packaging fix, `uv run pytest` (to confirm parity).

Both produce **identical results: `5 failed, 98 passed`**, stable across 3 consecutive
runs (no flakiness).

```
FAILED tests/test_service.py::test_driver_loop_broadcasts_on_change
FAILED tests/test_service.py::test_driver_loop_no_broadcast_on_unchanged_price
FAILED tests/test_service.py::test_multiple_subscribers
FAILED tests/test_sim_source.py::test_gbm_statistical_validity
FAILED tests/test_sim_source.py::test_sector_correlation
```

---

## Findings

### 1. HIGH — Project cannot be built (`uv sync` / `uv run` fail)

`backend/pyproject.toml` declares a hatchling build backend but no package selection, and
the project name `finally-backend` normalizes to `finally_backend`, which does not match
the actual package directory `app/`. Hatchling cannot determine what to ship:

```
ValueError: Unable to determine which files to ship inside the wheel ...
The most likely cause of this is that there is no directory that matches
the name of your project (finally_backend).
```

Impact: every documented run path is broken — `Dockerfile` Stage 2 (`uv sync`),
`scripts/start_*.sh`, and `uv run pytest`. A student following the README cannot start the
app.

**Fix (verified — `uv sync` then succeeds and the suite runs under `uv run`):**

```toml
[tool.hatch.build.targets.wheel]
packages = ["app"]
```

### 2. MEDIUM (test defects) — 3 facade tests assume wrong SSE batch boundaries

`test_driver_loop_broadcasts_on_change`, `test_driver_loop_no_broadcast_on_unchanged_price`,
and `test_multiple_subscribers` each read **one** batch after the snapshot and assert it
contains the latest price (191.0). It does not — and the **product behavior is correct**.

Captured delivery for the `[{AAPL:190},{AAPL:191}]` script:

```
snapshot: []                       # connected before any data
batch 1 : [(190.0, 'flat')]        # first-sight update — its own event
batch 2 : [(191.0, 'up')]          # the change the test expected
```

The first observation of a ticker is broadcast as a discrete `FLAT` `PriceUpdate` (correct
— it is new data the client needs), so the *first* post-snapshot batch is `190 flat`, not
`191`. The tests consume only one batch and therefore fail.

These are **test bugs, not code bugs.** Fix the tests to drain until the expected price
appears (e.g. loop `__anext__` collecting prices until `191.0` is seen, or assert across
accumulated batches). Do **not** change the production code to satisfy them.

### 3. MEDIUM (modeling/test tension) — 2 simulator statistics tests fail

Root cause isolated empirically (not rounding, as one might first guess):

- `test_sector_correlation` expects same-sector tick-return correlation `> 0.3`; actual
  **0.136**. With `_maybe_event` **disabled** the correlation is **0.714** (matching the
  theoretical `w_m + w_s = 0.7`). The rare events (2–5% jump at `p=0.002`/tick) are
  **idiosyncratic per ticker and enormous** relative to the normal per-tick move
  (~0.06% at `VOL_SCALE=8`). Their variance contribution (`≈0.002·0.035² ≈ 2.5e-6`)
  dominates the correlated GBM variance (`≈(6.5e-4)² ≈ 4e-7`), drowning the signal.
- `test_gbm_statistical_validity` (run at `VOL_SCALE=1`) fails the std tolerance for the
  same reason, compounded by `round(price, 2)`: at `VOL_SCALE=1` a per-tick move on AAPL
  is ~$0.015, comparable to the $0.01 cent-quantization, so rounding further corrupts the
  measured log-return statistics.

This is a genuine mismatch between **what the tests assert** (pure GBM / correlation
properties) and **what `SimulatedSource.fetch_prices` actually does** (GBM **plus** large
idiosyncratic events **plus** cent-rounding). Both cannot hold simultaneously as written.

Recommended resolution (pick one, lean toward the first):
- Make events and rounding **injectable/disable-able** for tests — e.g. an
  `enable_events: bool` and a `round_to: int | None` on `SimulatedSource`, or have the two
  statistical tests monkeypatch `_maybe_event → 1.0` and skip rounding. The correlation
  test already proves 0.71 once events are off.
- Or relax the thresholds to reflect the event-inflated variance (less principled — it
  hides that events dominate variance, which is itself worth a design note).

Either way the **simulator code is functioning as designed**; the design simply prioritizes
"visible drama" over statistical purity, and the tests were written against purity.

### 4. MEDIUM — No API / route tests (spec gap)

`PLAN.md §12` lists "API routes: correct status codes, response shapes, error handling".
There are **no tests** exercising `app/api/stream.py`, `prices.py`, `watchlist.py`, or
`main.py` via `fastapi.TestClient`/`httpx.ASGITransport`. Notably untested:
- SSE framing, the heartbeat merge (`_merge_with_heartbeat`), and disconnect teardown.
- `GET /api/prices/{ticker}/history` response shape.
- `POST /api/watchlist` 422-on-`SymbolError`, `GET`/`DELETE` behavior.
- `GET /api/health`.

The cache, history buffer, sources, and facade are well covered; the HTTP edge is not.

### 5. MEDIUM — Massive "Chg %" reference baseline can be silently lost (race)

In `MarketDataService.add_ticker` → `_maybe_seed_reference` performs an **awaited** HTTP
call (`previous_close`) *after* `register_ticker` has already primed `MassiveSource._latest`.
During that await, a driver tick can run `fetch_prices` (which now returns the primed
price) and `PriceCache.set` creates the entry with `reference = current price`. The later
`seed_reference` is then a no-op (it only seeds when the ticker is absent), so the
prior-close baseline intended for "Chg %" is discarded on the real-data path. Sim is
unaffected (its reference == seed price by design). Consider seeding the reference *before*
priming `_latest`, or having `seed_reference` win when the entry was created in the same
add cycle.

### 6. LOW — SSE disconnect teardown is plausibly racy and untested

`stream.py` cancels the in-flight `pending = updates.__anext__()` future in the
`finally`, then the outer `gen()` `finally` calls `await updates_iter.aclose()`. Cancelling
an in-progress `__anext__` and then `aclose()`-ing the same async generator can raise
"async generator is already running" under some interleavings. Also `request.is_disconnected()`
is only consulted when a batch or heartbeat arrives, so a client that drops while idle is
detected only at the next heartbeat (≤15s) — acceptable, but worth noting. Add a route-level
test that opens and drops the stream.

### 7. LOW — Watchlist add/remove does not affect streaming without the DB

`main.py` only ever reads `getattr(app.state, "db", None)`, which is never set (the DB
component is not built yet). Consequences today:
- `POST /api/watchlist` registers the symbol in the source but `get_watched()` returns the
  **static** `DEFAULT_WATCHLIST`, so a newly added ticker is **never fetched/streamed**.
- `DELETE` is effectively a no-op for streaming.
- `PriceCache`/`HistoryBuffer` never evict; a (future) removed ticker lingers in the cache
  and is re-emitted in the reconnect snapshot forever.

This is an expected **integration dependency** on the not-yet-built DB layer, not a
market-data defect — but it means the "add a ticker and watch it stream" flow is currently
non-functional end-to-end. Flag it so the DB/portfolio agent wires `app.state.db` and adds
cache eviction on remove.

### 8. LOW — `MarketConfig.from_env` ignores several of its own fields

`from_env()` reads only `MASSIVE_API_KEY`, `MASSIVE_BASE_URL`, `MASSIVE_POLL_INTERVAL`.
The dataclass also defines `tick_interval`, `history_max_points`, `heartbeat_interval`,
and `sim_seed`, none of which are env-wired, so they silently cannot be configured. Either
wire them or drop them from the env story to avoid confusion.

### 9. NITS

- `app/market/service.py` imports `SymbolError` but never uses it.
- `types.PricePoint` is defined and exported but unused (the buffer stores raw tuples).
- `massive._extract_price` uses truthiness checks (`if lt.get("price"):`), so a genuine
  `0.0` price would fall through to the next fallback. Impossible for real equities;
  harmless, but `is not None` would be more correct.
- `CLAUDE.md` references `planning/MARKET_DATA_SUMMARY.md` and a `planning/archive/`
  folder; **neither exists**. Doc drift — either add them or update `CLAUDE.md`.

---

## What's done well

- **Clean layering** exactly as designed: source ABC → facade owns cache/history/fan-out →
  thin routes. Swapping sim↔Massive is genuinely a single branch point.
- **Conformance suite** runs the same contract against both `SimulatedSource` and a
  transport-mocked `MassiveSource` — the abstraction is actually verified, not just claimed.
- **Resilience**: driver loop and Massive poll loop both swallow transient errors and keep
  last-known cache values; tested (`test_poll_loop_survives_network_error`).
- **SSE correctness**: full snapshot on (re)connect, change-only events thereafter,
  bounded subscriber queues with drop-oldest backpressure (`test_subscriber_queue_overflow_drops_oldest`).
- **Determinism**: seedable RNG with a determinism test; good for reproducible E2E later.
- **Massive client**: price-extraction fallback chain (last_trade → session.close → quote
  mid), ns/ms timestamp handling, 401/403 → `MassiveError`, chunking — all well tested.
- Cache (reference vs previous_price distinction, change detection, `change_pct`) is the
  most thoroughly tested unit and matches `PLAN.md §6` precisely.

---

## Recommended action list (in priority order)

1. **Add `[tool.hatch.build.targets.wheel] packages = ["app"]`** — unblocks build/Docker/CI.
   (Finding 1, verified fix.)
2. **Fix the 3 facade tests** to drain SSE batches instead of asserting on the first one.
   (Finding 2 — tests only.)
3. **Resolve the simulator stats tests** by making events/rounding disable-able for the two
   statistical tests (or relaxing thresholds + documenting that events dominate variance).
   (Finding 3.)
4. **Add route-level tests** (TestClient/ASGITransport) for SSE, history, watchlist, health.
   (Finding 4.)
5. Address the Massive reference-seed race and the SSE teardown race. (Findings 5, 6.)
6. Note the DB integration dependency for the next agent; add cache eviction on remove.
   (Finding 7.)
7. Tidy config env-wiring and nits. (Findings 8, 9.)

After (1)–(3) the suite should be green; (4)–(7) raise it to production quality.
