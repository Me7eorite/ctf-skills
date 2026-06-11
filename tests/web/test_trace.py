from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from core.paths import ProjectPaths
from core.state import StateStore
from web.trace import _event_payload, trace_stream


class TraceStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.store = StateStore(self.paths)

    def test_trace_events_after_returns_ordered_rows(self):
        first = self.store.record(
            shard="web-demo.json",
            challenge_id="web-demo-0001",
            worker="demo-01",
            stage="design",
            status="running",
            message="Designing",
        )
        second = self.store.record(
            shard="web-demo.json",
            challenge_id="web-demo-0001",
            worker="demo-01",
            stage="build",
            status="passed",
            message="Built",
        )

        rows = self.store.trace_events_after(first["event_id"])

        self.assertEqual([row["id"] for row in rows], [second["event_id"]])
        payload = _event_payload(rows[0])
        self.assertEqual(payload["worker"], "demo-01")
        self.assertEqual(payload["stage"], "build")
        self.assertEqual(payload["status"], "passed")

    def test_stream_emits_trace_event(self):
        self.store.record(
            shard="web-demo.json",
            worker="demo-01",
            stage="queued",
            status="running",
            message="Queued",
        )
        request = type("Request", (), {"is_disconnected": AsyncMock(side_effect=[False, True])})()

        async def collect_one() -> str:
            stream = trace_stream(self.store, request)
            return await anext(stream)

        event = asyncio.run(collect_one())

        self.assertIn("event: trace", event)
        self.assertIn('"worker":"demo-01"', event)


if __name__ == "__main__":
    unittest.main()
