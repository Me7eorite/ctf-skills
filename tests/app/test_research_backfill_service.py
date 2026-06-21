"""Postgres-backed tests for research result backfill preview/apply."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import cli
from core.paths import ProjectPaths
from persistence.models import research as model
from persistence.repositories import ResearchRepository
from persistence.session import SessionFactory
from services import ResearchJobService
from services import research_backfill_service as backfill_module
from services.research_backfill_service import ResearchBackfillError, ResearchBackfillService
from web.dashboard import DashboardService
from web.server import create_app

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


def _stdout(*, raw_text: str = "captured source text") -> str:
    return json.dumps(
        {
            "sources": [
                {
                    "url": "https://example.com/source",
                    "title": "Source",
                    "summary": "Source summary",
                    "content_hash": "a" * 64,
                    "raw_text": raw_text,
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


def _log(stdout: str) -> str:
    return f"header\n--- stdout ---\n{stdout}\n--- end stdout ---\n"


def _failed_run(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    *,
    max_attempts: int = 1,
    stdout: str | None = None,
):
    service = ResearchJobService(session_factory)
    request, _run = service.submit_request("web", "backfill", 1, {"easy": 1}, max_attempts=max_attempts)
    claimed = service.claim_next_run("worker-1", 60)
    assert claimed is not None
    assert claimed.claim_token is not None
    log_path = paths.research_logs / f"{claimed.id}.log"
    log_path.write_text(_log(stdout or _stdout()), encoding="utf-8")
    service.mark_run_failed(
        claimed.id,
        "worker-1",
        claimed.claim_token,
        "Hermes exited with 124",
        log_path=log_path,
    )
    return request, claimed, log_path


def test_preview_is_pure_and_apply_persists_results(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    request, run, _log_path = _failed_run(session_factory, paths)
    service = ResearchBackfillService(paths, session_factory)

    preview = service.preview(run.id)

    assert preview.run_id == run.id
    assert preview.generation_request_id == request.id
    assert preview.would_insert_sources == 1
    assert preview.would_insert_findings == 1
    assert preview.current_run_status == "failed"
    assert preview.would_run_status == "completed"
    assert len(preview.log_sha256) == 64
    assert not (paths.research_sources_staging / str(run.id)).exists()
    assert not (paths.research_sources / str(run.id)).exists()

    result = service.apply(run.id, preview.log_sha256)

    assert result.inserted_sources == 1
    assert result.inserted_findings == 1
    assert result.run_status == "completed"
    assert result.request_status == "researched"
    assert (paths.research_sources / str(run.id) / "0.txt").read_text(
        encoding="utf-8"
    ) == "captured source text"
    with session_factory() as session:
        row = session.get(model.ResearchRun, run.id)
        parent = session.get(model.GenerationRequest, request.id)
        assert row is not None
        assert parent is not None
        assert row.status == "completed"
        assert row.last_error is None
        assert parent.status == "researched"
        assert len(ResearchRepository(session).list_sources(run.id)) == 1
        assert len(ResearchRepository(session).list_findings(run.id)) == 1


def test_single_run_cli_dry_run_is_end_to_end_and_read_only(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    request, run, _log_path = _failed_run(session_factory, paths)

    with session_factory() as session:
        before = (
            session.get(model.GenerationRequest, request.id).status,
            session.get(model.ResearchRun, run.id).status,
            session.scalar(sa.select(sa.func.count()).select_from(model.ResearchSource)),
            session.scalar(sa.select(sa.func.count()).select_from(model.ResearchFinding)),
        )
    before_tree = sorted(
        (path.relative_to(paths.root).as_posix(), path.read_bytes())
        for path in paths.root.rglob("*")
        if path.is_file()
    )
    monkeypatch.setattr(cli, "_check_category_consistency", lambda: None)
    monkeypatch.setattr(cli.ProjectPaths, "discover", lambda: paths)
    monkeypatch.setattr(backfill_module, "SessionFactory", lambda: session_factory)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "challenge-factory",
            "research",
            "backfill",
            "--run-id",
            str(run.id),
            "--dry-run",
        ],
    )

    cli.main()

    assert capsys.readouterr().out.startswith(f"[backfill] {run.id} preview")
    with session_factory() as session:
        after = (
            session.get(model.GenerationRequest, request.id).status,
            session.get(model.ResearchRun, run.id).status,
            session.scalar(sa.select(sa.func.count()).select_from(model.ResearchSource)),
            session.scalar(sa.select(sa.func.count()).select_from(model.ResearchFinding)),
        )
    after_tree = sorted(
        (path.relative_to(paths.root).as_posix(), path.read_bytes())
        for path in paths.root.rglob("*")
        if path.is_file()
    )
    assert after == before
    assert after_tree == before_tree


def test_batch_cli_applies_valid_fixture_and_continues_after_failure(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    valid_request, valid_run, _ = _failed_run(session_factory, paths)
    failed_request, failed_run, failed_log = _failed_run(session_factory, paths)
    failed_log.write_text(
        "header\n--- stdout ---\nnot-json\n--- end stdout ---\n",
        encoding="utf-8",
    )

    @contextlib.contextmanager
    def test_transaction():
        with session_factory() as session:
            yield session

    monkeypatch.setattr(cli, "_check_category_consistency", lambda: None)
    monkeypatch.setattr(cli.ProjectPaths, "discover", lambda: paths)
    monkeypatch.setattr(backfill_module, "SessionFactory", lambda: session_factory)
    monkeypatch.setattr("persistence.session.transaction", test_transaction)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "challenge-factory",
            "research",
            "backfill",
            "--all-recoverable",
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    output = capsys.readouterr().out
    assert f"[backfill] {valid_run.id} applied sources=1 findings=1" in output
    assert f"[backfill] {failed_run.id} error=parse_failed" in output
    assert "summary succeeded=1 skipped=0 failed=1" in output
    with session_factory() as session:
        assert session.get(model.ResearchRun, valid_run.id).status == "completed"
        assert session.get(model.GenerationRequest, valid_request.id).status == "researched"
        assert session.get(model.ResearchRun, failed_run.id).status == "failed"
        assert session.get(model.GenerationRequest, failed_request.id).status == "failed"


def test_apply_rejects_stale_digest_without_materializing(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    _request, run, log_path = _failed_run(session_factory, paths)
    service = ResearchBackfillService(paths, session_factory)
    preview = service.preview(run.id)
    log_path.write_text(_log(_stdout(raw_text="changed text")), encoding="utf-8")

    with pytest.raises(ResearchBackfillError) as excinfo:
        service.apply(run.id, preview.log_sha256)

    assert excinfo.value.code == "preview_stale"
    assert not (paths.research_sources_staging / str(run.id)).exists()
    assert not (paths.research_sources / str(run.id)).exists()
    with session_factory() as session:
        assert ResearchRepository(session).list_sources(run.id) == []
        assert ResearchRepository(session).list_findings(run.id) == []


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("queued", "run_not_terminal"),
        ("running", "run_not_terminal"),
        ("completed", "already_completed"),
    ],
)
def test_preview_rejects_ineligible_status(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    status: str,
    code: str,
):
    _request, run, _log_path = _failed_run(session_factory, paths)
    with session_factory() as session:
        row = session.get(model.ResearchRun, run.id)
        assert row is not None
        row.status = status
        session.commit()

    with pytest.raises(ResearchBackfillError) as excinfo:
        ResearchBackfillService(paths, session_factory).preview(run.id)

    assert excinfo.value.code == code


def test_preview_rejects_superseded_active_and_existing_results(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    service = ResearchBackfillService(paths, session_factory)

    _request, run, _log_path = _failed_run(session_factory, paths, max_attempts=2)
    with pytest.raises(ResearchBackfillError) as excinfo:
        service.preview(run.id)
    assert excinfo.value.code == "active_sibling_run"

    _request2, run2, _log_path2 = _failed_run(session_factory, paths)
    with session_factory() as session:
        next_attempt = int(
            session.scalar(
                sa.select(sa.func.max(model.ResearchRun.attempt)).where(
                    model.ResearchRun.generation_request_id == run2.generation_request_id
                )
            )
            or 1
        ) + 1
        session.add(
            model.ResearchRun(
                id=uuid4(),
                generation_request_id=run2.generation_request_id,
                parent_run_id=run2.id,
                attempt=next_attempt,
                status="failed",
            )
        )
        session.commit()
    with pytest.raises(ResearchBackfillError) as excinfo:
        service.preview(run2.id)
    assert excinfo.value.code == "superseded_run"

    _request3, run3, _log_path3 = _failed_run(session_factory, paths)
    with session_factory() as session:
        session.add(
            model.ResearchSource(
                id=uuid4(),
                research_run_id=run3.id,
                url="https://example.com/source",
                title="Source",
                summary="Summary",
                content_hash="b" * 64,
                fetched_at=sa.func.now(),
            )
        )
        session.commit()
    with pytest.raises(ResearchBackfillError) as excinfo:
        service.preview(run3.id)
    assert excinfo.value.code == "already_has_results"


def test_preview_maps_safe_log_and_parse_errors(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    tmp_path: Path,
):
    _request, run, _log_path = _failed_run(session_factory, paths)
    outside = tmp_path / "outside.log"
    outside.write_text(_log(_stdout()), encoding="utf-8")
    with session_factory() as session:
        row = session.get(model.ResearchRun, run.id)
        assert row is not None
        row.hermes_log_path = str(outside)
        session.commit()
    with pytest.raises(ResearchBackfillError) as excinfo:
        ResearchBackfillService(paths, session_factory).preview(run.id)
    assert excinfo.value.code == "unsafe_log_path"

    _request2, run2, log_path2 = _failed_run(session_factory, paths)
    log_path2.write_text("header\n--- stdout ---\nnot json\n--- end stdout ---\n", encoding="utf-8")
    with pytest.raises(ResearchBackfillError) as excinfo:
        ResearchBackfillService(paths, session_factory).preview(run2.id)
    assert excinfo.value.code == "parse_failed"

    _request3, run3, log_path3 = _failed_run(session_factory, paths)
    log_path3.write_text(_log('{"sources":[],"findings":[]}'), encoding="utf-8")
    with pytest.raises(ResearchBackfillError) as excinfo:
        ResearchBackfillService(paths, session_factory).preview(run3.id)
    assert excinfo.value.code == "quality_gate_failed"


def test_preview_rejects_oversized_and_non_utf8_logs(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    _request, run, log_path = _failed_run(session_factory, paths)
    log_path.write_bytes(b"\xff\xfe--- stdout ---")
    with pytest.raises(ResearchBackfillError) as excinfo:
        ResearchBackfillService(paths, session_factory).preview(run.id)
    assert excinfo.value.code == "log_unreadable"

    _request2, run2, log_path2 = _failed_run(session_factory, paths)
    log_path2.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(ResearchBackfillError) as excinfo:
        ResearchBackfillService(paths, session_factory).preview(run2.id)
    assert excinfo.value.code == "log_too_large"


def test_concurrent_apply_persists_once(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    _request, run, _log_path = _failed_run(session_factory, paths)
    digest = ResearchBackfillService(paths, session_factory).preview(run.id).log_sha256

    def apply_once():
        try:
            return ResearchBackfillService(paths, session_factory).apply(run.id, digest).run_status
        except ResearchBackfillError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: apply_once(), range(2)))

    assert outcomes.count("completed") == 1
    assert set(outcomes) <= {"completed", "already_completed", "already_has_results"}
    with session_factory() as session:
        assert len(ResearchRepository(session).list_sources(run.id)) == 1
        assert len(ResearchRepository(session).list_findings(run.id)) == 1


def test_commit_failure_after_promotion_cleans_final_sources(
    session_factory: SessionFactory,
    paths: ProjectPaths,
):
    _request, run, _log_path = _failed_run(session_factory, paths)
    preview = ResearchBackfillService(paths, session_factory).preview(run.id)

    class CommitFailingSession:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def commit(self):
            raise RuntimeError("injected commit failure")

    def failing_factory():
        return CommitFailingSession(session_factory())

    with pytest.raises(RuntimeError, match="injected commit failure"):
        ResearchBackfillService(paths, failing_factory).apply(run.id, preview.log_sha256)

    assert not (paths.research_sources / str(run.id)).exists()
    assert not (paths.research_sources_staging / str(run.id)).exists()
    with session_factory() as session:
        row = session.get(model.ResearchRun, run.id)
        assert row is not None
        assert row.status == "failed"
        assert ResearchRepository(session).list_sources(run.id) == []
        assert ResearchRepository(session).list_findings(run.id) == []


def test_backfill_endpoint_preview_apply_and_error_shape(
    session_factory: SessionFactory,
    paths: ProjectPaths,
    monkeypatch,
):
    _request, run, _log_path = _failed_run(session_factory, paths)
    monkeypatch.setattr(
        "services.research_backfill_service.SessionFactory",
        lambda: session_factory,
    )
    app = create_app(DashboardService(paths))
    client = TestClient(app)

    malformed = client.post(f"/api/research/runs/{run.id}/backfill", json={})
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "invalid_request"

    preview_response = client.post(f"/api/research/runs/{run.id}/backfill", json={"apply": False})
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["would_insert_sources"] == 1
    assert len(preview["log_sha256"]) == 64

    apply_response = client.post(
        f"/api/research/runs/{run.id}/backfill",
        json={"apply": True, "expected_log_sha256": preview["log_sha256"]},
    )
    assert apply_response.status_code == 200
    assert apply_response.json()["run_status"] == "completed"

    second = client.post(
        f"/api/research/runs/{run.id}/backfill",
        json={"apply": True, "expected_log_sha256": preview["log_sha256"]},
    )
    assert second.status_code == 409
    assert second.json() == {
        "code": "already_completed",
        "detail": f"research run {run.id} is already completed",
    }
