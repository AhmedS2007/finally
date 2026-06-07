from __future__ import annotations
from collections import defaultdict, deque


class HistoryBuffer:
    """Bounded per-ticker ring buffer of (timestamp, price) points.

    max_points caps memory: at 0.5s ticks, 3600 points ≈ 30 minutes per ticker.
    deque(maxlen=...) drops the oldest point automatically on overflow — O(1).
    """

    def __init__(self, max_points: int = 3600) -> None:
        self._max = max_points
        self._buf: dict[str, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=max_points)
        )

    def append(self, ticker: str, timestamp: float, price: float) -> None:
        self._buf[ticker.upper()].append((timestamp, price))

    def points(self, ticker: str) -> list[tuple[float, float]]:
        """Oldest-first list of (t, p). Empty if nothing buffered yet."""
        return list(self._buf.get(ticker.upper(), ()))

    def has(self, ticker: str) -> bool:
        return bool(self._buf.get(ticker.upper()))

    def remove(self, ticker: str) -> None:
        """Drop a ticker's buffered history (e.g. on watchlist removal)."""
        self._buf.pop(ticker.upper(), None)

    def prime(self, ticker: str, points: list[tuple[float, float]]) -> None:
        """Seed the buffer with backfill points so the first chart render is rich."""
        dq = self._buf[ticker.upper()]
        for t, p in points[-self._max:]:
            dq.append((t, p))
