# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── db/                   # Schema definitions, seed data, migration logic
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/db/`** contains schema SQL definitions and seed logic. The backend lazily initializes the database on first request — creating tables and seeding default data if the SQLite file doesn't exist or is empty.
- **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts via Docker volume.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Required: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- The backend reads `.env` from the project root (mounted into the container or read via docker `--env-file`)

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.)
- Runs as an in-process background task — no external dependencies
- **Unknown tickers**: when a ticker without predefined seed/GBM params is added (manually or via the AI), the simulator synthesizes a plausible seed price and default GBM params so the ticker streams immediately rather than being rejected or showing "no data". (The Massive source instead returns whatever the API provides for that symbol; truly invalid symbols are surfaced as an add-watchlist error — see §8.)

### Reference ("Open") Price & Change %

- At server start, the price layer records a **reference price** per ticker (its first/seed price). For tickers added later, the reference is the first price observed after they join.
- "Change %" shown in the watchlist and header is defined as `(current - reference) / reference` — i.e., change since the session/server began, **not** an exchange trading-day open (the simulator has no real trading calendar). This reference is distinct from `PriceUpdate.previous_price`, which is only the prior tick and drives the green/red flash.
- The reference price is held in memory alongside the cache and is reset whenever the server restarts.

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the union of all watched tickers on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds the latest price, previous price, and timestamp for each ticker
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

### Rolling Price History (for charts)

- Alongside the latest-price cache, the price layer keeps a **bounded in-memory ring buffer** of recent prices per ticker (e.g., the last ~30 minutes, capped at a fixed point count) so the main chart can render immediately on first click instead of starting blank and filling in slowly.
- This history is in-memory only (lost on restart) and is **not** persisted to SQLite — it is purely a UX convenience for the detailed chart. Sparklines continue to accumulate frontend-side from the SSE stream; the history buffer backfills the larger main chart.
- Exposed via `GET /api/prices/{ticker}/history` (see §8).

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- **On connect**, the server emits a single **full snapshot** event containing the current price for every known ticker, so a freshly connected (or reconnected) client has complete state without waiting for the next tick.
- **Thereafter**, the server pushes updates using **version-based change detection**: it evaluates the cache on each simulation/poll tick (~500ms) and emits events only for tickers whose price actually changed since the last event. This avoids redundant payloads while keeping latency at ~500ms.
- A periodic lightweight **heartbeat/comment** is sent (e.g., every ~15s) so the client and intermediaries can distinguish a live-but-idle connection from a dropped one.
- Each price event contains ticker, price, previous price, timestamp, and change direction
- Client handles reconnection automatically (EventSource has built-in retry); on reconnect it receives a fresh full snapshot as the first event

---

## 7. Database

### SQLite with Lazy Initialization

The backend checks for the SQLite database on startup (or first request). If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

### Concurrency

Two writers exist: the periodic `portfolio_snapshots` background task (§7, every 30s) and request handlers (trades, watchlist, chat). To avoid SQLite "database is locked" errors under concurrent writes:

- Enable **WAL mode** (`PRAGMA journal_mode=WAL`) on initialization
- Serialize writes (a single shared connection or a write lock/queue) so the snapshot task and request handlers never contend
- Reads remain concurrent and lock-free under WAL

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL (fractional shares supported)
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds by a background task, and immediately after each trade execution. The 30s timer is intentional: it keeps the P&L line moving (positions revalue as prices stream) even when the user is idle and placing no trades.
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — trades executed, watchlist changes made; null for user messages). Shape defined in §9 (Auto-Execution) so the frontend can render inline confirmations consistently. Includes both successful and failed/rejected actions, each with a status.
- `created_at` TEXT (ISO timestamp)

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates (full snapshot on connect, then change-only events) |
| GET | `/api/prices/{ticker}/history` | Recent in-memory price history for a ticker (backfills the main chart on first render) |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized P&L |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots over time (for P&L chart) |

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices — used for **initial paint and on (re)connect**; live updates thereafter come from SSE, not by polling this endpoint |
| POST | `/api/watchlist` | Add a ticker: `{ticker}`. Validates/normalizes the symbol; an unusable symbol returns an error the caller surfaces |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/chat` | Recent conversation history (for rendering the chat panel on page load) |
| POST | `/api/chat` | Send a message, receive complete JSON response (message + executed actions) |

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment) |

---

## 9. LLM Integration

When writing code to make calls to LLMs, use cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results.

There is an OPENROUTER_API_KEY in the .env file in the project root.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads recent conversation history from the `chat_messages` table
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output, using the cerebras-inference skill
5. Parses the complete structured JSON response
6. Auto-executes any trades or watchlist changes specified in the response
7. Stores the message and executed actions in `chat_messages`
8. Returns the complete JSON response to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute. Each trade goes through the same validation as manual trades (sufficient cash for buys, sufficient shares for sells)
- `watchlist_changes` (optional): Array of watchlist modifications

**`quantity` is always a number of shares** (fractional allowed), never a dollar amount. The REST trade contract is share-based only. When the user expresses a notional intent ("buy $1,000 of NVDA"), the LLM is responsible for converting to shares using the live prices it receives in portfolio context, and emitting the resulting share quantity. The system prompt states this explicitly.

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

If a trade fails validation (e.g., insufficient cash), the error is included in the chat response so the LLM can inform the user.

**Persisted `actions` shape.** What was executed is recorded on the assistant message (`chat_messages.actions`) and returned to the frontend so it can render inline confirmations. Both successes and failures are recorded:

```json
{
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10, "status": "executed", "price": 191.23},
    {"ticker": "TSLA", "side": "buy", "quantity": 5, "status": "rejected", "error": "Insufficient cash"}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add", "status": "executed"}
  ]
}
```

**Structured-output fallback.** Structured output is requested on every call, but the backend does not assume it always returns schema-valid JSON. If the response cannot be parsed/validated, the backend executes **no** actions and returns a safe, generic assistant message (e.g., "Sorry — I couldn't process that. Please try rephrasing."). It never partially applies actions from a malformed response. (Whether `openrouter/openai/gpt-oss-120b` on Cerebras enforces the JSON schema vs. best-effort is confirmed via the `cerebras-inference` skill during implementation; the fallback makes correctness independent of that guarantee.)

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Convert any dollar/notional request into a share quantity using the live prices in context before emitting a trade (the API accepts shares only)
- Be concise and data-driven in responses
- Always respond with valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), change % (since session/server start — see §6 "Reference Price"; labeled "Chg" rather than "Day" to avoid implying a real trading day), and a sparkline mini-chart (accumulated from SSE since page load)
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Clicking a ticker in the watchlist selects it here. On selection the chart **backfills from `GET /api/prices/{ticker}/history`** (§8) so it renders immediately, then extends live from the SSE stream.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, using data from `portfolio_snapshots`
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill.
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Trade executions and watchlist changes shown inline as confirmations.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance

### Live Total Value — Source of Truth

The header's total value updates **client-side**: the frontend recomputes `cash + Σ(quantity × latest SSE price)` as prices stream, so it moves smoothly without polling. `GET /api/portfolio` (with its `total_value`) is the authoritative value used on initial load and after a trade; the live client-side figure is a continuous interpolation between those authoritative reads. Brief, sub-tick disagreement between the two is expected and acceptable.

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- **Charting libraries (one role each):** **Lightweight Charts** (canvas, performant) for the high-frequency time-series — the main price chart and the P&L chart; **Recharts** for the portfolio **treemap/heatmap**, which updates only on portfolio changes. Sparklines can use Lightweight Charts or a trivial inline SVG. Avoid pulling in a third charting library.
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build (produces static export)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync (install Python dependencies from lockfile)
  - Copy frontend build output into a static/ directory
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and all API routes on port 8000.

There is one blessed way to run the app: the start/stop scripts (below), which wrap a single `docker run`. There is no production `docker-compose.yml` — the only compose file is `test/docker-compose.test.yml` for E2E (§12). This keeps "how do I run it?" unambiguous for students.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container. The backend writes `finally.db` to this path.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

> **Exposure warning.** The app is intentionally unauthenticated (no login) and exposes endpoints that execute trades and spend OpenRouter credits via the chat. That is fine for local use (bound to `localhost`), but a public cloud deploy puts those endpoints on the open internet. Any cloud deployment should sit behind access control (platform-level basic auth, an IP allowlist, or similar). Treat public deployment as a deliberate, gated step — not the default.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, Massive API response parsing works, both implementations conform to the abstract interface
- Portfolio: trade execution logic, P&L calculations, edge cases (selling more than owned, buying with insufficient cash, selling at a loss)
- LLM: structured output parsing handles all valid schemas, graceful handling of malformed responses, trade validation within chat flow
- API routes: correct status codes, response shapes, error handling

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations
- Portfolio display calculations
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: A separate `docker-compose.test.yml` in `test/` that spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**Environment**: Tests run with `LLM_MOCK=true` by default for speed and determinism.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist
- Buy shares: cash decreases, position appears, portfolio updates
- Sell shares: cash increases, position updates or disappears
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points
- AI chat (mocked): send a message, receive a response, trade execution appears inline
- Chat history persists: send a message, reload the page, prior conversation still renders (via `GET /api/chat`)
- Main chart backfills: select a ticker and verify the chart renders data immediately (via `GET /api/prices/{ticker}/history`), not blank
- SSE resilience: disconnect and verify reconnection, including receipt of a full snapshot on reconnect

---

## 13. Design Decisions Log

> A documentation review (2026-06-04) surfaced 16 ambiguities/gaps in the portfolio, chat, and frontend specs (the market-data subsystem in §6 was already built). Each has been resolved **inline** in the sections above; this log records the decision and where it now lives, so the rationale isn't lost. The market-data subsystem already shipped — items touching §6 are spec clarifications for downstream code, not changes to built behavior.

| # | Issue | Decision | Where |
|---|-------|----------|-------|
| 1 | No endpoint to load chat history | Added `GET /api/chat` (recent messages, for rendering panel on load) | §8 |
| 2 | Main chart blank on load (no price history) | Added a bounded in-memory rolling history buffer + `GET /api/prices/{ticker}/history`; chart backfills then extends from SSE | §6, §8, §10 |
| 3 | "Daily change %" baseline undefined | Defined a per-ticker **reference price** captured at session/server start; "Chg %" = change vs. reference (not a real trading day); relabeled in UI | §6, §10 |
| 4 | SSE cadence vs. change-detection mismatch | Reconciled: full snapshot on connect, then change-only events on each ~500ms tick, plus a periodic heartbeat | §6 |
| 5 | Shares vs. notional in trades | `quantity` is always shares; the LLM converts dollar requests to shares using live prices | §9 |
| 6 | Cerebras structured-output guarantee unknown | Backend treats structured output as best-effort: unparseable → no actions executed + safe generic message; verified via `cerebras-inference` skill during build | §9 |
| 7 | Live total value — source of truth | Frontend computes live (`cash + Σ qty×price`); `GET /api/portfolio` is authoritative on load/after trade | §10 |
| 8 | SSE reconnect → initial state | Stream emits a full snapshot as the first event on every (re)connect | §6 |
| 9 | SQLite concurrent writes | WAL mode + serialized writes (snapshot task vs. request handlers) | §7 |
| 10 | Unknown ticker added to watchlist | Simulator synthesizes a seed price + default GBM params; invalid symbols surfaced as an add error | §6, §8 |
| 11 | Two charting libraries listed | Lightweight Charts for time-series (price + P&L), Recharts for the treemap — one role each | §10 |
| 12 | Three ways to run the app | Dropped the production `docker-compose.yml`; start scripts wrapping `docker run` are the only path (test compose kept) | §4, §11 |
| 13 | `GET /api/watchlist` redundant with SSE | Documented as initial-paint/reconnect snapshot only; SSE owns live updates | §8 |
| 14 | `portfolio_snapshots` 30s cadence | Confirmed intentional — keeps the idle P&L line moving as prices revalue | §7 |
| 15 | Unauthenticated public deploy | Added exposure warning; cloud deploys must sit behind access control | §11 |
| 16 | `chat_messages.actions` shape undefined | Defined JSON shape (trades + watchlist_changes, each with status/error) | §7, §9 |

### Still open (need your call)

- **#2 / #6 scope** — both add a little backend surface (a history ring-buffer + endpoint; a structured-output fallback path). I defaulted to including them because they protect the "polished demo" goal, but if you'd rather keep the first cut minimal, either can be dropped and revisited. Flag if so.
