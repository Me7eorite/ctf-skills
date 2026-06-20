"""Postgres-backed tests for ResearchAgentExecutor."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from core.paths import ProjectPaths
from hermes.process import HermesProcessResult
from persistence.models import research as model
from persistence.repositories import ResearchRepository
from persistence.session import SessionFactory
from services import ResearchJobService
from services.research_agent_executor import ResearchAgentExecutor

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
        subprocess.run(["uv", "run", "alembic", "downgrade", "base"], cwd=ROOT, env=env, check=False)


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    with session_factory() as session:
        session.execute(sa.delete(model.ResearchFindingSource))
        session.execute(sa.delete(model.ResearchFinding))
        session.execute(sa.delete(model.ResearchSource))
        session.execute(sa.delete(model.HermesProfileBinding))
        session.execute(sa.delete(model.ResearchRun))
        session.execute(sa.delete(model.GenerationRequest))
        session.execute(
            sa.delete(model.ChallengeCategory).where(
                model.ChallengeCategory.code.not_in(["web", "pwn", "re"])
            )
        )
        session.add(
            model.HermesProfileBinding(
                role="research",
                profile_name="default",
                description="default binding",
                status="enabled",
            )
        )
        session.commit()
    yield


@pytest.fixture
def paths(tmp_path: Path) -> ProjectPaths:
    project_paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    project_paths.initialize()
    return project_paths


@pytest.fixture
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(
        "services.research_agent_executor.HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )


def _research_stdout() -> str:
    return json.dumps(
        {
            "sources": [
                {
                    "url": "https://example.com/source",
                    "title": "Source",
                    "summary": "Source summary",
                    "content_hash": "a" * 64,
                    "raw_text": "captured source text",
                }
            ],
            "findings": [
                {
                    "kind": "technique",
                    "label": "UNION SELECT",
                    "summary": "Use UNION SELECT to align columns.",
                    "source_indices": [0],
                }
            ],
        }
    )


def _submit_and_claim(
    session_factory: SessionFactory,
    *,
    max_attempts: int = 3,
):
    service = ResearchJobService(session_factory)
    request, _run = service.submit_request(
        "web",
        "SQL injection",
        1,
        {"easy": 1},
        seed_urls=("https://example.com/seed",),
        max_attempts=max_attempts,
    )
    claimed = service.claim_next_run("worker-1", 60)
    assert claimed is not None
    assert claimed.claim_token is not None
    return request, claimed


def _executor(
    paths: ProjectPaths,
    session_factory: SessionFactory,
    hermes_invoke,
) -> ResearchAgentExecutor:
    return ResearchAgentExecutor(
        paths,
        repository_factory=session_factory,
        hermes_invoke=hermes_invoke,
    )


def _run_executor(executor: ResearchAgentExecutor, run) -> None:
    executor.execute(
        run,
        "worker-1",
        lease_seconds=60,
        hermes_timeout_seconds=30,
    )


def test_executor_happy_path_persists_results_and_touches_binding(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    fast_heartbeat,
    monkeypatch,
):
    request, claimed = _submit_and_claim(session_factory)
    prompts: list[str] = []

    def fake_hermes_invoke(**kwargs):
        prompts.append(kwargs["prompt"])
        assert kwargs["profile_name"] == "default"
        time.sleep(0.05)
        return HermesProcessResult(returncode=0, stdout=_research_stdout(), cancelled=False)

    monkeypatch.setattr("services.research_agent_executor.profile_exists", lambda _name: True)
    _run_executor(_executor(paths, session_factory, fake_hermes_invoke), claimed)

    with session_factory() as session:
        run = session.get(model.ResearchRun, claimed.id)
        parent = session.get(model.GenerationRequest, request.id)
        binding = session.get(model.HermesProfileBinding, "research")
        assert run is not None
        assert parent is not None
        assert binding is not None
        assert run.status == "completed"
        assert run.profile_name_used == "default"
        assert run.heartbeat_at is not None
        assert claimed.claimed_at is not None
        assert run.heartbeat_at > claimed.claimed_at
        assert parent.status == "researched"
        assert binding.last_used_run_id == claimed.id
        assert len(ResearchRepository(session).list_sources(claimed.id)) == 1
        assert len(ResearchRepository(session).list_findings(claimed.id)) == 1

    assert "https://example.com/seed" in prompts[0]
    assert (paths.research_sources / str(claimed.id) / "0.txt").read_text(
        encoding="utf-8"
    ) == "captured source text"


def test_executor_failure_creates_retry_without_touching_binding(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    monkeypatch,
):
    request, claimed = _submit_and_claim(session_factory, max_attempts=2)

    def fake_hermes_invoke(**_kwargs):
        return HermesProcessResult(returncode=7, stdout="", cancelled=False)

    monkeypatch.setattr("services.research_agent_executor.profile_exists", lambda _name: True)
    _run_executor(_executor(paths, session_factory, fake_hermes_invoke), claimed)

    with session_factory() as session:
        run = session.get(model.ResearchRun, claimed.id)
        parent = session.get(model.GenerationRequest, request.id)
        retry = session.scalar(
            sa.select(model.ResearchRun).where(model.ResearchRun.parent_run_id == claimed.id)
        )
        binding = session.get(model.HermesProfileBinding, "research")
        assert run is not None
        assert parent is not None
        assert retry is not None
        assert binding is not None
        assert run.status == "failed"
        assert run.last_error == "Hermes exited with 7"
        assert retry.status == "queued"
        assert retry.attempt == 2
        assert parent.status == "researching"
        assert binding.last_used_run_id is None
        assert ResearchRepository(session).list_sources(claimed.id) == []
        assert ResearchRepository(session).list_findings(claimed.id) == []


def test_executor_final_failure_marks_parent_failed(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    monkeypatch,
):
    request, claimed = _submit_and_claim(session_factory, max_attempts=1)

    def fake_hermes_invoke(**_kwargs):
        return HermesProcessResult(returncode=7, stdout="", cancelled=False)

    monkeypatch.setattr("services.research_agent_executor.profile_exists", lambda _name: True)
    _run_executor(_executor(paths, session_factory, fake_hermes_invoke), claimed)

    with session_factory() as session:
        parent = session.get(model.GenerationRequest, request.id)
        retry_count = session.scalar(
            sa.select(sa.func.count()).where(model.ResearchRun.parent_run_id == claimed.id)
        )
        assert parent is not None
        assert parent.status == "failed"
        assert retry_count == 0


def test_disabled_binding_marks_run_failed_profile_disabled(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    monkeypatch,
):
    # New contract (R1 / D2): disabled binding no longer silently falls back to
    # `default` — it fail-fasts and the run is marked `failed:profile_disabled:<name>`.
    with session_factory() as session:
        binding = session.get(model.HermesProfileBinding, "research")
        assert binding is not None
        binding.profile_name = "ctf-research-bot"
        binding.status = "disabled"
        session.commit()
    _request, claimed = _submit_and_claim(session_factory)
    seen_profiles: list[str] = []

    def fake_hermes_invoke(**kwargs):  # pragma: no cover — must not be called
        seen_profiles.append(kwargs["profile_name"])
        return HermesProcessResult(returncode=0, stdout=_research_stdout(), cancelled=False)

    monkeypatch.setattr("services.research_agent_executor.profile_exists", lambda _name: True)
    _run_executor(_executor(paths, session_factory, fake_hermes_invoke), claimed)

    with session_factory() as session:
        run = session.get(model.ResearchRun, claimed.id)
        assert run is not None
        assert run.status == "failed"
        assert run.last_error == "profile_disabled:ctf-research-bot"
    assert seen_profiles == []


def test_lost_lease_during_hermes_discards_output(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    fast_heartbeat,
    monkeypatch,
):
    _request, claimed = _submit_and_claim(session_factory)

    def fake_hermes_invoke(**kwargs):
        with session_factory() as session:
            row = session.get(model.ResearchRun, claimed.id)
            assert row is not None
            row.claimed_by = "worker-2"
            session.commit()
        deadline = time.monotonic() + 2
        while not kwargs["cancel_event"].is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert kwargs["cancel_event"].is_set()
        return HermesProcessResult(returncode=0, stdout=_research_stdout(), cancelled=False)

    monkeypatch.setattr("services.research_agent_executor.profile_exists", lambda _name: True)
    _run_executor(_executor(paths, session_factory, fake_hermes_invoke), claimed)

    with session_factory() as session:
        run = session.get(model.ResearchRun, claimed.id)
        assert run is not None
        assert run.status == "running"
        assert run.claimed_by == "worker-2"
        assert run.last_error is None
        assert ResearchRepository(session).list_sources(claimed.id) == []
        assert ResearchRepository(session).list_findings(claimed.id) == []


def test_stale_claim_after_hermes_is_logged_without_terminal_write(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    caplog,
    monkeypatch,
):
    _request, claimed = _submit_and_claim(session_factory)
    new_token = uuid4()

    def fake_hermes_invoke(**_kwargs):
        with session_factory() as session:
            row = session.get(model.ResearchRun, claimed.id)
            assert row is not None
            row.claim_token = new_token
            session.commit()
        return HermesProcessResult(returncode=0, stdout=_research_stdout(), cancelled=False)

    monkeypatch.setattr("services.research_agent_executor.profile_exists", lambda _name: True)
    with caplog.at_level(logging.WARNING):
        _run_executor(_executor(paths, session_factory, fake_hermes_invoke), claimed)

    with session_factory() as session:
        run = session.get(model.ResearchRun, claimed.id)
        assert run is not None
        assert run.status == "running"
        assert run.claim_token == new_token
        assert run.last_error is None
        assert ResearchRepository(session).list_sources(claimed.id) == []
        assert ResearchRepository(session).list_findings(claimed.id) == []
    assert "lost claim while completing" in caplog.text
