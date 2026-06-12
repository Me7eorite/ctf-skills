import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from web.dashboard import DashboardService, TaskManager
from web.server import create_app


class _StubTaskManager(TaskManager):
    def __init__(self, paths: ProjectPaths, response: tuple[bool, str]):
        super().__init__(paths)
        self._response = response
        self.calls: list[str] = []

    def start(
        self,
        kind: str,
        *,
        dry_run: bool = False,
        timeout: int | None = None,
    ) -> tuple[bool, str]:
        self.calls.append(kind)
        self.dry_run = dry_run
        self.timeout = timeout
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

    def test_seed_crud_and_enqueue(self):
        seed = {
            "id": "web-0001",
            "title": "Demo",
            "category": "web",
            "difficulty": "easy",
            "points": 100,
            "port": 8080,
            "primary_technique": "auth bypass",
            "learning_objective": "Understand trust boundaries",
            "runtime": "node",
        }
        with self._client() as client:
            saved = client.post("/api/seeds", json=seed)
            state = client.get("/api/state")
            enqueued = client.post("/api/seeds/enqueue", json={"size": 5})
            deleted = client.delete("/api/seeds/web-0001")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(state.json()["seeds"][0]["runtime"], "node")
        self.assertEqual(enqueued.status_code, 201)
        self.assertEqual(enqueued.json()["shards"], ["web-0001-0001.json"])
        self.assertEqual(deleted.status_code, 200)

    def test_create_run_saves_seeds_enqueues_and_starts_worker(self):
        tasks = _StubTaskManager(self.paths, (True, "worker 已启动"))
        payload = {
            "seeds": [
                {
                    "id": "web-0001",
                    "title": "Demo",
                    "category": "web",
                    "difficulty": "easy",
                    "points": 100,
                    "port": 8080,
                    "primary_technique": "auth bypass",
                    "learning_objective": "Understand trust boundaries",
                }
            ],
            "shard_size": 1,
            "start_worker": True,
            "dry_run": True,
            "timeout": 120,
        }

        with self._client(tasks=tasks) as client:
            response = client.post("/api/runs", json=payload)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["seeds"], ["web-0001"])
        self.assertEqual(body["shards"], ["web-0001-0001.json"])
        self.assertEqual(body["worker"]["started"], True)
        self.assertEqual(body["worker"]["dry_run"], True)
        self.assertEqual(tasks.calls, ["worker"])
        self.assertEqual(tasks.dry_run, True)
        self.assertEqual(tasks.timeout, 120)

    def test_create_run_can_only_enqueue(self):
        payload = {
            "seeds": [
                {
                    "id": "re-0001",
                    "title": "Demo",
                    "category": "re",
                    "difficulty": "easy",
                    "points": 100,
                    "primary_technique": "string recovery",
                    "learning_objective": "Trace encoded constants",
                }
            ],
            "shard_size": 1,
            "start_worker": False,
        }

        with self._client() as client:
            response = client.post("/api/runs", json=payload)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["shards"], ["re-0001-0001.json"])
        self.assertEqual(body["worker"]["requested"], False)

    def test_create_run_only_enqueues_submitted_seeds(self):
        existing = {
            "id": "web-0001",
            "title": "Existing",
            "category": "web",
            "difficulty": "easy",
            "points": 100,
            "port": 8080,
            "primary_technique": "auth bypass",
            "learning_objective": "Understand trust boundaries",
        }
        payload = {
            "seeds": [
                {
                    "id": "pwn-0001",
                    "title": "Submitted",
                    "category": "pwn",
                    "difficulty": "easy",
                    "points": 100,
                    "port": 9001,
                    "primary_technique": "overflow",
                    "learning_objective": "Control saved return state",
                }
            ],
            "shard_size": 1,
            "start_worker": False,
        }

        with self._client() as client:
            client.post("/api/seeds", json=existing)
            response = client.post("/api/runs", json=payload)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["shards"], ["pwn-0001-0001.json"])
        self.assertFalse((self.paths.shards / "pending" / "web-0001-0001.json").exists())

    def test_create_run_requires_seeds(self):
        with self._client() as client:
            response = client.post("/api/runs", json={"shard_size": 1})

        self.assertEqual(response.status_code, 400)
        self.assertIn("题目种子", response.json()["message"])

    def test_invalid_seed_returns_bad_request(self):
        with self._client() as client:
            response = client.post("/api/seeds", json={"id": "bad"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.json()["message"])


if __name__ == "__main__":
    unittest.main()
