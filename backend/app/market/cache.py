from __future__ import annotations
import time
from dataclasses import dataclass

from .types import Direction, PriceUpdate


@dataclass(slots=True)
class _Entry:
    price: float
    previous: float
    reference: float
    ts: float


class PriceCache:
    """In-memory latest/previous/reference price store with change detection.

    Mutated only by the facade's single driver loop, so no locking is required.
    """

    def __init__(self) -> None:
        self._data: dict[str, _Entry] = {}

    def set(self, ticker: str, price: float, *, reference: float | None = None
            ) -> PriceUpdate | None:
        """Record a new price. Returns a PriceUpdate, or None if unchanged."""
        ticker = ticker.upper()
        now = time.time()
        entry = self._data.get(ticker)

        if entry is None:
            ref = reference if reference is not None else price
            self._data[ticker] = _Entry(price=price, previous=price,
                                        reference=ref, ts=now)
            return PriceUpdate(ticker, price, price, now, Direction.FLAT)

        if price == entry.price:
            return None

        prev = entry.price
        entry.previous = prev
        entry.price = price
        entry.ts = now
        direction = Direction.UP if price > prev else Direction.DOWN
        return PriceUpdate(ticker, price, prev, now, direction)

    def seed_reference(self, ticker: str, reference: float) -> None:
        """Pre-set a reference baseline before the first tick."""
        ticker = ticker.upper()
        if ticker not in self._data:
            self._data[ticker] = _Entry(price=reference, previous=reference,
                                        reference=reference, ts=time.time())

    def get(self, ticker: str) -> _Entry | None:
        return self._data.get(ticker.upper())

    def change_pct(self, ticker: str) -> float | None:
        """(current - reference) / reference — the watchlist 'Chg %'."""
        e = self._data.get(ticker.upper())
        if e is None or e.reference == 0:
            return None
        return (e.price - e.reference) / e.reference

    def snapshot_updates(self) -> list[PriceUpdate]:
        """Full snapshot as PriceUpdate list — first SSE event on every (re)connect."""
        return [
            PriceUpdate(t, e.price, e.previous, e.ts,
                        Direction.UP if e.price > e.previous
                        else Direction.DOWN if e.price < e.previous
                        else Direction.FLAT)
            for t, e in self._data.items()
        ]

    def as_dict(self) -> dict[str, dict]:
        """Plain-dict view for GET /api/watchlist initial paint."""
        return {
            t: {
                "ticker": t,
                "price": round(e.price, 4),
                "previous_price": round(e.previous, 4),
                "reference": round(e.reference, 4),
                "change_pct": round((e.price - e.reference) / e.reference, 6)
                              if e.reference else None,
                "timestamp": e.ts,
            }
            for t, e in self._data.items()
        }
