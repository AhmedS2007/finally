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
        canonical = await market.add_ticker(body.ticker)
    except SymbolError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db = getattr(request.app.state, "db", None)
    if db is not None:
        db.add_watchlist_ticker(canonical)
    return {"ticker": canonical}


@router.get("/api/watchlist")
async def get_watchlist(request: Request):
    market = request.app.state.market
    snapshot = market.current_snapshot()
    db = getattr(request.app.state, "db", None)
    if db is not None:
        tickers = db.list_watchlist()
    else:
        tickers = list(snapshot.keys())
    return {
        "watchlist": [
            snapshot.get(t, {"ticker": t, "price": None, "change_pct": None})
            for t in tickers
        ]
    }


@router.delete("/api/watchlist/{ticker}")
async def remove_watchlist(ticker: str, request: Request):
    db = getattr(request.app.state, "db", None)
    if db is not None:
        db.remove_watchlist_ticker(ticker.upper())
    return {"ticker": ticker.upper()}
