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
            self.assertEqual(len(payload["design_tasks"]), 2)
            self.assertEqual(
                [t["task_no"] for t in payload["design_tasks"]], [1, 2]
            )
            self.assertEqual(
                {t["status"] for t in payload["design_tasks"]}, {"draft"}
            )

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


class RequestDetailIncludesDesignTasksTests(unittest.TestCase):
    def test_request_detail_includes_design_tasks_field(self):
        from domain.research import GenerationRequest

        request_id = uuid4()
        run_id = uuid4()
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
            list_sources=lambda _id: [],
            list_findings=lambda _id: [],
        )
        design_tasks = [
            _make_design_task(request_id=request_id, run_id=run_id, task_no=1),
            _make_design_task(request_id=request_id, run_id=run_id, task_no=2),
        ]
        design_repo = SimpleNamespace(
            list_design_tasks=lambda _id: design_tasks,
            set_design_task_status=lambda _id, _status: None,
        )
        with _app_client(
            research_repo=research_repo, design_repo=design_repo
        ) as client:
            resp = client.get(f"/api/research/requests/{request_id}")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(len(payload["design_tasks"]), 2)
            self.assertEqual(
                [t["task_no"] for t in payload["design_tasks"]], [1, 2]
            )
