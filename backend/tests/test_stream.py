"""Tests for the SSE route generator and heartbeat merge (PLAN.md §6, §8).

These exercise the real route code path (subscribe -> heartbeat merge -> SSE
framing -> teardown) without TestClient, whose portal deadlocks on an infinite
streaming response.
"""
import asyncio
import json
import types

import pytest

from app.api.stream import _sse, _merge_with_heartbeat, stream_prices
from app.market.service import MarketDataService
from app.market.sim_source import SimulatedSource


def test_sse_formats_data_frame():
    assert _sse("hello") == "data: hello\n\n"


def test_sse_formats_named_event():
    assert _sse("hello", event="tick") == "event: tick\ndata: hello\n\n"


@pytest.mark.asyncio
async def test_merge_emits_heartbeat_when_idle():
    async def src():
        yield ["first"]
        await asyncio.Event().wait()  # then go quiet forever

    merged = _merge_with_heartbeat(src(), interval=0.05)
    first = await merged.__anext__()
    assert first == ["first"]
    beat = await asyncio.wait_for(merged.__anext__(), timeout=1.0)
    assert beat is None  # heartbeat sentinel
    await merged.aclose()


@pytest.mark.asyncio
async def test_merge_stops_when_source_exhausts():
    async def src():
        yield ["only"]

    items = [x async for x in _merge_with_heartbeat(src(), interval=1.0)]
    assert items == [["only"]]


@pytest.mark.asyncio
async def test_merge_teardown_does_not_raise():
    """aclose() after cancelling the in-flight __anext__ must settle cleanly
    (regression guard for the disconnect teardown race)."""
    closed = False

    async def src():
        nonlocal closed
        try:
            while True:
                await asyncio.Event().wait()
                yield ["x"]
        finally:
            closed = True

    merged = _merge_with_heartbeat(src(), interval=0.05)
    # advance once so the heartbeat path is active with a pending __anext__
    await asyncio.wait_for(merged.__anext__(), timeout=1.0)
    await merged.aclose()  # should not raise "async generator is already running"
    assert closed


def _fake_request(app):
    req = types.SimpleNamespace()
    req.app = app

    async def is_disconnected():
        return False

    req.is_disconnected = is_disconnected
    return req


@pytest.fixture
async def app_with_market():
    service = MarketDataService(
        source=SimulatedSource(seed=1, tick_interval=0.02),
        get_watched_tickers=lambda: ["AAPL", "GOOGL"],
        history_max_points=100,
    )
    await service.start()
    await asyncio.sleep(0.1)  # warm the cache
    app = types.SimpleNamespace()
    app.state = types.SimpleNamespace(
        market=service,
        market_config=types.SimpleNamespace(heartbeat_interval=15.0),
    )
    yield app
    await service.stop()


@pytest.mark.asyncio
async def test_route_emits_snapshot_first(app_with_market):
    resp = await stream_prices(_fake_request(app_with_market))
    assert resp.media_type == "text/event-stream"

    agen = resp.body_iterator
    first = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
    assert first.startswith("data:")
    payload = json.loads(first[len("data:"):].strip())
    assert isinstance(payload, list)
    tickers = {u["ticker"] for u in payload}
    assert "AAPL" in tickers
    assert {"ticker", "price", "previous_price", "timestamp", "direction"} <= set(payload[0])
    await agen.aclose()


@pytest.mark.asyncio
async def test_route_stream_has_no_cache_headers(app_with_market):
    resp = await stream_prices(_fake_request(app_with_market))
    assert resp.headers["Cache-Control"] == "no-cache"
    assert resp.headers["X-Accel-Buffering"] == "no"
    await resp.body_iterator.aclose()
