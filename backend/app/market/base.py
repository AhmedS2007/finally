from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterable


class SymbolError(Exception):
    """Raised when a ticker cannot be used (surfaced as an add-watchlist error)."""


class MarketSource(ABC):
    """Abstract price source. Either a simulator or the Massive REST client."""

    #: Nominal cadence (seconds) at which the driver loop should pull/produce prices.
    tick_interval: float = 0.5

    @abstractmethod
    async def start(self) -> None:
        """Initialize state (seed prices, open HTTP client, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Release resources (close HTTP client, cancel timers)."""

    @abstractmethod
    async def fetch_prices(self, tickers: Iterable[str]) -> dict[str, float]:
        """Return {ticker: price} for the requested tickers for this tick.

        Must NOT raise on transient errors — return what it has (possibly empty).
        """

    @abstractmethod
    async def register_ticker(self, ticker: str) -> str:
        """Validate/normalize a new symbol; return the canonical ticker.

        Simulator always succeeds. Massive raises SymbolError if invalid.
        """

    @abstractmethod
    async def history(self, ticker: str) -> list[tuple[float, float]]:
        """Return [(epoch_seconds, price)] for backfilling the main chart.

        May return [] if no history is available yet.
        """
