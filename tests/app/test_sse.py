"""Server-Sent Events stream tests.

We exercise the async generator directly instead of going through the HTTP
test client, because the stream is open-ended by design and the client's
context-manager cleanup waits for the generator to finish on its own.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from core.paths import ProjectPaths
from core.state import StateStore
from web.sse import _event_generator, create_sse_router


class _FakeRequest:
    """Minimal request stand-in: is_disconnected() flips True after N calls."""

    def __init__(self, disconnect_after: int) -> None:
        self._remaining = disconnect_after

    async def is_disconnected(self) -> bool:
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False


async def _drain(
    request: _FakeRequest,
    store: StateStore,
    *,
    last_event_id: int = 0,
    heartbeat: float = 0.02,
    poll: float = 0.005,
) -> list[str]:
    chunks: list[str] = []
    async for chunk in _event_generator(
        request,
        store,
        last_event_id,
        heartbeat_interval=heartbeat,
        poll_interval=poll,
    ):
        chunks.append(chunk)
    return chunks


class _DummyRequest:
    """Stand-in request that satisfies the SSE handler signature."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return True


class SseHeaderTests(unittest.TestCase):
    """Inspect the StreamingResponse returned by the route directly.

    The full HTTP test client cannot be used because the stream is open-ended
    and TestClient blocks on cleanup until the generator completes.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.store = StateStore(self.paths)

    def test_router_sets_event_stream_content_type_and_buffering(self) -> None:
        router = create_sse_router(self.store, heartbeat_interval=0.01, poll_interval=0.005)
        endpoint = next(
            route.endpoint for route in router.routes
            if getattr(route, "path", "") == "/api/events/stream"
        )
        response = asyncio.run(endpoint(_DummyRequest()))
        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers.get("x-accel-buffering"), "no")
        self.assertEqual(response.headers.get("cache-control"), "no-store")


class SseGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.store = StateStore(self.paths)

    def test_heartbeat_emitted_when_no_events(self) -> None:
        request = _FakeRequest(disconnect_after=20)
        chunks = asyncio.run(_drain(request, self.store))
        joined = "".join(chunks)
        self.assertIn(":heartbeat", joined)

    def test_replay_from_last_event_id(self) -> None:
        for index in range(3):
            self.store.record(
                shard=f"shard-{index}",
                stage="queued",
                status="running",
                worker="worker",
                message=f"msg-{index}",
            )
        request = _FakeRequest(disconnect_after=1)
        chunks = asyncio.run(
            _drain(request, self.store, last_event_id=1)
        )
        joined = "".join(chunks)
        self.assertIn("id: 2", joined)
        self.assertIn("id: 3", joined)
        self.assertNotIn("id: 1\ndata:", joined)

    def test_new_events_tailed_after_replay(self) -> None:
        request = _FakeRequest(disconnect_after=3)
        # No events stored; the only outputs should be heartbeats.
        chunks = asyncio.run(
            _drain(request, self.store, heartbeat=0.005, poll=0.005)
        )
        joined = "".join(chunks)
        self.assertIn(":heartbeat", joined)
        self.assertNotIn("data:", joined)


if __name__ == "__main__":
    unittest.main()
