"""Unit tests for MassiveClient using httpx mock transport."""
import json
import pytest
import httpx
from app.market.massive import MassiveClient, MassiveError, _extract_price, _chunks


SNAPSHOT_RESPONSE = {
    "status": "OK",
    "results": [
        {
            "ticker": "AAPL",
            "last_trade": {"price": 191.23, "size": 100},
            "last_quote": {"bid": 191.22, "ask": 191.24},
            "session": {"open": 189.90, "close": 191.23, "high": 192.10, "low": 189.50},
        },
        {
            "ticker": "GOOGL",
            "last_trade": {"price": 175.50},
            "last_quote": {"bid": 175.49, "ask": 175.51},
            "session": {"open": 174.00, "close": 175.50, "high": 176.00, "low": 173.80},
        },
    ],
}

SNAPSHOT_NO_LAST_TRADE = {
    "status": "OK",
    "results": [
        {
            "ticker": "THIN",
            "last_trade": {},
            "session": {"close": 50.0},
            "last_quote": {"bid": 49.9, "ask": 50.1},
        },
    ],
}

AGGREGATES_RESPONSE = {
    "ticker": "AAPL",
    "status": "OK",
    "results": [
        {"t": 1577941200000, "o": 74.06, "h": 75.15, "l": 73.79, "c": 75.08, "v": 100},
        {"t": 1578027600000, "o": 74.28, "h": 75.14, "l": 74.12, "c": 74.35, "v": 100},
    ],
}

PREV_CLOSE_RESPONSE = {
    "ticker": "AAPL",
    "status": "OK",
    "results": [{"c": 189.50, "o": 188.0, "h": 191.0, "l": 187.5, "t": 1717603200000}],
}

NOT_AUTHORIZED_RESPONSE = {
    "status": "NOT_AUTHORIZED",
    "message": "You are not entitled to this data.",
}


def make_mock_transport(routes: dict):
    """Create an httpx MockTransport that maps URL patterns to responses."""
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
            return httpx.Response(404, content=b'{"error": "not found"}')
    return MockTransport()


def make_client(transport) -> MassiveClient:
    client = MassiveClient.__new__(MassiveClient)
    client._base_url = "https://api.polygon.io"
    client._client = httpx.AsyncClient(
        base_url="https://api.polygon.io",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )
    return client


@pytest.mark.asyncio
async def test_latest_prices_parses_last_trade():
    transport = make_mock_transport({"/v3/snapshot": SNAPSHOT_RESPONSE})
    client = make_client(transport)
    prices = await client.latest_prices(["AAPL", "GOOGL"])
    assert prices["AAPL"] == 191.23
    assert prices["GOOGL"] == 175.50
    await client.aclose()


@pytest.mark.asyncio
async def test_latest_prices_falls_back_to_session_close():
    transport = make_mock_transport({"/v3/snapshot": SNAPSHOT_NO_LAST_TRADE})
    client = make_client(transport)
    prices = await client.latest_prices(["THIN"])
    assert prices["THIN"] == 50.0
    await client.aclose()


@pytest.mark.asyncio
async def test_latest_prices_falls_back_to_quote_mid():
    no_last_no_session = {
        "status": "OK",
        "results": [
            {
                "ticker": "MID",
                "last_trade": {},
                "session": {},
                "last_quote": {"bid": 99.0, "ask": 101.0},
            }
        ],
    }
    transport = make_mock_transport({"/v3/snapshot": no_last_no_session})
    client = make_client(transport)
    prices = await client.latest_prices(["MID"])
    assert prices["MID"] == 100.0
    await client.aclose()


@pytest.mark.asyncio
async def test_raises_massive_error_on_401():
    transport = make_mock_transport({"/v3/snapshot": (401, NOT_AUTHORIZED_RESPONSE)})
    client = make_client(transport)
    with pytest.raises(MassiveError):
        await client.latest_prices(["AAPL"])
    await client.aclose()


@pytest.mark.asyncio
async def test_raises_massive_error_on_403():
    transport = make_mock_transport({"/v3/snapshot": (403, NOT_AUTHORIZED_RESPONSE)})
    client = make_client(transport)
    with pytest.raises(MassiveError):
        await client.latest_prices(["AAPL"])
    await client.aclose()


@pytest.mark.asyncio
async def test_history_maps_bars_to_tuples():
    transport = make_mock_transport({"/v2/aggs/ticker/AAPL": AGGREGATES_RESPONSE})
    client = make_client(transport)
    bars = await client.history("AAPL", from_="2020-01-01", to="2020-01-02")
    assert len(bars) == 2
    assert bars[0] == (1577941200.0, 75.08)
    assert bars[1] == (1578027600.0, 74.35)
    await client.aclose()


@pytest.mark.asyncio
async def test_history_returns_empty_on_no_results():
    transport = make_mock_transport({
        "/v2/aggs/ticker/AAPL": {"status": "OK", "results": []}
    })
    client = make_client(transport)
    bars = await client.history("AAPL", from_="2020-01-01", to="2020-01-02")
    assert bars == []
    await client.aclose()


@pytest.mark.asyncio
async def test_previous_close_returns_close_price():
    transport = make_mock_transport({"/v2/aggs/ticker/AAPL/prev": PREV_CLOSE_RESPONSE})
    client = make_client(transport)
    close = await client.previous_close("AAPL")
    assert close == 189.50
    await client.aclose()


@pytest.mark.asyncio
async def test_previous_close_returns_none_on_empty():
    transport = make_mock_transport({
        "/v2/aggs/ticker/AAPL/prev": {"status": "OK", "results": []}
    })
    client = make_client(transport)
    close = await client.previous_close("AAPL")
    assert close is None
    await client.aclose()


def test_extract_price_last_trade():
    assert _extract_price({"last_trade": {"price": 100.0}}) == 100.0


def test_extract_price_session_close():
    assert _extract_price({"last_trade": {}, "session": {"close": 99.0}}) == 99.0


def test_extract_price_quote_mid():
    result = {"last_trade": {}, "session": {}, "last_quote": {"bid": 98.0, "ask": 102.0}}
    assert _extract_price(result) == 100.0


def test_extract_price_none_when_no_data():
    assert _extract_price({}) is None


def test_chunks():
    result = list(_chunks(["A", "B", "C", "D", "E"], 2))
    assert result == [["A", "B"], ["C", "D"], ["E"]]


def test_massive_client_raises_on_empty_key():
    with pytest.raises(ValueError):
        MassiveClient(api_key="")
