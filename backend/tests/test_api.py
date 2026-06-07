"""Route-level tests for the market-data HTTP surface (PLAN.md §8, §12).

Exercises the FastAPI app end-to-end with the simulator source via TestClient
(which runs the lifespan, so the driver loop populates the cache).
"""
import pytest
from starlette.testclient import TestClient

from app.market.base import SymbolError


@pytest.fixture
def client(monkeypatch):
    # Force the simulator path with a fast, deterministic tick.
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setenv("MARKET_TICK_INTERVAL", "0.02")
    monkeypatch.setenv("SIM_SEED", "1")
    from app.main import app
    with TestClient(app) as c:
        # let the driver loop populate the cache before assertions
        import time
        time.sleep(0.1)
        yield c


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_watchlist_returns_default_tickers(client):
    resp = client.get("/api/watchlist")
    assert resp.status_code == 200
    tickers = {row["ticker"] for row in resp.json()["watchlist"]}
    assert {"AAPL", "GOOGL", "MSFT"} <= tickers


def test_watchlist_rows_have_live_prices(client):
    rows = client.get("/api/watchlist").json()["watchlist"]
    aapl = next(r for r in rows if r["ticker"] == "AAPL")
    assert aapl["price"] is not None
    assert aapl["price"] > 0


def test_add_ticker_appears_in_watchlist_and_streams(client):
    resp = client.post("/api/watchlist", json={"ticker": "pltr"})
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "PLTR"

    # now watched; after a few ticks it should have a price in the snapshot
    import time
    time.sleep(0.1)
    rows = client.get("/api/watchlist").json()["watchlist"]
    pltr = next((r for r in rows if r["ticker"] == "PLTR"), None)
    assert pltr is not None
    assert pltr["price"] is not None


def test_remove_ticker_evicts_from_watchlist_and_cache(client):
    client.post("/api/watchlist", json={"ticker": "SNOW"})
    import time
    time.sleep(0.05)

    resp = client.delete("/api/watchlist/snow")
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "SNOW"

    rows = client.get("/api/watchlist").json()["watchlist"]
    assert all(r["ticker"] != "SNOW" for r in rows)


def test_history_endpoint_returns_points(client):
    resp = client.get("/api/prices/AAPL/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert isinstance(body["points"], list)
    assert len(body["points"]) > 0
    assert {"t", "p"} <= set(body["points"][0].keys())


def test_history_ticker_is_uppercased(client):
    body = client.get("/api/prices/aapl/history").json()
    assert body["ticker"] == "AAPL"


def test_add_invalid_symbol_returns_422(client):
    """A source that rejects the symbol surfaces as HTTP 422."""
    class _Rejecting:
        async def add_ticker(self, ticker):
            raise SymbolError("Unknown or unsupported symbol")
        def current_snapshot(self):
            return {}
        def remove_ticker(self, ticker):
            pass

    client.app.state.market = _Rejecting()
    resp = client.post("/api/watchlist", json={"ticker": "NOTAREALSYM"})
    assert resp.status_code == 422
    assert "symbol" in resp.json()["detail"].lower()
