from __future__ import annotations
import os
from typing import Iterable

import httpx

DEFAULT_BASE_URL = "https://api.polygon.io"


class MassiveError(Exception):
    """Raised for auth/plan problems the caller should surface."""


class MassiveClient:
    """Thin async wrapper over the Massive (Polygon) REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("MASSIVE_API_KEY is required for MassiveClient")
        self._base_url = (base_url or os.getenv("MASSIVE_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def snapshot(self, tickers: Iterable[str]) -> dict[str, dict]:
        """Return {ticker: snapshot_result} for the given tickers (<=250 per chunk)."""
        tickers_list = [t.upper() for t in tickers]
        out: dict[str, dict] = {}
        for chunk in _chunks(tickers_list, 250):
            params = {
                "type": "stocks",
                "ticker.any_of": ",".join(chunk),
                "limit": 250,
            }
            resp = await self._client.get("/v3/snapshot", params=params)
            self._raise_for_plan(resp)
            resp.raise_for_status()
            body = resp.json()
            for r in body.get("results", []):
                out[r["ticker"]] = r
        return out

    async def latest_prices(self, tickers: Iterable[str]) -> dict[str, float]:
        """Flatten snapshot() to {ticker: price}."""
        snap = await self.snapshot(tickers)
        prices: dict[str, float] = {}
        for tk, r in snap.items():
            price = _extract_price(r)
            if price is not None:
                prices[tk] = price
        return prices

    async def history(
        self,
        ticker: str,
        *,
        multiplier: int = 1,
        timespan: str = "minute",
        from_: str,
        to: str,
        limit: int = 5000,
    ) -> list[tuple[float, float]]:
        """Return [(epoch_seconds, close)] bars, oldest first."""
        path = f"/v2/aggs/ticker/{ticker.upper()}/range/{multiplier}/{timespan}/{from_}/{to}"
        resp = await self._client.get(
            path, params={"adjusted": "true", "sort": "asc", "limit": limit}
        )
        self._raise_for_plan(resp)
        resp.raise_for_status()
        body = resp.json()
        return [(bar["t"] / 1000.0, bar["c"]) for bar in body.get("results", [])]

    async def previous_close(self, ticker: str) -> float | None:
        """Prior trading day's close — works on all plans."""
        resp = await self._client.get(
            f"/v2/aggs/ticker/{ticker.upper()}/prev",
            params={"adjusted": "true"},
        )
        self._raise_for_plan(resp)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        return results[0]["c"] if results else None

    @staticmethod
    def _raise_for_plan(resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            try:
                msg = resp.json().get("message", resp.text)
            except Exception:
                msg = resp.text
            raise MassiveError(f"Massive API not authorized ({resp.status_code}): {msg}")


def _extract_price(result: dict) -> float | None:
    lt = result.get("last_trade") or {}
    if lt.get("price"):
        return float(lt["price"])
    session = result.get("session") or {}
    if session.get("close"):
        return float(session["close"])
    q = result.get("last_quote") or {}
    if q.get("bid") and q.get("ask"):
        return (float(q["bid"]) + float(q["ask"])) / 2.0
    return None


def _chunks(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i: i + n]
