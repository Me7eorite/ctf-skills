import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from core.jsonio import write_json
from core.paths import ProjectPaths
from web.dashboard import DashboardService, TaskManager
from web.server import create_app


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


class _StubBuildReconciler:
    def __init__(self):
        self.calls = 0

    def tick_once_sync(self) -> None:
        self.calls += 1


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

    def _write_deliverable_challenge(self) -> None:
        challenge = self.paths.challenges / "re" / "re-0001-demo"
        (challenge / "writenup").mkdir(parents=True)
        (challenge / "writenup" / "wp.md").write_text(
            "# 题目分析\n\n这是中文题解。\n",
            encoding="utf-8",
        )
        (challenge / "writenup" / "exp.py").write_text(
            "print('flag{demo}')\n",
            encoding="utf-8",
        )
        (challenge / "dist").mkdir()
        (challenge / "dist" / "checker.bin").write_bytes(b"artifact")
        write_json(
            challenge / "metadata.json",
            {
                "id": "re-0001",
                "title": "Demo",
                "category": "re",
                "difficulty": "easy",
                "build_status": "passed",
                "solve_status": "passed",
            },
        )

    def test_state_endpoint_returns_dashboard(self):
        with self._client() as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("summary", payload)
        self.assertIn("shards", payload)

    def test_state_endpoint_exposes_build_profile_readiness(self):
        readiness = {
            "ready": False,
            "categories": {
                "pwn": {
                    "ready": False,
                    "profile": "cf-pwn",
                    "create_command": "hermes profile create cf-pwn",
                }
            },
            "missing_profiles": ["cf-pwn"],
        }
        service = DashboardService(self.paths)
        with TestClient(
            create_app(service, build_profile_readiness=readiness)
        ) as client:
            payload = client.get("/api/state").json()

        self.assertEqual(payload["build_readiness"], readiness)

    def test_state_endpoint_exposes_latest_sequential_worker_result(self):
        write_json(
            self.paths.logs / "dashboard-sequential-worker-result.json",
            {
                "abort_reason": "consecutive_infra",
                "aborted": ["attempt-1"],
                "outcomes": [{"status": "aborted", "shard": "attempt-1"}],
            },
        )

        with self._client() as client:
            payload = client.get("/api/state").json()

        self.assertEqual(
            payload["sequential_worker_result"]["abort_reason"],
            "consecutive_infra",
        )

    def test_state_endpoint_does_not_trigger_synchronous_reconciliation(self):
        """Phase 0 hot fix: /api/state no longer triggers tick_once_sync.

        Frontend polling at high frequency was amplifying reconciler tick
        rate above filesystem stability, causing the lost-race bug. Background
        thread now owns the reconciler cadence.
        """
        reconciler = _StubBuildReconciler()
        service = DashboardService(self.paths)
        with TestClient(create_app(service, build_reconciler=reconciler)) as client:
            for _ in range(3):
                response = client.get("/api/state")
                self.assertEqual(response.status_code, 200)
        self.assertEqual(
            reconciler.calls, 0, "GET /api/state must not call tick_once_sync"
        )

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

    def test_corpus_rollout_evidence_endpoint_evaluates_gate(self):
        trial = {
            "mode": "trial",
            "challenge_count": 20,
            "design_evidence_passed": 20,
            "build_contracts_passed": 20,
            "artifact_observations_passed": 20,
            "aggregate_decision": "passed",
            "member_decisions": {"passed": 18, "review_required": 2, "blocked": 0},
        }

        with self._client() as client:
            response = client.post(
                "/api/corpus/rollout-evidence",
                json={
                    "shadow_report": {
                        "challenge_count": 40,
                        "required_vs_observed": {"matched": 40},
                        "member_decisions": {
                            "passed": 36,
                            "review_required": 4,
                            "blocked": 0,
                        },
                    },
                    "trial_reports": [
                        {**trial, "id": "trial-1"},
                        {**trial, "id": "trial-2"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        evidence = response.json()["evidence"]
        self.assertTrue(evidence["production_mode_allowed"])
        self.assertEqual(evidence["production_mode_action"], "manual_enable_allowed")

    def test_corpus_rollout_evidence_endpoint_requires_two_trials(self):
        with self._client() as client:
            response = client.post(
                "/api/corpus/rollout-evidence",
                json={"shadow_report": {}, "trial_reports": []},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("at least two", response.json()["message"])

    def test_delivery_download_returns_zip_for_publishable_challenges(self):
        self._write_deliverable_challenge()

        with self._client() as client:
            response = client.get("/api/challenges/delivery/download")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        archive_path = self.paths.work / "download-fixture.zip"
        archive_path.write_bytes(response.content)
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        self.assertIn("题库资源/ctf-overview.xlsx", names)
        self.assertIn("工具/js-reverse-re-0001exp.zip", names)

    def test_delivery_download_rejects_empty_publishable_set(self):
        with self._client() as client:
            response = client.get("/api/challenges/delivery/download")

        self.assertEqual(response.status_code, 409)
        self.assertIn("没有可交付题目", response.json()["detail"])

    def test_single_delivery_download_returns_zip_for_one_challenge(self):
        self._write_deliverable_challenge()

        with self._client() as client:
            response = client.get("/api/challenges/re-0001/delivery/download")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        archive_path = self.paths.work / "single-download-fixture.zip"
        archive_path.write_bytes(response.content)
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        self.assertIn("题库资源/ctf-overview.xlsx", names)
        self.assertEqual(
            [name for name in names if name.startswith("工具/")],
            ["工具/js-reverse-re-0001exp.zip"],
        )

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

    def test_task_manager_starts_exact_worker_without_loop(self):
        tasks = TaskManager(self.paths)
        process = Mock()
        process.poll.return_value = None
        attempt_id = uuid4()
        with (
            patch("web.dashboard.subprocess.Popen", return_value=process) as popen,
            patch("web.dashboard.time.sleep"),
        ):
            ok, _message = tasks.start_worker(
                category="web",
                build_attempt_id=attempt_id,
            )

        self.assertTrue(ok)
        command = popen.call_args.args[0]
        self.assertIn("--category", command)
        self.assertIn("web", command)
        self.assertIn("--build-attempt", command)
        self.assertIn(str(attempt_id), command)
        self.assertNotIn("--loop", command)

    def test_task_manager_exact_worker_respects_busy_guard(self):
        tasks = TaskManager(self.paths)
        running = Mock()
        running.poll.return_value = None
        tasks._process = running

        with patch("web.dashboard.subprocess.Popen") as popen:
            ok, message = tasks.start_worker(
                category="web",
                build_attempt_id=uuid4(),
            )

        self.assertFalse(ok)
        self.assertIn("already running", message)
        popen.assert_not_called()

    def test_shard_requeue_validates_state(self):
        with self._client() as client:
            response = client.post("/api/shards/done/foo.json/requeue")
        self.assertEqual(response.status_code, 404)

    def test_shard_requeue_returns_conflict_when_missing(self):
        with self._client() as client:
            response = client.post("/api/shards/failed/missing.json/requeue")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["ok"], False)

    def test_shard_requeue_rejects_attributed_build_attempt(self):
        shard = self.paths.shards / "failed" / "web-0001.json"
        write_json(
            shard,
            {
                "build_attempt_id": "11111111-1111-1111-1111-111111111111",
                "challenges": [{"id": "web-0001", "category": "web"}],
            },
        )
        with self._client() as client:
            response = client.post("/api/shards/failed/web-0001.json/requeue")

        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertEqual(payload["build_attempt_id"], "11111111-1111-1111-1111-111111111111")
        self.assertIn("/api/build-attempts/11111111-1111-1111-1111-111111111111/retry", payload["retry_url"])
        self.assertTrue(shard.exists())

    def test_shard_requeue_keeps_unattributed_behavior(self):
        shard = self.paths.shards / "failed" / "web-0001.json"
        write_json(shard, {"challenges": [{"id": "web-0001", "category": "web"}]})

        with self._client() as client:
            response = client.post("/api/shards/failed/web-0001.json/requeue")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(shard.exists())
        self.assertTrue((self.paths.shards / "pending" / "web-0001.json").exists())

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

    def test_invalid_seed_returns_bad_request(self):
        with self._client() as client:
            response = client.post("/api/seeds", json={"id": "bad"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.json()["message"])


if __name__ == "__main__":
    unittest.main()
