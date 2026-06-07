"""Unit tests for HistoryBuffer."""
import pytest
from app.market.history import HistoryBuffer


def test_append_and_retrieve():
    buf = HistoryBuffer(max_points=100)
    buf.append("AAPL", 1000.0, 190.0)
    buf.append("AAPL", 1000.5, 191.0)
    pts = buf.points("AAPL")
    assert len(pts) == 2
    assert pts[0] == (1000.0, 190.0)
    assert pts[1] == (1000.5, 191.0)


def test_points_empty_for_unknown_ticker():
    buf = HistoryBuffer()
    assert buf.points("UNKNOWN") == []


def test_oldest_point_evicted_at_max():
    buf = HistoryBuffer(max_points=3)
    for i in range(5):
        buf.append("AAPL", float(i), 100.0 + i)
    pts = buf.points("AAPL")
    assert len(pts) == 3
    assert pts[0][0] == 2.0


def test_has_returns_false_before_append():
    buf = HistoryBuffer()
    assert not buf.has("AAPL")


def test_has_returns_true_after_append():
    buf = HistoryBuffer()
    buf.append("AAPL", 1.0, 190.0)
    assert buf.has("AAPL")


def test_prime_seeds_buffer():
    buf = HistoryBuffer(max_points=100)
    points = [(float(i), 100.0 + i) for i in range(10)]
    buf.prime("AAPL", points)
    pts = buf.points("AAPL")
    assert len(pts) == 10
    assert pts[0] == (0.0, 100.0)


def test_prime_respects_max_points():
    buf = HistoryBuffer(max_points=5)
    points = [(float(i), float(i)) for i in range(10)]
    buf.prime("AAPL", points)
    pts = buf.points("AAPL")
    assert len(pts) == 5
    assert pts[0][0] == 5.0


def test_ticker_uppercased():
    buf = HistoryBuffer()
    buf.append("aapl", 1.0, 190.0)
    assert buf.has("AAPL")
    assert buf.points("aapl") == buf.points("AAPL")


def test_remove_drops_history():
    buf = HistoryBuffer()
    buf.append("AAPL", 1.0, 190.0)
    assert buf.has("AAPL")
    buf.remove("aapl")
    assert not buf.has("AAPL")
    assert buf.points("AAPL") == []
    buf.remove("AAPL")  # idempotent, no error


def test_separate_tickers_independent():
    buf = HistoryBuffer()
    buf.append("AAPL", 1.0, 190.0)
    buf.append("GOOGL", 2.0, 175.0)
    assert len(buf.points("AAPL")) == 1
    assert len(buf.points("GOOGL")) == 1
    assert buf.points("AAPL")[0][1] == 190.0
    assert buf.points("GOOGL")[0][1] == 175.0
