import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard import DashboardService, TaskManager
from paths import ProjectPaths
from webserver import create_app


class _StubTaskManager(TaskManager):
    def __init__(self, paths: ProjectPaths, response: tuple[bool, str]):
        super().__init__(paths)
        self._response = response
        self.calls: list[str] = []

    def start(self, kind: str) -> tuple[bool, str]:
        self.calls.append(kind)
        return self._response

    def state(self) -> dict:
        return {"running": False}


class WebserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()

    def _client(self, tasks: TaskManager | None = None) -> TestClient:
        service = DashboardService(self.paths, tasks=tasks)
        return TestClient(create_app(service))

    def test_state_endpoint_returns_dashboard(self):
        with self._client() as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("summary", payload)
        self.assertIn("shards", payload)

    def test_logs_endpoint_returns_404_when_missing(self):
        with self._client() as client:
            response = client.get("/api/logs/missing.log")
        self.assertEqual(response.status_code, 404)

    def test_logs_endpoint_returns_content(self):
        log_path = self.paths.logs / "demo.log"
        log_path.write_text("hello\nworld\n", encoding="utf-8")
        with self._client() as client:
            response = client.get("/api/logs/demo.log")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "demo.log")
        self.assertIn("hello", body["content"])

    def test_worker_action_returns_accepted_on_ok(self):
        tasks = _StubTaskManager(self.paths, (True, "worker 已启动"))
        with self._client(tasks=tasks) as client:
            response = client.post("/api/actions/worker")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(tasks.calls, ["worker"])
        self.assertEqual(response.json(), {"ok": True, "message": "worker 已启动"})

    def test_validate_action_returns_conflict_on_failure(self):
        tasks = _StubTaskManager(self.paths, (False, "busy"))
        with self._client(tasks=tasks) as client:
            response = client.post("/api/actions/validate")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(tasks.calls, ["validate"])

    def test_shard_requeue_validates_state(self):
        with self._client() as client:
            response = client.post("/api/shards/done/foo.json/requeue")
        self.assertEqual(response.status_code, 404)

    def test_shard_requeue_returns_conflict_when_missing(self):
        with self._client() as client:
            response = client.post("/api/shards/failed/missing.json/requeue")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["ok"], False)


if __name__ == "__main__":
    unittest.main()
