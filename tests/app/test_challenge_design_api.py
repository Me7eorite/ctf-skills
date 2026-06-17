"""HTTP tests for structured challenge-design attempt endpoints."""

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
from domain.design_tasks import DesignTask
from domain.research import GenerationRequest
from services.challenge_design_service import (
    ChallengeDesignConflictError,
    ChallengeDesignNotFoundError,
    ChallengeDesignServiceResult,
)
from web.dashboard import DashboardService
from web.server import create_app


@contextlib.contextmanager
def _app_client(*, service_factory=None, challenge_repo=None, design_repo=None, research_repo=None):
    temp = tempfile.TemporaryDirectory()
    try:
        paths = ProjectPaths(root=Path(temp.name), repository=Path(temp.name))
        paths.initialize()
        app = create_app(DashboardService(paths))
        fake_session = SimpleNamespace(
            scalar=lambda _stmt: None,
            scalars=lambda _stmt: [],
        )

        @contextlib.contextmanager
        def _ctx():
            yield fake_session

        default_challenge_repo = SimpleNamespace(
            get_attempt=lambda _attempt_id: None,
            list_attempts=lambda _task_id: [],
            latest_design=lambda _task_id: None,
        )
        default_design_repo = SimpleNamespace(
            list_design_tasks=lambda _request_id: [],
            set_design_task_status=lambda _task_id, _status: None,
        )
        default_research_repo = SimpleNamespace()
        patches = [
            patch("persistence.session.transaction", _ctx),
            patch(
                "persistence.repositories.ChallengeDesignRepository",
                return_value=challenge_repo or default_challenge_repo,
            ),
            patch(
                "persistence.repositories.DesignTaskRepository",
                return_value=design_repo or default_design_repo,
            ),
            patch(
                "persistence.repositories.ResearchRepository",
                return_value=research_repo or default_research_repo,
            ),
        ]
        if service_factory is not None:
            patches.append(patch("services.ChallengeDesignService", service_factory))
        for item in patches:
            item.start()
        try:
            yield TestClient(app), paths
        finally:
            for item in patches:
                item.stop()
    finally:
        temp.cleanup()


def _design(task_id: UUID, attempt_id: UUID) -> ChallengeDesign:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return ChallengeDesign(
        id=uuid4(),
        design_task_id=task_id,
        design_attempt_id=attempt_id,
        payload=MappingProxyType({"id": "web-0001", "title": "Blind Login"}),
        summary="Blind Login uses boolean inference.",
        flag_format="flag{...}",
        validation_notes="validated",
        quality_gate_passed=True,
        status="draft",
        created_at=now,
        updated_at=now,
    )


def _attempt(task_id: UUID, *, attempt_no: int = 1, status: str = "completed") -> DesignAttempt:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    attempt_id = uuid4()
    return DesignAttempt(
        id=attempt_id,
        design_task_id=task_id,
        attempt=attempt_no,
        status=status,
        claimed_by="alice",
        claim_token=uuid4(),
        started_at=now,
        finished_at=now if status != "running" else None,
        profile_name_used="default",
        prompt_path=f"work/design/prompts/{attempt_id}.md",
        hermes_log_path=f"work/design/logs/{attempt_id}.log",
        last_error=None if status == "completed" else "bad schema",
        created_at=now,
    )


class FakeChallengeDesignService:
    result: ChallengeDesignServiceResult | None = None
    error: Exception | None = None
    calls: list[tuple[UUID, str]] = []

    def __init__(self, *_, **__):
        pass

    def design_for_task(self, task_id: UUID, caller: str) -> ChallengeDesignServiceResult:
        self.__class__.calls.append((task_id, caller))
        if self.__class__.error is not None:
            raise self.__class__.error
        assert self.__class__.result is not None
        return self.__class__.result


class DesignEndpointTests(unittest.TestCase):
    def setUp(self):
        FakeChallengeDesignService.result = None
        FakeChallengeDesignService.error = None
        FakeChallengeDesignService.calls = []

    def test_success_returns_completed_design_without_retry_available(self):
        task_id = uuid4()
        attempt_id = uuid4()
        design = _design(task_id, attempt_id)
        FakeChallengeDesignService.result = ChallengeDesignServiceResult(
            design_task_id=task_id,
            attempt_id=attempt_id,
            design_task_status="designed",
            attempt_status="completed",
            challenge_design=design,
            error=None,
        )

        with _app_client(service_factory=FakeChallengeDesignService) as (client, _paths):
            resp = client.post(f"/api/design-tasks/{task_id}/design")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["design_task_status"], "designed")
        self.assertEqual(payload["attempt_status"], "completed")
        self.assertEqual(payload["challenge_design"]["id"], str(design.id))
        self.assertIsNone(payload["error"])
        self.assertNotIn("retry_available", payload)

    def test_not_found_and_conflict_translate_to_http_errors(self):
        task_id = uuid4()
        FakeChallengeDesignService.error = ChallengeDesignNotFoundError("missing")
        with _app_client(service_factory=FakeChallengeDesignService) as (client, _paths):
            self.assertEqual(client.post(f"/api/design-tasks/{task_id}/design").status_code, 404)

        FakeChallengeDesignService.error = ChallengeDesignConflictError("expected queued")
        with _app_client(service_factory=FakeChallengeDesignService) as (client, _paths):
            resp = client.post(f"/api/design-tasks/{task_id}/design")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("expected queued", resp.json()["detail"])

    def test_validation_failure_returns_200_with_failed_attempt(self):
        task_id = uuid4()
        attempt_id = uuid4()
        FakeChallengeDesignService.result = ChallengeDesignServiceResult(
            design_task_id=task_id,
            attempt_id=attempt_id,
            design_task_status="queued",
            attempt_status="failed",
            challenge_design=None,
            error="event must be an object",
        )

        with _app_client(service_factory=FakeChallengeDesignService) as (client, _paths):
            resp = client.post(f"/api/design-tasks/{task_id}/design")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["design_task_status"], "queued")
        self.assertEqual(payload["attempt_status"], "failed")
        self.assertIsNone(payload["challenge_design"])
        self.assertIn("event must be an object", payload["error"])
        self.assertNotIn("retry_available", payload)

    def test_exhausted_failure_returns_failed_task_without_retry_available(self):
        task_id = uuid4()
        attempt_id = uuid4()
        FakeChallengeDesignService.result = ChallengeDesignServiceResult(
            design_task_id=task_id,
            attempt_id=attempt_id,
            design_task_status="failed",
            attempt_status="failed",
            challenge_design=None,
            error="timeout",
        )

        with _app_client(service_factory=FakeChallengeDesignService) as (client, _paths):
            resp = client.post(f"/api/design-tasks/{task_id}/design")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["design_task_status"], "failed")
        self.assertNotIn("retry_available", resp.json())

    def test_retry_is_operator_triggered_second_call(self):
        task_id = uuid4()
        first_attempt = uuid4()
        second_attempt = uuid4()
        results = [
            ChallengeDesignServiceResult(
                design_task_id=task_id,
                attempt_id=first_attempt,
                design_task_status="queued",
                attempt_status="failed",
                challenge_design=None,
                error="bad schema",
            ),
            ChallengeDesignServiceResult(
                design_task_id=task_id,
                attempt_id=second_attempt,
                design_task_status="failed",
                attempt_status="failed",
                challenge_design=None,
                error="bad schema",
            ),
        ]

        class RetryService(FakeChallengeDesignService):
            def design_for_task(self, task_id: UUID, caller: str) -> ChallengeDesignServiceResult:
                self.__class__.calls.append((task_id, caller))
                return results.pop(0)

        with _app_client(service_factory=RetryService) as (client, _paths):
            first = client.post(f"/api/design-tasks/{task_id}/design")
            second = client.post(f"/api/design-tasks/{task_id}/design")

        self.assertEqual(first.json()["design_task_status"], "queued")
        self.assertEqual(second.json()["design_task_status"], "failed")
        self.assertEqual(first.json()["attempt_id"], str(first_attempt))
        self.assertEqual(second.json()["attempt_id"], str(second_attempt))


class RequestDetailDesignAttemptTests(unittest.TestCase):
    def test_request_detail_includes_attempts_and_latest_design(self):
        request_id = uuid4()
        task_id = uuid4()
        run_id = uuid4()
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        request = GenerationRequest(
            id=request_id,
            category="web",
            topic="SQLi",
            target_count=1,
            difficulty_distribution=MappingProxyType({"medium": 1}),
            runtime_constraints=MappingProxyType({}),
            seed_urls=(),
            max_attempts=2,
            status="researched",
            created_at=now,
            updated_at=now,
        )
        task = DesignTask(
            id=task_id,
            generation_request_id=request_id,
            research_run_id=run_id,
            task_no=1,
            challenge_id="web-0001",
            title="Blind Login",
            category="web",
            difficulty="medium",
            primary_technique="sqli",
            learning_objective="extract data",
            points=200,
            port=8080,
            scenario="login",
            constraints=MappingProxyType({}),
            evidence_summary="evidence",
            finding_ids=(),
            status="designed",
            created_at=now,
            updated_at=now,
        )
        attempt = _attempt(task_id)
        research_repo = SimpleNamespace(
            get_generation_request=lambda _id: request,
            list_runs=lambda **_kw: [],
            get_latest_run_for_request=lambda _id: None,
            list_sources=lambda _id: [],
            list_findings=lambda _id: [],
        )
        design_repo = SimpleNamespace(
            list_design_tasks=lambda _id: [task],
            set_design_task_status=lambda _id, _status: None,
        )
        challenge_repo = SimpleNamespace(
            get_attempt=lambda _id: None,
            list_attempts=lambda _id: [attempt],
            latest_design=lambda _id: _design(task_id, attempt.id),
        )

        with _app_client(
            research_repo=research_repo,
            design_repo=design_repo,
            challenge_repo=challenge_repo,
        ) as (client, _paths):
            resp = client.get(f"/api/research/requests/{request_id}")

        self.assertEqual(resp.status_code, 200)
        task_payload = resp.json()["design_tasks"][0]
        self.assertEqual(task_payload["attempts"][0]["id"], str(attempt.id))
        self.assertEqual(
            task_payload["attempts"][0]["prompt_artifact_url"],
            f"/api/design-attempts/{attempt.id}/artifact?kind=prompt",
        )
        self.assertIsNotNone(task_payload["latest_design"])


class DesignArtifactEndpointTests(unittest.TestCase):
    def test_serves_prompt_and_log(self):
        task_id = uuid4()
        attempt = _attempt(task_id)
        challenge_repo = SimpleNamespace(
            get_attempt=lambda _id: attempt,
            list_attempts=lambda _id: [],
            latest_design=lambda _id: None,
        )
        with _app_client(challenge_repo=challenge_repo) as (client, paths):
            prompt = paths.design_prompts / f"{attempt.id}.md"
            log = paths.design_logs / f"{attempt.id}.log"
            prompt.write_text("prompt body", encoding="utf-8")
            log.write_text("log body", encoding="utf-8")

            prompt_resp = client.get(f"/api/design-attempts/{attempt.id}/artifact?kind=prompt")
            log_resp = client.get(f"/api/design-attempts/{attempt.id}/artifact?kind=log")

        self.assertEqual(prompt_resp.status_code, 200)
        self.assertEqual(prompt_resp.text, "prompt body")
        self.assertEqual(log_resp.status_code, 200)
        self.assertEqual(log_resp.text, "log body")

    def test_rejects_traversal_arbitrary_paths_and_unknown_kind(self):
        task_id = uuid4()
        attempt = _attempt(task_id)
        traversal = DesignAttempt(
            **{
                **attempt.__dict__,
                "prompt_path": "work/design/prompts/../logs/escape.log",
            }
        )
        arbitrary = DesignAttempt(
            **{
                **attempt.__dict__,
                "prompt_path": "work/logs/dashboard.log",
            }
        )

        for bad_attempt in (traversal, arbitrary):
            challenge_repo = SimpleNamespace(
                get_attempt=lambda _id, bad_attempt=bad_attempt: bad_attempt,
                list_attempts=lambda _id: [],
                latest_design=lambda _id: None,
            )
            with _app_client(challenge_repo=challenge_repo) as (client, _paths):
                resp = client.get(f"/api/design-attempts/{bad_attempt.id}/artifact?kind=prompt")
            self.assertEqual(resp.status_code, 403)

        challenge_repo = SimpleNamespace(
            get_attempt=lambda _id: attempt,
            list_attempts=lambda _id: [],
            latest_design=lambda _id: None,
        )
        with _app_client(challenge_repo=challenge_repo) as (client, _paths):
            resp = client.get(f"/api/design-attempts/{attempt.id}/artifact?kind=stdout")
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
