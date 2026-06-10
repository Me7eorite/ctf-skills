import tempfile
import unittest
from pathlib import Path

from core.paths import ProjectPaths
from core.state import StateStore


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.store = StateStore(self.paths)

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

    def test_falls_back_when_work_path_is_not_a_directory(self):
        blocked_root = Path(self.temp.name) / "blocked"
        blocked_root.mkdir()
        (blocked_root / "work").write_text("not a directory", encoding="utf-8")
        paths = ProjectPaths(root=blocked_root, repository=Path(self.temp.name))

        store = StateStore(paths)
        progress = store.dashboard()

        self.assertTrue(progress["storage"]["fallback"])
        self.assertIn("fallback database", progress["storage"]["warning"])
