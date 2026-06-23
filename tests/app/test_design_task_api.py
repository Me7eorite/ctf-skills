"""HTTP tests for the design-task-planning endpoints.

Same patching strategy as test_research_api: the FastAPI app uses
`persistence.session.transaction()` per request; tests substitute the
repository/service constructors with stubs so the endpoints can be
exercised without a real Postgres.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from domain.challenge_designs import ChallengeDesign, DesignAttempt
from domain.design_task_validators import DesignTaskValidationError
from domain.design_tasks import DesignTask
from web.dashboard import DashboardService
from web.server import create_app


def _make_design_task(
    *,
    request_id: UUID | None = None,
    run_id: UUID | None = None,
    task_no: int = 1,
    status: str = "draft",
    diversity_flags=None,
) -> DesignTask:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return DesignTask(
        id=uuid4(),
        generation_request_id=request_id or uuid4(),
        research_run_id=run_id or uuid4(),
        task_no=task_no,
        challenge_id=f"web-{task_no:04d}",
        title=f"Drill {task_no}",
        category="web",
        difficulty="easy",
        primary_technique="boolean blind sqli",
        learning_objective="extract data",
        points=100,
        port=8080 + task_no,
        scenario="login form",
        constraints=MappingProxyType({"runtime": "docker"}),
        evidence_summary="cited",
        finding_ids=(uuid4(),),
        status=status,
        created_at=now,
        updated_at=now,
        diversity_flags=diversity_flags,
    )


def _make_attempt(task_id: UUID, attempt_no: int = 1) -> DesignAttempt:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return DesignAttempt(
        id=uuid4(),
        design_task_id=task_id,
        attempt=attempt_no,
        status="completed",
        claimed_by="tester",
        claim_token=uuid4(),
        started_at=now,
        finished_at=now,
        profile_name_used="default",
        prompt_path="work/design/prompts/prompt.md",
        hermes_log_path="work/design/logs/log.txt",
        last_error=None,
        created_at=now,
    )


def _make_design(task_id: UUID, attempt_id: UUID) -> ChallengeDesign:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return ChallengeDesign(
        id=uuid4(),
        design_task_id=task_id,
        design_attempt_id=attempt_id,
        payload={"challenge": {"title": "Drill"}},
        summary="draft challenge",
        flag_format="flag{...}",
        validation_notes="ok",
        quality_gate_passed=True,
        status="draft",
        created_at=now,
        updated_at=now,
    )


@contextlib.contextmanager
def _app_client(
    *,
    planning_service=None,
    design_repo=None,
    research_repo=None,
):
    """Build a TestClient whose design-task endpoints see the supplied stubs."""
    temp = tempfile.TemporaryDirectory()
    try:
        paths = ProjectPaths(root=Path(temp.name), repository=Path(temp.name))
        paths.initialize()
        service = DashboardService(paths)
        app = create_app(service)

        fake_session = SimpleNamespace(
            scalar=lambda _stmt: None,
            scalars=lambda _stmt: [],
        )

        @contextlib.contextmanager
        def _ctx():
            yield fake_session

        default_research_repo = SimpleNamespace()
        default_design_repo = SimpleNamespace(
            list_design_tasks=lambda _request_id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _request_id: {
                "total": 0,
                "by_status": {
                    "draft": 0,
                    "queued": 0,
                    "designing": 0,
                    "designed": 0,
                    "failed": 0,
                    "archived": 0,
                },
            },
            get_with_history=lambda _task_id: None,
            set_design_task_status=lambda _task_id, _status: None,
        )
        default_challenge_design_repo = SimpleNamespace(
            list_attempts=lambda _task_id: [],
            latest_design=lambda _task_id: None,
        )
        default_planning_service = SimpleNamespace(
            generate_for_request=lambda _request_id: [],
        )
        patches = [
            patch("persistence.session.transaction", _ctx),
            patch(
                "persistence.repositories.ResearchRepository",
                return_value=research_repo or default_research_repo,
            ),
            patch(
                "persistence.repositories.DesignTaskRepository",
                return_value=design_repo or default_design_repo,
            ),
            patch(
                "persistence.repositories.ChallengeDesignRepository",
                return_value=default_challenge_design_repo,
            ),
            patch(
                "services.DesignTaskPlanningService",
                return_value=planning_service or default_planning_service,
            ),
        ]
        for p in patches:
            p.start()
        try:
            yield TestClient(app)
        finally:
            for p in patches:
                p.stop()
    finally:
        temp.cleanup()


class GenerateDesignTasksTests(unittest.TestCase):
    def test_creates_target_count_tasks(self):
        request_id = uuid4()
        run_id = uuid4()
        tasks = [
            _make_design_task(request_id=request_id, run_id=run_id, task_no=1),
            _make_design_task(request_id=request_id, run_id=run_id, task_no=2),
        ]
        planner = SimpleNamespace(generate_for_request=lambda _id: tasks)

        with _app_client(planning_service=planner) as client:
            resp = client.post(
                f"/api/research/requests/{request_id}/design-tasks/generate"
            )
            self.assertEqual(resp.status_code, 201)
            payload = resp.json()
            self.assertEqual(payload["request_id"], str(request_id))
            self.assertEqual(payload["design_task_ids"], [str(t.id) for t in tasks])
            self.assertEqual(payload["total"], 2)
            self.assertNotIn("design_tasks", payload)
            self.assertEqual(len(payload["design_task_ids"]), 2)

    def test_unknown_request_returns_404(self):
        def _missing(_id):
            raise DesignTaskValidationError(
                f"generation_request {_id} does not exist"
            )
        planner = SimpleNamespace(generate_for_request=_missing)
        with _app_client(planning_service=planner) as client:
            resp = client.post(
                f"/api/research/requests/{uuid4()}/design-tasks/generate"
            )
            self.assertEqual(resp.status_code, 404)
            self.assertIn("does not exist", resp.json()["detail"])

    def test_not_researched_returns_409(self):
        def _not_researched(_id):
            raise DesignTaskValidationError(
                "generation request has no completed research run"
            )
        planner = SimpleNamespace(generate_for_request=_not_researched)
        with _app_client(planning_service=planner) as client:
            resp = client.post(
                f"/api/research/requests/{uuid4()}/design-tasks/generate"
            )
            self.assertEqual(resp.status_code, 409)
            self.assertIn("research run", resp.json()["detail"])

    def test_regeneration_conflict_returns_409(self):
        def _blocked(_id):
            raise DesignTaskValidationError(
                "cannot regenerate design tasks: 1 task(s) already in "
                "non-draft/archived status"
            )
        planner = SimpleNamespace(generate_for_request=_blocked)
        with _app_client(planning_service=planner) as client:
            resp = client.post(
                f"/api/research/requests/{uuid4()}/design-tasks/generate"
            )
            self.assertEqual(resp.status_code, 409)
            self.assertIn("cannot regenerate", resp.json()["detail"])

    def test_non_uuid_returns_404(self):
        with _app_client() as client:
            resp = client.post(
                "/api/research/requests/not-a-uuid/design-tasks/generate"
            )
            self.assertEqual(resp.status_code, 404)


class PlanReviewEndpointTests(unittest.TestCase):
    def test_approve_returns_approved_ids(self):
        request_id = uuid4()
        task = _make_design_task(request_id=request_id)
        planner = SimpleNamespace(approve_plan=lambda _id: [task])

        with _app_client(planning_service=planner) as client:
            resp = client.post(f"/api/research/requests/{request_id}/design-tasks/approve")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["request_id"], str(request_id))
        self.assertEqual(payload["approved_task_ids"], [str(task.id)])
        self.assertEqual(payload["total"], 1)

    def test_regenerate_all_returns_new_ids(self):
        request_id = uuid4()
        task = _make_design_task(request_id=request_id)
        planner = SimpleNamespace(regenerate_plan=lambda _id: [task])

        with _app_client(planning_service=planner) as client:
            resp = client.post(f"/api/research/requests/{request_id}/design-tasks/regenerate")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["design_task_ids"], [str(task.id)])
        self.assertEqual(payload["total"], 1)

    def test_regenerate_one_returns_outcome_and_task(self):
        request_id = uuid4()
        task = _make_design_task(
            request_id=request_id,
            diversity_flags={
                "family": "injection",
                "sub_technique": "blind sqli",
                "warnings": ["family_quota_exceeded"],
            },
        )
        planner = SimpleNamespace(
            regenerate_task=lambda _id, _task_no: {
                "outcome": "regenerated_with_warning",
                "task": task,
            }
        )

        with _app_client(planning_service=planner) as client:
            resp = client.post(
                f"/api/research/requests/{request_id}/design-tasks/1/regenerate"
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["outcome"], "regenerated_with_warning")
        self.assertEqual(payload["task"]["id"], str(task.id))
        self.assertEqual(payload["task"]["diversity_flags"]["family"], "injection")

    def test_plan_endpoint_validation_conflict(self):
        request_id = uuid4()

        def _blocked(_id):
            raise DesignTaskValidationError("plan approval requires all tasks to be draft")

        planner = SimpleNamespace(approve_plan=_blocked)

        with _app_client(planning_service=planner) as client:
            resp = client.post(f"/api/research/requests/{request_id}/design-tasks/approve")

        self.assertEqual(resp.status_code, 409)
        self.assertIn("requires all tasks", resp.json()["detail"])


class QueueAndArchiveTests(unittest.TestCase):
    def test_queue_transitions_status(self):
        task_id = uuid4()
        queued = _make_design_task(status="queued")

        def _set(task_uuid, status):
            self.assertEqual(task_uuid, task_id)
            self.assertEqual(status, "queued")
            return queued

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: None,
            set_design_task_status=_set,
        )
        with _app_client(design_repo=repo) as client:
            resp = client.post(f"/api/design-tasks/{task_id}/queue")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "queued")

    def test_archive_transitions_status(self):
        task_id = uuid4()
        archived = _make_design_task(status="archived")

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: None,
            set_design_task_status=lambda _id, _status: archived,
        )
        with _app_client(design_repo=repo) as client:
            resp = client.post(f"/api/design-tasks/{task_id}/archive")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "archived")

    def test_unknown_task_returns_404(self):
        def _missing(task_uuid, _status):
            raise DesignTaskValidationError(
                f"design task {task_uuid} does not exist"
            )

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: None,
            set_design_task_status=_missing,
        )
        with _app_client(design_repo=repo) as client:
            resp = client.post(f"/api/design-tasks/{uuid4()}/queue")
            self.assertEqual(resp.status_code, 404)

    def test_invalid_transition_returns_409(self):
        def _reject(_task, _status):
            raise DesignTaskValidationError(
                "transition 'designed' -> 'archived' is not allowed"
            )

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: None,
            set_design_task_status=_reject,
        )
        with _app_client(design_repo=repo) as client:
            resp = client.post(f"/api/design-tasks/{uuid4()}/archive")
            self.assertEqual(resp.status_code, 409)
            self.assertIn("not allowed", resp.json()["detail"])

    def test_non_uuid_returns_404(self):
        with _app_client() as client:
            resp = client.post("/api/design-tasks/not-a-uuid/queue")
            self.assertEqual(resp.status_code, 404)


class RequestDetailDesignTaskSummaryTests(unittest.TestCase):
    def test_request_detail_includes_design_tasks_summary_only(self):
        from domain.research import GenerationRequest

        request_id = uuid4()
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        request = GenerationRequest(
            id=request_id,
            category="web",
            topic="SQLi",
            target_count=2,
            difficulty_distribution=MappingProxyType({"easy": 1, "medium": 1}),
            runtime_constraints=MappingProxyType({}),
            seed_urls=(),
            max_attempts=3,
            status="researched",
            created_at=now,
            updated_at=now,
        )
        research_repo = SimpleNamespace(
            get_generation_request=lambda _: request,
            list_runs=lambda **_kw: [],
            get_latest_run_for_request=lambda _: None,
            get_latest_completed_run_for_request=lambda _: None,
            list_sources=lambda _id: [],
            list_findings=lambda _id: [],
        )
        summary = {
            "total": 2,
            "by_status": {
                "draft": 1,
                "queued": 1,
                "designing": 0,
                "designed": 0,
                "failed": 0,
                "archived": 0,
            },
        }
        design_repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: summary,
            get_with_history=lambda _id: None,
            set_design_task_status=lambda _id, _status: None,
        )
        with _app_client(
            research_repo=research_repo, design_repo=design_repo
        ) as client:
            resp = client.get(f"/api/research/requests/{request_id}")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertNotIn("design_tasks", payload)
            self.assertEqual(payload["design_tasks_summary"], summary)


class DesignTaskReadEndpointTests(unittest.TestCase):
    def test_list_filters_and_returns_lightweight_rows(self):
        request_id = uuid4()
        task = _make_design_task(
            request_id=request_id,
            status="queued",
            diversity_flags={
                "family": "injection",
                "sub_technique": "blind sqli",
                "warnings": [],
            },
        )
        calls = []

        def _list_tasks(**kwargs):
            calls.append(kwargs)
            return [task]

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=_list_tasks,
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: None,
            set_design_task_status=lambda _id, _status: None,
        )
        with _app_client(design_repo=repo) as client:
            resp = client.get(
                "/api/design-tasks"
                f"?generation_request_id={request_id}&status=queued&category=web"
            )
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["id"], str(task.id))
            self.assertEqual(
                payload[0]["diversity_flags"],
                {
                    "family": "injection",
                    "sub_technique": "blind sqli",
                    "warnings": [],
                },
            )
            self.assertNotIn("attempts", payload[0])
            self.assertNotIn("latest_design", payload[0])
            self.assertEqual(calls[0]["generation_request_id"], request_id)
            self.assertEqual(calls[0]["status"], "queued")
            self.assertEqual(calls[0]["category"], "web")

    def test_list_rejects_unknown_status(self):
        with _app_client() as client:
            resp = client.get("/api/design-tasks?status=nonsense")
            self.assertEqual(resp.status_code, 400)
            self.assertIn("allowed", resp.json()["detail"])

    def test_list_rejects_malformed_request_id(self):
        with _app_client() as client:
            resp = client.get("/api/design-tasks?generation_request_id=nope")
            self.assertEqual(resp.status_code, 400)

    def test_detail_returns_attempts_and_latest_design(self):
        task = _make_design_task()
        attempt = _make_attempt(task.id)
        design = _make_design(task.id, attempt.id)

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: (task, [attempt], design),
            set_design_task_status=lambda _id, _status: None,
        )
        with _app_client(design_repo=repo) as client:
            resp = client.get(f"/api/design-tasks/{task.id}")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["id"], str(task.id))
            self.assertEqual(payload["attempts"][0]["attempt"], 1)
            self.assertEqual(payload["latest_design"]["id"], str(design.id))

    def test_detail_unknown_or_malformed_returns_404(self):
        with _app_client() as client:
            self.assertEqual(client.get(f"/api/design-tasks/{uuid4()}").status_code, 404)
            self.assertEqual(client.get("/api/design-tasks/not-a-uuid").status_code, 404)
