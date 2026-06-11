"""Server-Sent Events trace stream backed by SQLite progress events."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from http import HTTPStatus

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from core.state import StateStore

PING_INTERVAL_SECONDS = 15
POLL_INTERVAL_SECONDS = 0.5


def _epoch_seconds(timestamp: str) -> float:
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return time.time()
    return parsed.replace(tzinfo=timezone.utc).timestamp()


def _event_payload(row: dict) -> dict:
    return {
        "worker": str(row.get("worker") or ""),
        "shard": str(row.get("shard") or ""),
        "stage": str(row.get("stage") or ""),
        "status": str(row.get("status") or ""),
        "message": str(row.get("message") or "")[:240],
        "ts": _epoch_seconds(str(row.get("created_at") or "")),
    }


def _sse_event(event: str, data: dict | str) -> str:
    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


async def trace_stream(store: StateStore, request: Request) -> AsyncIterator[str]:
    last_id = 0
    last_ping = 0.0
    while True:
        if await request.is_disconnected():
            break
        emitted = False
        for row in store.trace_events_after(last_id):
            last_id = max(last_id, int(row["id"]))
            emitted = True
            yield _sse_event("trace", _event_payload(row))
        now = time.monotonic()
        if not emitted and now - last_ping >= PING_INTERVAL_SECONDS:
            last_ping = now
            yield _sse_event("ping", "keep-alive")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def create_trace_router(store: StateStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/trace/stream")
    async def get_trace_stream(request: Request) -> StreamingResponse:
        return StreamingResponse(
            trace_stream(store, request),
            media_type="text/event-stream",
            status_code=HTTPStatus.OK,
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router
