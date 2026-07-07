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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from domain.challenge_designs import ChallengeDesign, DesignAttempt
from domain.design.difficulty_review import DesignDifficultyReview
from domain.design.profile_taxonomy import DesignDiversityExhausted, ProfileTaxonomyError
from domain.design_evidence import DesignEvidence
from domain.design_profile_reservations import DesignProfileReservation
from domain.design_task_validators import DesignTaskValidationError
from domain.design_tasks import DesignTask
from services.challenge_design_service import ChallengeDesignServiceResult
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


def _make_reservation(
    task: DesignTask,
    *,
    state: str = "committed",
) -> DesignProfileReservation:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    profile = {
        "semantic": {"family": "web", "sub_technique": "boolean blind sqli"},
        "solve": {"required_action": "extract_with_boolean_oracle"},
        "implementation": {
            "artifact_format": "web_app",
            "language": "python",
            "interaction": "http_form",
            "flag_concealment": "database_record",
        },
        "presentation": {"scenario_type": "login", "input_model": "form"},
    }
    return DesignProfileReservation(
        id=uuid4(),
        design_task_id=task.id,
        generation_request_id=task.generation_request_id,
        reservation_version=1,
        profile=profile,
        profile_signature="profile-a",
        occupancy_scope="web",
        exclusive_signature_key="profile-a",
        state=state,
        taxonomy_version=1,
        policy_version=1,
        ledger_version=1,
        created_at=now,
        committed_at=now if state == "committed" else None,
        released_at=now if state == "released" else None,
    )


def _make_evidence(
    task: DesignTask,
    design: ChallengeDesign,
    *,
    superseded: bool = False,
) -> DesignEvidence:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    profile = {
        "semantic": {"family": "web", "sub_technique": "boolean blind sqli"},
        "solve": {"required_action": "extract_with_boolean_oracle"},
        "implementation": {
            "artifact_format": "web_app",
            "language": "python",
            "interaction": "http_form",
            "flag_concealment": "database_record",
        },
        "presentation": {"scenario_type": "login", "input_model": "form"},
    }
    return DesignEvidence(
        id=uuid4(),
        design_task_id=task.id,
        evidence_version=2 if not superseded else 1,
        challenge_design_id=design.id,
        research_finding_ids=task.finding_ids,
        profile=profile,
        profile_signature="profile-a",
        distinctness_claim="differs from sibling solve flow",
        compared_challenge_ids=("web-0000",),
        evidence={"claims": [{"finding_id": str(task.finding_ids[0])}]},
        build_contract={
            "required_profile": profile,
            "required_player_actions": ["extract_with_boolean_oracle"],
            "required_components": ["login"],
            "artifact_ids": ["primary"],
            "fixture_ids": ["oracle"],
            "required_asset_flow": [
                {
                    "stage_id": "oracle",
                    "produced_asset_or_capability": "boolean oracle",
                    "verification_harness": {
                        "test_kind": "fixture_assertion",
                        "fixture_ref": "oracle",
                        "assertion": "non_empty",
                    },
                    "dependency_harness": {
                        "test_kind": "solver_without_fixture",
                        "fixture_ref": "oracle",
                        "assertion": "must_fail",
                    },
                }
            ],
            "forbidden_shortcuts": [],
            "acceptance_tests": [],
            "allowed_implementation_freedom": ["function_names"],
        },
        ledger_version=1,
        created_at=now,
        superseded_at=now if superseded else None,
        supersession_reason="revision" if superseded else None,
    )


def _make_review(task_id: UUID, design_id: UUID) -> DesignDifficultyReview:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return DesignDifficultyReview(
        id=uuid4(),
        design_task_id=task_id,
        challenge_design_id=design_id,
        passed=False,
        claimed_difficulty="medium",
        actual_difficulty="below_claimed",
        confidence=0.9,
        reasons=("medium requires a required asset/capability chain",),
        detected_risks=("declared difficulty may be step inflation",),
        required_revision=("revise asset_flow",),
        reviewer="deterministic-asset-flow",
        created_at=now,
    )


@contextlib.contextmanager
def _app_client(
    *,
    planning_service=None,
    design_service=None,
    design_repo=None,
    research_repo=None,
    difficulty_review_repo=None,
    evidence_repo=None,
    reservation_repo=None,
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
        default_difficulty_review_repo = SimpleNamespace(
            summarize_for_design_task=lambda _task_id: {
                "total": 0,
                "failed": 0,
                "latest": None,
            },
        )
        default_evidence_repo = SimpleNamespace(
            get=lambda _id: None,
            list_for_task=lambda _task_id: [],
        )
        default_reservation_repo = SimpleNamespace(
            get=lambda _id: None,
            list_for_task=lambda _task_id: [],
        )
        default_planning_service = SimpleNamespace(
            generate_for_request=lambda _request_id: [],
        )
        default_design_service = SimpleNamespace()
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
                "persistence.repositories.DesignDifficultyReviewRepository",
                return_value=difficulty_review_repo or default_difficulty_review_repo,
            ),
            patch(
                "persistence.repositories.DesignEvidenceRepository",
                return_value=evidence_repo or default_evidence_repo,
            ),
            patch(
                "persistence.repositories.DesignProfileReservationRepository",
                return_value=reservation_repo or default_reservation_repo,
            ),
            patch(
                "services.DesignTaskPlanningService",
                return_value=planning_service or default_planning_service,
            ),
            patch(
                "services.ChallengeDesignService",
                return_value=design_service or default_design_service,
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

    def test_generate_busy_returns_stable_code(self):
        def _busy(_id):
            raise DesignTaskValidationError(
                "generation request is busy",
                code="generation_request_busy",
            )

        planner = SimpleNamespace(generate_for_request=_busy)
        with _app_client(planning_service=planner) as client:
            resp = client.post(
                f"/api/research/requests/{uuid4()}/design-tasks/generate"
            )
            self.assertEqual(resp.status_code, 409)
            self.assertEqual(
                resp.json()["detail"],
                {
                    "code": "generation_request_busy",
                    "message": "generation request is busy",
                },
            )

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

    def test_regenerate_one_profile_taxonomy_error_returns_structured_400(self):
        request_id = uuid4()

        def _raise(_id, _task_no):
            raise ProfileTaxonomyError("profile semantic.sub_technique='x' is not in closed vocabulary")

        planner = SimpleNamespace(regenerate_task=_raise)

        with _app_client(planning_service=planner) as client:
            resp = client.post(
                f"/api/research/requests/{request_id}/design-tasks/1/regenerate"
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "profile_taxonomy_error")
        self.assertIn("closed vocabulary", resp.json()["message"])

    def test_regenerate_all_diversity_exhausted_returns_structured_409(self):
        request_id = uuid4()

        def _raise(_id):
            raise DesignDiversityExhausted(
                {"code": "design_diversity_exhausted", "available_count": 0}
            )

        planner = SimpleNamespace(regenerate_plan=_raise)

        with _app_client(planning_service=planner) as client:
            resp = client.post(
                f"/api/research/requests/{request_id}/design-tasks/regenerate"
            )

        self.assertEqual(resp.status_code, 409)
        payload = resp.json()
        self.assertEqual(payload["code"], "design_diversity_exhausted")
        self.assertEqual(payload["details"]["available_count"], 0)

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

    def test_batch_queue_transitions_selected_tasks(self):
        task_ids = [uuid4(), uuid4()]
        seen = []

        def _set(task_uuid, status):
            seen.append((task_uuid, status))
            return _make_design_task(status="queued")

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: None,
            set_design_task_status=_set,
        )
        with _app_client(design_repo=repo) as client:
            resp = client.post(
                "/api/design-tasks/queue",
                json={"design_task_ids": [str(task_id) for task_id in task_ids]},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["total"], 2)
            self.assertEqual(seen, [(task_ids[0], "queued"), (task_ids[1], "queued")])

    def test_batch_design_runs_selected_tasks_with_bounded_concurrency(self):
        task_ids = [uuid4(), uuid4()]
        seen = []

        def _design(task_uuid, caller):
            seen.append((task_uuid, caller))
            return ChallengeDesignServiceResult(
                design_task_id=task_uuid,
                attempt_id=uuid4(),
                design_task_status="designed",
                attempt_status="completed",
                challenge_design=None,
                error=None,
            )

        design_service = SimpleNamespace(design_for_task=_design)
        with _app_client(design_service=design_service) as client:
            resp = client.post(
                "/api/design-tasks/design",
                json={
                    "design_task_ids": [str(task_id) for task_id in task_ids],
                    "concurrency": 99,
                },
            )
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["total"], 2)
            self.assertEqual(payload["failed"], 0)
            self.assertEqual(payload["concurrency"], 4)
            self.assertEqual([item["design_task_status"] for item in payload["results"]], ["designed", "designed"])
            self.assertEqual({item[1] for item in seen}, {"dashboard-batch"})

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
        review_repo = SimpleNamespace(
            summarize_for_design_task=lambda _task_id: {
                "total": 2,
                "failed": 1,
                "latest": None,
            },
        )
        with _app_client(design_repo=repo, difficulty_review_repo=review_repo) as client:
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
            self.assertEqual(payload[0]["difficulty_review_summary"]["failed"], 1)
            self.assertEqual(calls[0]["generation_request_id"], request_id)
            self.assertEqual(calls[0]["status"], "queued")
            self.assertEqual(calls[0]["category"], "web")

    def test_collapse_endpoint_empty_request_reports_zero(self):
        request_id = uuid4()
        with _app_client() as client:
            resp = client.get(
                f"/api/design-tasks/collapse?generation_request_id={request_id}"
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["total"], 0)
            self.assertFalse(body["collapsed"])
            self.assertEqual(body["generation_request_id"], str(request_id))

    def test_collapse_endpoint_rejects_bad_uuid(self):
        with _app_client() as client:
            resp = client.get(
                "/api/design-tasks/collapse?generation_request_id=nope"
            )
            self.assertEqual(resp.status_code, 400)

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
        review = _make_review(task.id, design.id)

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: (task, [attempt], design),
            set_design_task_status=lambda _id, _status: None,
        )
        review_repo = SimpleNamespace(
            summarize_for_design_task=lambda _task_id: {
                "total": 1,
                "failed": 1,
                "latest": review,
            },
        )
        with _app_client(design_repo=repo, difficulty_review_repo=review_repo) as client:
            resp = client.get(f"/api/design-tasks/{task.id}")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["id"], str(task.id))
            self.assertEqual(payload["attempts"][0]["attempt"], 1)
            self.assertEqual(payload["latest_design"]["id"], str(design.id))
            summary = payload["difficulty_review_summary"]
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertFalse(summary["latest"]["passed"])
            self.assertEqual(summary["latest"]["required_revision"], ["revise asset_flow"])

    def test_detail_exposes_current_governance_chain_and_history(self):
        task = _make_design_task(status="designed")
        attempt = _make_attempt(task.id)
        design = _make_design(task.id, attempt.id)
        design = replace(design, quality_gate_passed=False)
        reservation = _make_reservation(task)
        evidence = _make_evidence(task, design)
        superseded_reservation = _make_reservation(task, state="released")
        superseded_evidence = _make_evidence(task, design, superseded=True)

        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: (task, [attempt], design),
            set_design_task_status=lambda _id, _status: None,
        )
        evidence_repo = SimpleNamespace(
            get=lambda evidence_id: evidence if evidence_id == evidence.id else None,
            list_for_task=lambda _task_id: [superseded_evidence, evidence],
        )
        reservation_repo = SimpleNamespace(
            get=lambda reservation_id: reservation if reservation_id == reservation.id else None,
            list_for_task=lambda _task_id: [superseded_reservation, reservation],
        )

        task = replace(
            task,
            current_reservation_id=reservation.id,
            current_design_evidence_id=evidence.id,
        )
        repo = SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: (task, [attempt], design),
            set_design_task_status=lambda _id, _status: None,
        )

        with _app_client(
            design_repo=repo,
            evidence_repo=evidence_repo,
            reservation_repo=reservation_repo,
        ) as client:
            resp = client.get(f"/api/design-tasks/{task.id}")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["current_reservation"]["id"], str(reservation.id))
        self.assertEqual(payload["current_design_evidence"]["id"], str(evidence.id))
        self.assertFalse(payload["build_eligibility"]["eligible"])
        self.assertIn("design_quality_gate_failed", payload["build_eligibility"]["blocking_reasons"])
        self.assertEqual(payload["governance_history"]["reservations"][0]["id"], str(superseded_reservation.id))
        self.assertEqual(payload["governance_history"]["design_evidence"][0]["id"], str(superseded_evidence.id))

    def test_detail_unknown_or_malformed_returns_404(self):
        with _app_client() as client:
            self.assertEqual(client.get(f"/api/design-tasks/{uuid4()}").status_code, 404)
            self.assertEqual(client.get("/api/design-tasks/not-a-uuid").status_code, 404)

    def test_revision_endpoint_calls_service_and_returns_design_task(self):
        task = _make_design_task(status="designed")
        service = SimpleNamespace(
            request_design_revision=lambda task_id, reason: replace(task, status="draft"),
        )
        with _app_client(planning_service=service, design_repo=SimpleNamespace(
            list_design_tasks=lambda _id: [],
            list_tasks=lambda **_kw: [],
            summarize_for_request=lambda _id: {"total": 0, "by_status": {}},
            get_with_history=lambda _id: (task, [], None),
            set_design_task_status=lambda _id, _status: None,
        )) as client:
            resp = client.post(f"/api/design-tasks/{task.id}/revision", json={"reason": "quality"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["reason"], "quality")
        self.assertEqual(payload["design_task"]["status"], "draft")
