# FinAlly — AI Trading Workstation

A visually rich, AI-powered trading workstation: it streams live market data, simulates portfolio trading, and ships an LLM chat assistant that can analyze positions and execute trades from natural language.

Built entirely by coding agents as the capstone for an agentic AI coding course.

## Features

- **Live price streaming** over SSE, with green/red flash animations
- **Simulated portfolio** — $10k virtual cash, market orders, instant fills
- **Portfolio visualizations** — treemap heatmap, P&L chart, positions table
- **AI chat assistant** — analyzes holdings and auto-executes trades
- **Watchlist management** — track tickers manually or via the AI
- **Dark, Bloomberg-inspired terminal** aesthetic

## Architecture

A single Docker container serves everything on port 8000:

- **Frontend** — Next.js static export (TypeScript, Tailwind CSS)
- **Backend** — FastAPI (Python / uv) with SSE streaming
- **Database** — SQLite, lazily initialized, volume-mounted
- **AI** — LiteLLM → OpenRouter (Cerebras inference), structured outputs
- **Market data** — built-in GBM simulator (default) or Massive API (optional)

## Quick Start

```bash
cp .env.example .env          # then add your OPENROUTER_API_KEY

docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
# open http://localhost:8000
```

On Windows use `scripts/start_windows.ps1`; on macOS/Linux use `scripts/start_mac.sh`.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter key for the AI chat |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data; omit to use the simulator |
| `LLM_MOCK` | No | Set `true` for deterministic mock LLM responses (testing) |

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project (market data subsystem complete)
├── planning/    # Project spec and agent contracts (see PLAN.md)
├── test/        # Playwright E2E tests
├── db/          # SQLite volume mount (runtime)
└── scripts/     # Start/stop helpers
```

## Status

The market-data backend is complete and tested — see `planning/MARKET_DATA_SUMMARY.md`. The full specification lives in `planning/PLAN.md`; remaining platform work (portfolio, chat, frontend) is in progress.

## License

See [LICENSE](LICENSE).
