"""Postgres-backed tests for ChallengeDesignService orchestration."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from core.paths import ProjectPaths
from hermes.process import HERMES_TIMEOUT_RETURNCODE
from persistence.models import challenge_designs as cd_model
from persistence.models import design_tasks as dt_model
from persistence.models import research as model
from persistence.repositories import ChallengeDesignRepository, DesignTaskRepository
from persistence.session import SessionFactory
from services.challenge_design_service import (
    ChallengeDesignConflictError,
    ChallengeDesignService,
)

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def session_factory() -> SessionFactory:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        yield SessionFactory(engine)
    finally:
        engine.dispose()
        subprocess.run(
            ["uv", "run", "alembic", "downgrade", "base"],
            cwd=ROOT,
            env=env,
            check=False,
        )


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    with session_factory() as session:
        session.execute(sa.delete(cd_model.ChallengeDesign))
        session.execute(sa.delete(cd_model.DesignAttempt))
        session.execute(sa.delete(dt_model.DesignTask))
        session.execute(sa.delete(model.ResearchFindingSource))
        session.execute(sa.delete(model.ResearchFinding))
        session.execute(sa.delete(model.ResearchSource))
        session.execute(sa.delete(model.HermesProfileBinding))
        session.execute(sa.delete(model.ResearchRun))
        session.execute(sa.delete(model.GenerationRequest))
        session.commit()
    yield


class FakeDesignExecutor:
    def __init__(self, *, stdout: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.exit_code = exit_code
        self.calls: list[dict[str, object]] = []

    def execute(self, prompt_text, profile_name, timeout_seconds, log_path):
        self.calls.append(
            {
                "prompt_text": prompt_text,
                "profile_name": profile_name,
                "timeout_seconds": timeout_seconds,
                "log_path": Path(log_path),
            }
        )
        return self.stdout, self.exit_code, 0.01


def _context_loader(_paths: ProjectPaths):
    from services.design_prompt import DesignPromptContext

    references = {
        "design-core.md": "design core",
        "category-tactics.md": "category tactics",
        "difficulty-rubric.md": "difficulty rubric",
    }
    return DesignPromptContext(skill_text="design skill", references=references)


def _seed(
    session_factory: SessionFactory,
    *,
    max_attempts: int = 2,
    binding_profile: str | None = "design-profile",
) -> tuple[UUID, UUID]:
    request_id = uuid4()
    run_id = uuid4()
    source_id = uuid4()
    finding_id = uuid4()
    task_id = uuid4()
    with session_factory() as session:
        if binding_profile is not None:
            session.add(
                model.HermesProfileBinding(
                    role="design",
                    profile_name=binding_profile,
                    description="design binding",
                    status="enabled",
                )
            )
        session.add(
            model.GenerationRequest(
                id=request_id,
                category="web",
                topic="SQL injection",
                target_count=1,
                difficulty_distribution={"medium": 1},
                runtime_constraints={},
                seed_urls=[],
                max_attempts=max_attempts,
                status="researched",
            )
        )
        session.add(
            model.ResearchRun(
                id=run_id,
                generation_request_id=request_id,
                attempt=1,
                status="completed",
                finished_at=datetime.now(timezone.utc),
                profile_name_used="default",
            )
        )
        session.add(
            model.ResearchSource(
                id=source_id,
                research_run_id=run_id,
                url="https://example.com/sqli",
                title="SQLi",
                summary="boolean blind SQL injection",
                content_hash="hash",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            model.ResearchFinding(
                id=finding_id,
                research_run_id=run_id,
                kind="technique",
                label="boolean blind sqli",
                summary="branch on truthy responses",
            )
        )
        session.add(model.ResearchFindingSource(finding_id=finding_id, source_id=source_id))
        session.add(
            dt_model.DesignTask(
                id=task_id,
                generation_request_id=request_id,
                research_run_id=run_id,
                task_no=1,
                challenge_id="web-0001",
                title="Blind Login",
                category="web",
                difficulty="medium",
                primary_technique="boolean blind sqli",
                learning_objective="Extract data through conditional responses.",
                points=200,
                port=8081,
                scenario="Login leaks booleans through redirects.",
                constraints={"runtime": "docker"},
                evidence_summary="Finding supports boolean inference.",
                finding_ids=[str(finding_id)],
                status="queued",
            )
        )
        session.commit()
    return task_id, finding_id


def _valid_stdout() -> str:
    # Phase 2 rubric for medium: 2–3 distinct techniques, 2–5
    # intended_path steps, ≥60-char player prompt.
    return json.dumps(
        {
            "event": {"flag_format": "flag{...}"},
            "challenges": [
                {
                    "id": "web-0001",
                    "title": "Blind Login",
                    "category": "web",
                    "difficulty": "medium",
                    "points": 200,
                    "deployment": "single docker compose service on port 8081",
                    "port": 8081,
                    "primary_technique": "boolean blind sqli",
                    "secondary_technique": "session-cookie role flag",
                    "techniques": [
                        "boolean blind sqli",
                        "session-cookie role flag",
                    ],
                    "learning_objective": "Extract data through conditional responses.",
                    "prompt": (
                        "Customer-support agents triage tickets in this portal; "
                        "recover the admin's pinned note."
                    ),
                    "intended_path": [
                        "Spot the conditional response on the login form",
                        "Extract the admin password byte-by-byte",
                        "Log in and read the pinned note",
                    ],
                    "artifacts": [
                        "README.md",
                        "metadata.json",
                        "validate.sh",
                        "deploy/Dockerfile",
                        "deploy/docker-compose.yml",
                        "deploy/src/app.py",
                        "deploy/_files/start.sh",
                        "writenup/wp.md",
                        "writenup/exp.py",
                    ],
                    "flag_location": "FLAG environment variable",
                    "validation": "Run exp.py against the compose service.",
                    "hints": [
                        "Observe redirects.",
                        "Boolean conditions change the response.",
                        "Extract the secret one character at a time.",
                    ],
                }
            ],
        }
    )


def _service(
    tmp_path: Path,
    session_factory: SessionFactory,
    executor: FakeDesignExecutor,
) -> ChallengeDesignService:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    return ChallengeDesignService(
        paths=paths,
        session_factory=session_factory,
        executor=executor,  # type: ignore[arg-type]
        timeout_seconds=30,
        prompt_context_loader=_context_loader,
    )


def test_happy_path_inserts_design_and_marks_task_designed(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, finding_id = _seed(session_factory)
    executor = FakeDesignExecutor(stdout=_valid_stdout())

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "designed"
    assert result.attempt_status == "completed"
    assert result.error is None
    assert result.challenge_design is not None
    assert executor.calls[0]["profile_name"] == "design-profile"
    assert executor.calls[0]["timeout_seconds"] == 30
    assert "boolean blind sqli" in executor.calls[0]["prompt_text"]
    prompt_path = tmp_path / "work" / "design" / "prompts" / f"{result.attempt_id}.md"
    assert prompt_path.exists()

    with session_factory() as session:
        task = DesignTaskRepository(session).get_design_task(task_id)
        assert task is not None and task.status == "designed"
        attempts = ChallengeDesignRepository(session).list_attempts(task_id)
        assert len(attempts) == 1
        assert attempts[0].prompt_path == f"work/design/prompts/{result.attempt_id}.md"
        assert attempts[0].hermes_log_path == f"work/design/logs/{result.attempt_id}.log"
        assert ChallengeDesignRepository(session).latest_design(task_id) is not None


def test_schema_invalid_requeues_when_retry_remains(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=2)
    executor = FakeDesignExecutor(stdout="{}")

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "queued"
    assert result.attempt_status == "failed"
    assert result.challenge_design is None
    assert "event" in result.error
    assert "challenges" in result.error
    with session_factory() as session:
        assert DesignTaskRepository(session).get_design_task(task_id).status == "queued"
        attempts = ChallengeDesignRepository(session).list_attempts(task_id)
        assert len(attempts) == 1
        assert attempts[0].attempt == 1
        assert attempts[0].last_error == result.error


def test_exhausted_retry_marks_task_failed(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=1)
    executor = FakeDesignExecutor(stdout="{}")

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "failed"
    assert result.attempt_status == "failed"
    assert "event" in result.error
    assert "challenges" in result.error


def test_timeout_path_records_timeout(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=2)
    executor = FakeDesignExecutor(exit_code=HERMES_TIMEOUT_RETURNCODE)

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "queued"
    assert result.attempt_status == "failed"
    assert result.error == "timeout"


def test_missing_design_binding_falls_back_to_default_profile(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, binding_profile=None)
    executor = FakeDesignExecutor(stdout=_valid_stdout())

    _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert executor.calls[0]["profile_name"] == "default"


def test_second_call_after_success_returns_conflict(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory)
    executor = FakeDesignExecutor(stdout=_valid_stdout())
    service = _service(tmp_path, session_factory, executor)
    service.design_for_task(task_id, "alice")

    with pytest.raises(ChallengeDesignConflictError, match="expected queued"):
        service.design_for_task(task_id, "bob")
