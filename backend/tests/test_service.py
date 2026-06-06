"""Unit tests for MarketDataService facade."""
import asyncio
import pytest
from app.market.service import MarketDataService
from app.market.base import MarketSource, SymbolError
from app.market.types import Direction


class FakeSource(MarketSource):
    """Scripted fake source for service tests."""
    tick_interval = 0.01

    def __init__(self, price_sequence: list[dict]):
        self._sequence = list(price_sequence)
        self._call_count = 0
        self._started = False

    async def start(self):
        self._started = True

    async def stop(self):
        self._started = False

    async def fetch_prices(self, tickers):
        if self._call_count < len(self._sequence):
            prices = self._sequence[self._call_count]
        else:
            prices = self._sequence[-1] if self._sequence else {}
        self._call_count += 1
        return prices

    async def register_ticker(self, ticker):
        return ticker.upper()

    async def history(self, ticker):
        return [(1000.0, 100.0), (1001.0, 101.0)]


class FakeSourceWithReference(FakeSource):
    async def reference_price(self, ticker):
        return 185.0


@pytest.fixture
def watched():
    return ["AAPL", "GOOGL"]


def make_service(source, watched_list):
    return MarketDataService(
        source=source,
        get_watched_tickers=lambda: watched_list,
        history_max_points=100,
    )


@pytest.mark.asyncio
async def test_start_calls_source_start():
    source = FakeSource([{"AAPL": 190.0}])
    service = make_service(source, ["AAPL"])
    await service.start()
    assert source._started
    await service.stop()


@pytest.mark.asyncio
async def test_stop_calls_source_stop():
    source = FakeSource([{"AAPL": 190.0}])
    service = make_service(source, ["AAPL"])
    await service.start()
    await service.stop()
    assert not source._started


@pytest.mark.asyncio
async def test_subscribe_first_yield_is_snapshot():
    source = FakeSource([{"AAPL": 190.0}])
    service = make_service(source, ["AAPL"])
    await service.start()
    try:
        gen = service.subscribe()
        snapshot = await gen.__anext__()
        assert isinstance(snapshot, list)
    finally:
        await gen.aclose()
        await service.stop()


@pytest.mark.asyncio
async def test_driver_loop_broadcasts_on_change():
    prices = [{"AAPL": 190.0}, {"AAPL": 191.0}]
    source = FakeSource(prices)
    service = make_service(source, ["AAPL"])
    await service.start()

    gen = service.subscribe()
    snapshot = await gen.__anext__()

    await asyncio.sleep(0.05)
    updates = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert any(u.ticker == "AAPL" for u in updates)
    assert any(u.price == 191.0 for u in updates)

    await gen.aclose()
    await service.stop()


@pytest.mark.asyncio
async def test_driver_loop_no_broadcast_on_unchanged_price():
    prices = [{"AAPL": 190.0}, {"AAPL": 190.0}, {"AAPL": 190.0}, {"AAPL": 191.0}]
    source = FakeSource(prices)
    service = make_service(source, ["AAPL"])
    await service.start()

    gen = service.subscribe()
    await gen.__anext__()  # snapshot

    updates = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert all(u.price == 191.0 for u in updates)

    await gen.aclose()
    await service.stop()


@pytest.mark.asyncio
async def test_reference_price_set_on_first_tick():
    source = FakeSource([{"AAPL": 190.0}, {"AAPL": 195.0}])
    service = make_service(source, ["AAPL"])
    await service.start()

    gen = service.subscribe()
    await gen.__anext__()
    await asyncio.sleep(0.05)

    entry = service._cache.get("AAPL")
    assert entry is not None
    assert entry.reference == 190.0

    await gen.aclose()
    await service.stop()


@pytest.mark.asyncio
async def test_reference_price_survives_subsequent_ticks():
    source = FakeSource([{"AAPL": 190.0}, {"AAPL": 200.0}, {"AAPL": 180.0}])
    service = make_service(source, ["AAPL"])
    await service.start()
    await asyncio.sleep(0.05)

    entry = service._cache.get("AAPL")
    assert entry.reference == 190.0

    await service.stop()


@pytest.mark.asyncio
async def test_history_buffer_fills_from_stream():
    prices = [{"AAPL": 190.0 + i * 0.1} for i in range(10)]
    source = FakeSource(prices)
    service = make_service(source, ["AAPL"])
    await service.start()
    await asyncio.sleep(0.15)

    history = service._history.points("AAPL")
    assert len(history) > 0

    await service.stop()


@pytest.mark.asyncio
async def test_get_history_falls_back_to_source():
    source = FakeSource([{"AAPL": 190.0}])
    service = make_service(source, ["AAPL"])
    hist = await service.get_history("AAPL")
    assert hist == [(1000.0, 100.0), (1001.0, 101.0)]


@pytest.mark.asyncio
async def test_get_history_returns_local_when_buffer_has_data():
    source = FakeSource([{"AAPL": 190.0}])
    service = make_service(source, ["AAPL"])
    service._history.append("AAPL", 999.0, 188.0)

    hist = await service.get_history("AAPL")
    assert hist == [(999.0, 188.0)]


@pytest.mark.asyncio
async def test_add_ticker_calls_register():
    source = FakeSource([])
    service = make_service(source, [])
    canonical = await service.add_ticker("aapl")
    assert canonical == "AAPL"


@pytest.mark.asyncio
async def test_add_ticker_propagates_symbol_error():
    class RejectingSource(FakeSource):
        async def register_ticker(self, ticker):
            raise SymbolError("Bad ticker")

    source = RejectingSource([])
    service = make_service(source, [])
    with pytest.raises(SymbolError):
        await service.add_ticker("NOTREAL")


@pytest.mark.asyncio
async def test_maybe_seed_reference_called_for_massive_like_source():
    source = FakeSourceWithReference([])
    service = make_service(source, [])
    await service._maybe_seed_reference("AAPL")
    entry = service._cache.get("AAPL")
    assert entry is not None
    assert entry.reference == 185.0


@pytest.mark.asyncio
async def test_maybe_seed_reference_noop_for_simulator_like_source():
    source = FakeSource([])
    service = make_service(source, [])
    await service._maybe_seed_reference("AAPL")
    assert service._cache.get("AAPL") is None


@pytest.mark.asyncio
async def test_current_snapshot_returns_dict():
    source = FakeSource([{"AAPL": 190.0}])
    service = make_service(source, ["AAPL"])
    await service.start()
    await asyncio.sleep(0.05)

    snap = service.current_snapshot()
    assert "AAPL" in snap
    assert snap["AAPL"]["price"] == 190.0

    await service.stop()


@pytest.mark.asyncio
async def test_subscriber_queue_overflow_drops_oldest():
    """QueueFull should not raise; oldest item gets dropped."""
    source = FakeSource([{"AAPL": 190.0 + i} for i in range(300)])
    service = make_service(source, ["AAPL"])
    await service.start()

    gen = service.subscribe()
    await gen.__anext__()

    await asyncio.sleep(0.5)

    updates = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert updates is not None

    await gen.aclose()
    await service.stop()


@pytest.mark.asyncio
async def test_multiple_subscribers():
    prices = [{"AAPL": 190.0}, {"AAPL": 191.0}]
    source = FakeSource(prices)
    service = make_service(source, ["AAPL"])
    await service.start()

    gen1 = service.subscribe()
    gen2 = service.subscribe()

    s1 = await gen1.__anext__()
    s2 = await gen2.__anext__()

    await asyncio.sleep(0.05)

    u1 = await asyncio.wait_for(gen1.__anext__(), timeout=1.0)
    u2 = await asyncio.wait_for(gen2.__anext__(), timeout=1.0)
    assert any(u.price == 191.0 for u in u1)
    assert any(u.price == 191.0 for u in u2)

    await gen1.aclose()
    await gen2.aclose()
    await service.stop()
