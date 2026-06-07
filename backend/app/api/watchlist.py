from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..market import SymbolError

router = APIRouter()


class AddTicker(BaseModel):
    ticker: str


def _watched_tickers(request: Request) -> list[str]:
    """Current watchlist symbols, from the DB if wired else the in-memory fallback."""
    db = getattr(request.app.state, "db", None)
    if db is not None:
        return db.list_watchlist()
    return list(getattr(request.app.state, "watchlist", []))


@router.post("/api/watchlist")
async def add_watchlist(body: AddTicker, request: Request):
    market = request.app.state.market
    try:
        canonical = await market.add_ticker(body.ticker)
    except SymbolError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    db = getattr(request.app.state, "db", None)
    if db is not None:
        db.add_watchlist_ticker(canonical)
    else:
        wl = request.app.state.watchlist
        if canonical not in wl:
            wl.append(canonical)
    return {"ticker": canonical}


@router.get("/api/watchlist")
async def get_watchlist(request: Request):
    market = request.app.state.market
    snapshot = market.current_snapshot()
    tickers = _watched_tickers(request)
    return {
        "watchlist": [
            snapshot.get(t, {"ticker": t, "price": None, "change_pct": None})
            for t in tickers
        ]
    }


@router.delete("/api/watchlist/{ticker}")
async def remove_watchlist(ticker: str, request: Request):
    canonical = ticker.upper()
    db = getattr(request.app.state, "db", None)
    if db is not None:
        db.remove_watchlist_ticker(canonical)
    else:
        wl = request.app.state.watchlist
        if canonical in wl:
            wl.remove(canonical)
    # Evict from the price cache + history so it stops appearing in reconnect snapshots.
    request.app.state.market.remove_ticker(canonical)
    return {"ticker": canonical}
