"""Unit tests for SimulatedSource."""
import math
import pytest
from app.market.sim_source import SimulatedSource, SECONDS_PER_TRADING_YEAR, VOL_SCALE
from app.market.sim_params import SEED


@pytest.fixture
def sim():
    return SimulatedSource(seed=42)


@pytest.mark.asyncio
async def test_fetch_prices_returns_all_known_tickers(sim):
    tickers = list(SEED.keys())
    prices = await sim.fetch_prices(tickers)
    assert set(prices.keys()) == set(tickers)


@pytest.mark.asyncio
async def test_fetch_prices_all_positive(sim):
    prices = await sim.fetch_prices(list(SEED.keys()))
    assert all(p > 0 for p in prices.values())


@pytest.mark.asyncio
async def test_determinism():
    sim1 = SimulatedSource(seed=42)
    sim2 = SimulatedSource(seed=42)
    tickers = ["AAPL", "GOOGL", "MSFT"]
    for _ in range(5):
        p1 = await sim1.fetch_prices(tickers)
        p2 = await sim2.fetch_prices(tickers)
        assert p1 == p2


@pytest.mark.asyncio
async def test_different_seeds_produce_different_sequences():
    sim1 = SimulatedSource(seed=1)
    sim2 = SimulatedSource(seed=2)
    prices1 = await sim1.fetch_prices(["AAPL"])
    prices2 = await sim2.fetch_prices(["AAPL"])
    assert prices1["AAPL"] != prices2["AAPL"]


@pytest.mark.asyncio
async def test_register_unknown_ticker_succeeds(sim):
    canonical = await sim.register_ticker("ZZZZ")
    assert canonical == "ZZZZ"


@pytest.mark.asyncio
async def test_register_unknown_ticker_streams_positive_price(sim):
    await sim.register_ticker("ZZZZ")
    prices = await sim.fetch_prices(["ZZZZ"])
    assert "ZZZZ" in prices
    assert prices["ZZZZ"] > 0


@pytest.mark.asyncio
async def test_register_ticker_uppercases(sim):
    canonical = await sim.register_ticker("aapl")
    assert canonical == "AAPL"


@pytest.mark.asyncio
async def test_register_existing_ticker_is_noop(sim):
    await sim.register_ticker("AAPL")
    await sim.register_ticker("AAPL")
    assert "AAPL" in sim._params


@pytest.mark.asyncio
async def test_start_stop_noop(sim):
    await sim.start()
    await sim.stop()


@pytest.mark.asyncio
async def test_history_returns_list(sim):
    result = await sim.history("AAPL")
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_synth_history_returns_ascending_timestamps(sim):
    history = sim.synth_history("AAPL", points=10)
    assert len(history) == 10
    timestamps = [t for t, _ in history]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_synth_history_prices_all_positive(sim):
    history = sim.synth_history("AAPL", points=20)
    assert all(p > 0 for _, p in history)


@pytest.mark.asyncio
async def test_synth_history_empty_for_unknown(sim):
    result = sim.synth_history("NOTREGISTERED")
    assert result == []


@pytest.mark.asyncio
async def test_gbm_statistical_validity():
    """With VOL_SCALE=1 and many ticks, mean/std of log-returns within tolerance.

    Events and cent-rounding are disabled here: at VOL_SCALE=1 the per-tick GBM move
    is sub-cent, so the rare 2-5% events and 0.01 rounding would otherwise dominate
    the statistics. Both are demo knobs, off here to test the underlying GBM.
    """
    sim = SimulatedSource(seed=0, enable_events=False, round_digits=None)

    # Monkey-patch VOL_SCALE temporarily
    import app.market.sim_source as ss
    original_vol_scale = ss.VOL_SCALE
    ss.VOL_SCALE = 1.0

    tickers = ["AAPL"]
    prices = []
    start_price = sim._price["AAPL"]

    for _ in range(2000):
        p = await sim.fetch_prices(tickers)
        prices.append(p["AAPL"])

    ss.VOL_SCALE = original_vol_scale

    log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    mu = SEED["AAPL"].drift
    sigma = SEED["AAPL"].volatility
    dt = 0.5 / SECONDS_PER_TRADING_YEAR

    expected_mean = (mu - 0.5 * sigma ** 2) * dt
    expected_std = sigma * math.sqrt(dt)

    actual_mean = sum(log_returns) / len(log_returns)
    variance = sum((r - actual_mean) ** 2 for r in log_returns) / len(log_returns)
    actual_std = math.sqrt(variance)

    # Allow generous tolerance for statistical test
    assert abs(actual_mean - expected_mean) < expected_std * 3
    assert abs(actual_std - expected_std) / expected_std < 0.3


@pytest.mark.asyncio
async def test_sector_correlation():
    """Same-sector tickers have positively correlated returns over many ticks.

    Events are disabled: they are large, per-ticker idiosyncratic jumps whose variance
    swamps the small correlated GBM move, masking the sector co-movement under test.
    """
    sim = SimulatedSource(seed=99, enable_events=False)
    tech_tickers = ["AAPL", "GOOGL", "MSFT"]
    n = 500

    prev_prices = {t: sim._price[t] for t in tech_tickers}
    returns = {t: [] for t in tech_tickers}

    for _ in range(n):
        prices = await sim.fetch_prices(tech_tickers)
        for t in tech_tickers:
            returns[t].append(math.log(prices[t] / prev_prices[t]))
            prev_prices[t] = prices[t]

    def corr(a, b):
        n = len(a)
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
        std_a = math.sqrt(sum((x - mean_a) ** 2 for x in a) / n)
        std_b = math.sqrt(sum((x - mean_b) ** 2 for x in b) / n)
        return cov / (std_a * std_b) if std_a and std_b else 0

    r_ab = corr(returns["AAPL"], returns["GOOGL"])
    r_ac = corr(returns["AAPL"], returns["MSFT"])
    assert r_ab > 0.3, f"AAPL-GOOGL correlation too low: {r_ab}"
    assert r_ac > 0.3, f"AAPL-MSFT correlation too low: {r_ac}"


@pytest.mark.asyncio
async def test_prices_never_go_non_positive(sim):
    tickers = list(SEED.keys())
    for _ in range(100):
        prices = await sim.fetch_prices(tickers)
        assert all(p > 0 for p in prices.values())


@pytest.mark.asyncio
async def test_events_disabled_produces_no_large_jumps():
    """With events off, no single tick should move a price by >=2%."""
    sim = SimulatedSource(seed=7, enable_events=False)
    tickers = ["AAPL"]
    prev = sim._price["AAPL"]
    for _ in range(2000):
        p = await sim.fetch_prices(tickers)
        move = abs(p["AAPL"] / prev - 1.0)
        assert move < 0.02, f"unexpected jump of {move:.4f} with events disabled"
        prev = p["AAPL"]


@pytest.mark.asyncio
async def test_round_digits_none_allows_subcent_prices():
    """round_digits=None keeps full precision (not snapped to cents)."""
    sim = SimulatedSource(seed=3, enable_events=False, round_digits=None)
    seen_subcent = False
    for _ in range(50):
        prices = await sim.fetch_prices(["AAPL"])
        if round(prices["AAPL"], 2) != prices["AAPL"]:
            seen_subcent = True
            break
    assert seen_subcent


@pytest.mark.asyncio
async def test_default_prices_rounded_to_cents(sim):
    """Default behavior snaps prices to two decimals for display."""
    for _ in range(20):
        prices = await sim.fetch_prices(["AAPL"])
        assert round(prices["AAPL"], 2) == prices["AAPL"]
