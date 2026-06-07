from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .market import MarketConfig, MarketDataService, build_market_source
from .api import stream, prices, watchlist

DEFAULT_WATCHLIST = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = MarketConfig.from_env()
    app.state.market_config = config

    # In-memory watchlist fallback used until the DB/persistence component is wired.
    # Shared with the route handlers via app.state so add/remove affect streaming.
    app.state.watchlist = list(DEFAULT_WATCHLIST)

    def get_watched() -> list[str]:
        db = getattr(app.state, "db", None)
        if db is not None:
            return db.list_watchlist()
        return list(app.state.watchlist)

    source = build_market_source(config)
    market = MarketDataService(
        source=source,
        get_watched_tickers=get_watched,
        history_max_points=config.history_max_points,
    )
    app.state.market = market
    await market.start()
    try:
        yield
    finally:
        await market.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)
app.include_router(stream.router)
app.include_router(prices.router)
app.include_router(watchlist.router)


@app.get("/api/health")
async def health():
    return JSONResponse({"status": "ok"})
