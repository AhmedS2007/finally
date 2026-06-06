"""Unit tests for MassiveSource (adapter layer)."""
import asyncio
import json
import pytest
import httpx
from app.market.massive_source import MassiveSource
from app.market.base import SymbolError


SNAPSHOT_OK = {
    "status": "OK",
    "results": [
        {"ticker": "AAPL", "last_trade": {"price": 191.0}, "session": {"close": 191.0}},
        {"ticker": "GOOGL", "last_trade": {"price": 175.0}, "session": {"close": 175.0}},
    ],
}

SNAPSHOT_UNKNOWN = {
    "status": "OK",
    "results": [],
}

NOT_AUTHORIZED = {
    "status": "NOT_AUTHORIZED",
    "message": "You are not entitled.",
}


def make_mock_transport(routes: dict):
    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            for pattern, response_data in routes.items():
                if pattern in url:
                    if isinstance(response_data, tuple):
                        status_code, body = response_data
                    else:
                        status_code, body = 200, response_data
                    return httpx.Response(
                        status_code,
                        content=json.dumps(body).encode(),
                        headers={"content-type": "application/json"},
                    )
            return httpx.Response(404, content=b'{}')
    return MockTransport()


def make_source(routes: dict) -> MassiveSource:
    source = MassiveSource.__new__(MassiveSource)
    source.tick_interval = 0.5
    source._poll_interval = 15.0
    source._latest = {}
    source._watched = []
    source._known = set()
    source._poll_task = None
    from app.market.massive import MassiveClient
    client = MassiveClient.__new__(MassiveClient)
    client._base_url = "https://api.polygon.io"
    client._client = httpx.AsyncClient(
        base_url="https://api.polygon.io",
        headers={"Authorization": "Bearer test"},
        transport=make_mock_transport(routes),
    )
    source._client = client
    return source


@pytest.mark.asyncio
async def test_fetch_prices_returns_cached_latest():
    source = make_source({"/v3/snapshot": SNAPSHOT_OK})
    source._latest = {"AAPL": 191.0, "GOOGL": 175.0}
    prices = await source.fetch_prices(["AAPL", "GOOGL"])
    assert prices == {"AAPL": 191.0, "GOOGL": 175.0}
    await source._client.aclose()


@pytest.mark.asyncio
async def test_fetch_prices_does_no_network_io():
    """fetch_prices must not call the network; only the poll loop does."""
    call_count = 0

    class CountingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, content=json.dumps(SNAPSHOT_OK).encode(),
                                  headers={"content-type": "application/json"})

    source = MassiveSource.__new__(MassiveSource)
    source.tick_interval = 0.5
    source._poll_interval = 15.0
    source._latest = {"AAPL": 190.0}
    source._watched = []
    source._known = set()
    source._poll_task = None
    from app.market.massive import MassiveClient
    client = MassiveClient.__new__(MassiveClient)
    client._base_url = "https://api.polygon.io"
    client._client = httpx.AsyncClient(
        base_url="https://api.polygon.io",
        headers={"Authorization": "Bearer test"},
        transport=CountingTransport(),
    )
    source._client = client

    await source.fetch_prices(["AAPL"])
    assert call_count == 0
    await source._client.aclose()


@pytest.mark.asyncio
async def test_register_ticker_succeeds_for_known_symbol():
    source = make_source({"/v3/snapshot": SNAPSHOT_OK})
    canonical = await source.register_ticker("AAPL")
    assert canonical == "AAPL"
    await source._client.aclose()


@pytest.mark.asyncio
async def test_register_ticker_raises_for_unknown_symbol():
    source = make_source({"/v3/snapshot": SNAPSHOT_UNKNOWN})
    with pytest.raises(SymbolError):
        await source.register_ticker("NOTREAL")
    await source._client.aclose()


@pytest.mark.asyncio
async def test_register_ticker_raises_on_not_authorized():
    source = make_source({"/v3/snapshot": (403, NOT_AUTHORIZED)})
    with pytest.raises(SymbolError):
        await source.register_ticker("AAPL")
    await source._client.aclose()


@pytest.mark.asyncio
async def test_register_ticker_primes_latest_cache():
    source = make_source({"/v3/snapshot": SNAPSHOT_OK})
    await source.register_ticker("AAPL")
    assert "AAPL" in source._latest
    await source._client.aclose()


@pytest.mark.asyncio
async def test_register_ticker_skips_if_already_known():
    source = make_source({"/v3/snapshot": SNAPSHOT_OK})
    source._known.add("AAPL")
    canonical = await source.register_ticker("AAPL")
    assert canonical == "AAPL"
    await source._client.aclose()


@pytest.mark.asyncio
async def test_history_returns_list_of_tuples():
    agg_data = {
        "status": "OK",
        "results": [
            {"t": 1000000, "c": 190.0},
            {"t": 2000000, "c": 191.0},
        ],
    }
    source = make_source({"/v2/aggs/ticker/AAPL": agg_data})
    hist = await source.history("AAPL")
    assert len(hist) == 2
    assert hist[0] == (1000.0, 190.0)
    assert hist[1] == (2000.0, 191.0)
    await source._client.aclose()


@pytest.mark.asyncio
async def test_history_returns_empty_on_error():
    source = make_source({"/v2/aggs/ticker/AAPL": (500, {})})
    hist = await source.history("AAPL")
    assert hist == []
    await source._client.aclose()


@pytest.mark.asyncio
async def test_poll_loop_updates_latest_and_stops():
    source = make_source({"/v3/snapshot": SNAPSHOT_OK})
    source._watched = ["AAPL", "GOOGL"]
    source._poll_interval = 999.0

    poll_task = asyncio.create_task(source._poll_loop())
    await asyncio.sleep(0.1)
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass

    assert "AAPL" in source._latest
    assert source._latest["AAPL"] == 191.0
    await source._client.aclose()


@pytest.mark.asyncio
async def test_poll_loop_survives_network_error():
    """A failed poll must not crash the loop."""
    call_count = 0

    class FailingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("simulated failure")

    source = MassiveSource.__new__(MassiveSource)
    source.tick_interval = 0.5
    source._poll_interval = 999.0
    source._latest = {"AAPL": 100.0}
    source._watched = ["AAPL"]
    source._known = set()
    source._poll_task = None
    from app.market.massive import MassiveClient
    client = MassiveClient.__new__(MassiveClient)
    client._base_url = "https://api.polygon.io"
    client._client = httpx.AsyncClient(
        base_url="https://api.polygon.io",
        headers={"Authorization": "Bearer test"},
        transport=FailingTransport(),
    )
    source._client = client

    poll_task = asyncio.create_task(source._poll_loop())
    await asyncio.sleep(0.1)
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass

    assert source._latest["AAPL"] == 100.0
    await source._client.aclose()


@pytest.mark.asyncio
async def test_reference_price_returns_close():
    prev_close_data = {
        "status": "OK",
        "results": [{"c": 188.5, "o": 187.0, "h": 190.0, "l": 186.0, "t": 1000}],
    }
    source = make_source({"/v2/aggs/ticker/AAPL/prev": prev_close_data})
    ref = await source.reference_price("AAPL")
    assert ref == 188.5
    await source._client.aclose()


@pytest.mark.asyncio
async def test_reference_price_returns_none_on_error():
    source = make_source({"/v2/aggs/ticker/AAPL/prev": (500, {})})
    ref = await source.reference_price("AAPL")
    assert ref is None
    await source._client.aclose()
