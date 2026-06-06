from __future__ import annotations
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


def _sse(data: str, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {data}\n\n"


@router.get("/api/stream/prices")
async def stream_prices(request: Request):
    market = request.app.state.market
    heartbeat_interval = request.app.state.market_config.heartbeat_interval

    async def gen():
        updates_iter = market.subscribe()
        try:
            async for batch in _merge_with_heartbeat(updates_iter, heartbeat_interval):
                if await request.is_disconnected():
                    break
                if batch is None:
                    yield ": keep-alive\n\n"
                    continue
                payload = json.dumps([u.to_event() for u in batch])
                yield _sse(payload)
        finally:
            await updates_iter.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _merge_with_heartbeat(updates, interval: float):
    """Yield update batches as they arrive; yield None every interval seconds."""
    pending = asyncio.ensure_future(updates.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if pending in done:
                try:
                    yield pending.result()
                except StopAsyncIteration:
                    return
                pending = asyncio.ensure_future(updates.__anext__())
            else:
                yield None
    finally:
        pending.cancel()
