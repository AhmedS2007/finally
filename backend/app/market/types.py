from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """One price observation. Emitted by the cache; serialized into SSE events."""
    ticker: str
    price: float
    previous_price: float
    timestamp: float
    direction: Direction

    def to_event(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": round(self.price, 4),
            "previous_price": round(self.previous_price, 4),
            "timestamp": self.timestamp,
            "direction": self.direction.value,
        }


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A single (time, price) sample for the rolling history ring buffer."""
    timestamp: float
    price: float
