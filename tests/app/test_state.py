import tempfile
import unittest
from pathlib import Path

import core.state as state_module
from core.paths import ProjectPaths
from core.state import STAGES, STATUSES, InMemoryProgressStore, ProgressEventInput, ProgressStore


class ProgressStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.store = InMemoryProgressStore()

    def test_record_appends_events_and_updates_snapshot(self):
        self.store.record(
            shard="web-0001-0001.worker.json",
            challenge_id="web-0001",
            worker="worker-1",
            stage="design",
            status="running",
            message="Drafting the intended path",
        )
        self.store.record(
            shard="web-0001-0001.worker.json",
            challenge_id="web-0001",
            worker="worker-1",
            stage="design",
            status="passed",
            message="Design passed the quality gate",
        )

        progress = self.store.dashboard()

        self.assertEqual(len(progress["snapshots"]), 1)
        self.assertEqual(progress["snapshots"][0]["status"], "passed")
        self.assertEqual(len(progress["events"]), 2)
        self.assertEqual(
            progress["events"][0]["message"], "Design passed the quality gate"
        )

    def test_rejects_unknown_stage(self):
        with self.assertRaises(ValueError):
            self.store.record(
                shard="demo.json",
                stage="invent",
                status="running",
            )

    def test_dashboard_declares_memory_backend(self):
        progress = self.store.dashboard()

        self.assertEqual(progress["storage"]["backend"], "memory")
        self.assertFalse(progress["storage"]["fallback"])

    def test_core_state_exports_progress_contract(self):
        self.assertFalse(hasattr(state_module, "State" + "Store"))
        self.assertIsNotNone(ProgressStore)
        self.assertIsNotNone(ProgressEventInput)
        self.assertIsNotNone(InMemoryProgressStore)
        self.assertIn("queued", STAGES)
        self.assertIn("running", STATUSES)
