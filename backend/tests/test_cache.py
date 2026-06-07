"""Unit tests for PriceCache."""
import pytest
from app.market.cache import PriceCache
from app.market.types import Direction


def test_first_observation_returns_flat_update():
    cache = PriceCache()
    update = cache.set("AAPL", 190.0)
    assert update is not None
    assert update.ticker == "AAPL"
    assert update.price == 190.0
    assert update.previous_price == 190.0
    assert update.direction == Direction.FLAT


def test_first_observation_sets_reference_to_price():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    entry = cache.get("AAPL")
    assert entry.reference == 190.0


def test_unchanged_price_returns_none():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    result = cache.set("AAPL", 190.0)
    assert result is None


def test_price_increase_returns_up_direction():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    update = cache.set("AAPL", 191.0)
    assert update is not None
    assert update.direction == Direction.UP
    assert update.previous_price == 190.0
    assert update.price == 191.0


def test_price_decrease_returns_down_direction():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    update = cache.set("AAPL", 189.0)
    assert update is not None
    assert update.direction == Direction.DOWN
    assert update.previous_price == 190.0
    assert update.price == 189.0


def test_previous_price_tracks_prior_tick():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    cache.set("AAPL", 191.0)
    update = cache.set("AAPL", 192.0)
    assert update.previous_price == 191.0


def test_reference_is_immutable_after_first_set():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    cache.set("AAPL", 195.0)
    cache.set("AAPL", 185.0)
    entry = cache.get("AAPL")
    assert entry.reference == 190.0


def test_change_pct_calculation():
    cache = PriceCache()
    cache.set("AAPL", 100.0)
    cache.set("AAPL", 110.0)
    pct = cache.change_pct("AAPL")
    assert abs(pct - 0.10) < 1e-9


def test_change_pct_none_for_unknown_ticker():
    cache = PriceCache()
    assert cache.change_pct("UNKNOWN") is None


def test_change_pct_none_when_reference_is_zero():
    cache = PriceCache()
    cache.set("AAPL", 0.0)
    assert cache.change_pct("AAPL") is None


def test_seed_reference_sets_baseline_before_tick():
    cache = PriceCache()
    cache.seed_reference("AAPL", 188.0)
    entry = cache.get("AAPL")
    assert entry is not None
    assert entry.reference == 188.0


def test_seed_reference_overrides_auto_reference():
    """An explicit baseline wins over the auto reference set on first sight,
    so a driver tick that lands before seeding doesn't lose the prev-close baseline."""
    cache = PriceCache()
    cache.set("AAPL", 190.0)           # auto reference = 190 (ref_seeded=False)
    cache.seed_reference("AAPL", 100.0)
    entry = cache.get("AAPL")
    assert entry.reference == 100.0
    assert entry.price == 190.0        # latest price untouched


def test_seed_reference_noop_if_already_seeded():
    """Re-seeding never clobbers a baseline that was already set explicitly."""
    cache = PriceCache()
    cache.seed_reference("AAPL", 188.0)
    cache.seed_reference("AAPL", 100.0)
    entry = cache.get("AAPL")
    assert entry.reference == 188.0


def test_remove_drops_ticker():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    assert cache.remove("aapl") is True
    assert cache.get("AAPL") is None
    assert cache.remove("AAPL") is False


def test_snapshot_updates_returns_all_known_tickers():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    cache.set("GOOGL", 175.0)
    snapshot = cache.snapshot_updates()
    tickers = {u.ticker for u in snapshot}
    assert tickers == {"AAPL", "GOOGL"}


def test_snapshot_updates_empty_when_no_tickers():
    cache = PriceCache()
    assert cache.snapshot_updates() == []


def test_as_dict_contains_expected_fields():
    cache = PriceCache()
    cache.set("AAPL", 190.0)
    d = cache.as_dict()
    assert "AAPL" in d
    entry = d["AAPL"]
    assert "price" in entry
    assert "previous_price" in entry
    assert "reference" in entry
    assert "change_pct" in entry
    assert "timestamp" in entry


def test_ticker_uppercased_on_set():
    cache = PriceCache()
    cache.set("aapl", 190.0)
    assert cache.get("AAPL") is not None
    assert cache.get("aapl") is not None


def test_set_with_explicit_reference():
    cache = PriceCache()
    update = cache.set("AAPL", 190.0, reference=185.0)
    assert update is not None
    entry = cache.get("AAPL")
    assert entry.reference == 185.0
