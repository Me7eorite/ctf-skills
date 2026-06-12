"""Server-Sent Events stream for progress updates."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from core.state import StateStore

HEARTBEAT_INTERVAL_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 1.0


def _format_event(event: dict) -> str:
    event_id = event.get("id", 0)
    return f"id: {event_id}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _parse_last_event_id(header_value: str | None) -> int:
    if not header_value:
        return 0
    try:
        return max(0, int(header_value.strip()))
    except ValueError:
        return 0


async def _event_generator(
    request: Request,
    store: StateStore,
    last_event_id: int,
    *,
    replay: bool,
    heartbeat_interval: float,
    poll_interval: float,
) -> AsyncIterator[str]:
    cursor = last_event_id
    if replay:
        # Replay any events the client missed before tailing new ones.
        backlog = store.trace_events_after(cursor, limit=500)
        for event in backlog:
            yield _format_event(event)
            cursor = max(cursor, int(event.get("id", cursor)))
    else:
        cursor = max(cursor, store.latest_progress_event_id())

    next_heartbeat = asyncio.get_event_loop().time() + heartbeat_interval
    while True:
        if await request.is_disconnected():
            return
        events = store.trace_events_after(cursor, limit=100)
        for event in events:
            yield _format_event(event)
            cursor = max(cursor, int(event.get("id", cursor)))
        now = asyncio.get_event_loop().time()
        if now >= next_heartbeat:
            yield ":heartbeat\n\n"
            next_heartbeat = now + heartbeat_interval
        await asyncio.sleep(poll_interval)


def create_sse_router(
    store: StateStore,
    *,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/events/stream")
    async def stream(request: Request, replay: bool = True) -> StreamingResponse:
        last_event_id = _parse_last_event_id(request.headers.get("Last-Event-ID"))
        generator = _event_generator(
            request,
            store,
            last_event_id,
            replay=replay,
            heartbeat_interval=heartbeat_interval,
            poll_interval=poll_interval,
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    return router
