import tempfile
import unittest
from pathlib import Path

from core.jsonio import write_json
from core.paths import ProjectPaths
from web.dashboard import DashboardService, TaskManager


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()

    def test_state_summarizes_challenges_and_queue(self):
        challenge = self.paths.challenges / "web" / "web-0001-demo"
        write_json(
            challenge / "metadata.json",
            {
                "id": "web-0001",
                "title": "Demo",
                "category": "web",
                "difficulty": "easy",
                "runtime": "node",
                "framework": "Express",
                "build_status": "passed",
                "solve_status": "passed",
            },
        )
        write_json(
            self.paths.shards / "pending" / "web-0001-0001.json",
            {"challenges": [{"id": "web-0001", "category": "web"}]},
        )

        state = DashboardService(self.paths).state()

        self.assertEqual(state["summary"]["challenges"], 1)
        self.assertEqual(state["summary"]["validated"], 1)
        self.assertEqual(state["summary"]["queue"]["pending"], 1)
        self.assertEqual(state["challenges"][0]["runtime"], "node")
        self.assertEqual(state["seeds"], [])
        self.assertEqual(state["progress"]["snapshots"], [])
        self.assertFalse(state["progress"]["storage"]["fallback"])

    def test_worker_rejects_empty_pending_queue(self):
        ok, message = TaskManager(self.paths).start("worker")

        self.assertFalse(ok)
        self.assertIn("待处理分片", message)
