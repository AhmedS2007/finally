"""Interface conformance tests — same suite run against SimulatedSource and MassiveSource."""
import json
import pytest
import httpx
from app.market.base import MarketSource, SymbolError
from app.market.sim_source import SimulatedSource


SNAPSHOT_KNOWN = {
    "status": "OK",
    "results": [
        {"ticker": "AAPL", "last_trade": {"price": 191.0}, "session": {"close": 191.0}},
    ],
}

SNAPSHOT_UNKNOWN = {"status": "OK", "results": []}


def make_massive_source():
    from app.market.massive_source import MassiveSource
    from app.market.massive import MassiveClient

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            url = str(request.url)
            if "/v3/snapshot" in url:
                if "NOTREAL" in url or "NOTREAL" in str(request.url.params):
                    body = SNAPSHOT_UNKNOWN
                else:
                    body = SNAPSHOT_KNOWN
            elif "/v2/aggs" in url:
                body = {"status": "OK", "results": [
                    {"t": 1000000, "c": 191.0}
                ]}
            else:
                body = {"status": "OK", "results": []}
            return httpx.Response(200, content=json.dumps(body).encode(),
                                  headers={"content-type": "application/json"})

    source = MassiveSource.__new__(MassiveSource)
    source.tick_interval = 0.5
    source._poll_interval = 999.0
    source._latest = {"AAPL": 191.0}
    source._watched = []
    source._known = set()
    source._poll_task = None
    client = MassiveClient.__new__(MassiveClient)
    client._base_url = "https://api.polygon.io"
    client._client = httpx.AsyncClient(
        base_url="https://api.polygon.io",
        headers={"Authorization": "Bearer test"},
        transport=MockTransport(),
    )
    source._client = client
    return source


@pytest.fixture(params=["sim", "massive"])
async def source(request):
    if request.param == "sim":
        s = SimulatedSource(seed=42)
    else:
        s = make_massive_source()
    await s.start()
    yield s
    await s.stop()


@pytest.mark.asyncio
async def test_is_market_source_instance(source):
    assert isinstance(source, MarketSource)


@pytest.mark.asyncio
async def test_fetch_prices_returns_floats_for_known_tickers(source):
    prices = await source.fetch_prices(["AAPL"])
    assert "AAPL" in prices
    assert isinstance(prices["AAPL"], float)


@pytest.mark.asyncio
async def test_fetch_prices_never_raises_on_empty_input(source):
    prices = await source.fetch_prices([])
    assert isinstance(prices, dict)


@pytest.mark.asyncio
async def test_register_ticker_returns_uppercase(source):
    canonical = await source.register_ticker("aapl")
    assert canonical == "AAPL"


@pytest.mark.asyncio
async def test_register_known_ticker_succeeds(source):
    result = await source.register_ticker("AAPL")
    assert result == "AAPL"


@pytest.mark.asyncio
async def test_history_returns_list(source):
    result = await source.history("AAPL")
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_history_tuples_are_ascending(source):
    result = await source.history("AAPL")
    if len(result) > 1:
        timestamps = [t for t, _ in result]
        assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_sim_register_unknown_always_succeeds():
    sim = SimulatedSource(seed=0)
    await sim.start()
    canonical = await sim.register_ticker("NOTREAL")
    assert canonical == "NOTREAL"
    prices = await sim.fetch_prices(["NOTREAL"])
    assert prices["NOTREAL"] > 0
    await sim.stop()


@pytest.mark.asyncio
async def test_massive_register_unknown_raises_symbol_error():
    source = make_massive_source()
    await source.start()
    with pytest.raises(SymbolError):
        await source.register_ticker("NOTREAL")
    await source.stop()
