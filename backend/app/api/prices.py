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
