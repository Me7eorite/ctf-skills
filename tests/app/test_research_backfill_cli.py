"""CLI contracts for operator research-result backfill."""

from __future__ import annotations

import contextlib
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from uuid import uuid4

import cli
from core.paths import ProjectPaths
from services.research_backfill_service import ResearchBackfillError


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = 0
    with patch.object(sys, "argv", ["challenge-factory", *argv]):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                cli.main()
            except SystemExit as exc:
                code = int(exc.code or 0)
    return code, stdout.getvalue(), stderr.getvalue()


def _preview(run_id):
    return SimpleNamespace(
        run_id=run_id,
        log_sha256="a" * 64,
        would_insert_sources=2,
        would_insert_findings=3,
        current_run_status="failed",
        would_run_status="completed",
        current_request_status="failed",
        would_request_status="researched",
    )


def test_modes_and_targets_are_mutually_exclusive():
    run_id = str(uuid4())
    code, _, stderr = _run(
        ["research", "backfill", "--run-id", run_id, "--all-recoverable", "--dry-run"]
    )
    assert code == 2
    assert "not allowed with argument" in stderr

    code, _, stderr = _run(
        ["research", "backfill", "--all-recoverable", "--dry-run", "--apply"]
    )
    assert code == 2
    assert "not allowed with argument" in stderr


def test_target_and_mode_are_required():
    code, _, stderr = _run(["research", "backfill", "--all-recoverable"])
    assert code == 2
    assert "required" in stderr

    code, _, stderr = _run(["research", "backfill", "--dry-run"])
    assert code == 2
    assert "required" in stderr


def test_single_run_apply_is_rejected():
    with patch("cli._check_category_consistency"):
        code, _, stderr = _run(
            ["research", "backfill", "--run-id", str(uuid4()), "--apply"]
        )
    assert code == 2
    assert "single-run apply requires the dashboard" in stderr


def test_single_run_dry_run_prints_preview_without_apply(tmp_path: Path):
    run_id = uuid4()
    paths = ProjectPaths(tmp_path, tmp_path)
    service = SimpleNamespace(preview=lambda value: _preview(value))
    service.apply = lambda *_args: (_ for _ in ()).throw(AssertionError("apply called"))
    with patch("cli._check_category_consistency"), patch(
        "cli.ProjectPaths.discover", return_value=paths
    ), patch(
        "services.research_backfill_service.ResearchBackfillService", return_value=service
    ):
        code, stdout, stderr = _run(
            ["research", "backfill", "--run-id", str(run_id), "--dry-run"]
        )
    assert code == 0
    assert stderr == ""
    assert stdout.startswith(f"[backfill] {run_id} preview")
    assert "sources=2 findings=3" in stdout


def test_batch_pages_passes_digest_continues_and_exits_one(tmp_path: Path):
    paths = ProjectPaths(tmp_path, tmp_path)
    paths.initialize()
    log_path = paths.research_logs / "fixture.log"
    log_path.write_text("--- stdout ---\n{}\n--- end stdout ---", encoding="utf-8")
    run1 = SimpleNamespace(
        id=uuid4(), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), hermes_log_path=str(log_path)
    )
    run2 = SimpleNamespace(
        id=uuid4(), created_at=datetime(2026, 1, 2, tzinfo=timezone.utc), hermes_log_path=str(log_path)
    )
    repo = SimpleNamespace(list_failed_runs_page=Mock(side_effect=[[run1, run2], []]))
    service = SimpleNamespace()
    service.preview = Mock(side_effect=[_preview(run1.id), _preview(run2.id)])
    service.apply = Mock(
        side_effect=[
            SimpleNamespace(inserted_sources=2, inserted_findings=3, run_status="completed"),
            ResearchBackfillError("preview_stale", "changed"),
        ]
    )

    @contextlib.contextmanager
    def transaction():
        yield "session"

    with patch("cli._check_category_consistency"), patch(
        "cli.ProjectPaths.discover", return_value=paths
    ), patch("persistence.session.transaction", transaction), patch(
        "persistence.repositories.ResearchRepository", return_value=repo
    ), patch(
        "services.research_backfill_service.ResearchBackfillService", return_value=service
    ):
        code, stdout, _ = _run(
            ["research", "backfill", "--all-recoverable", "--apply"]
        )

    assert code == 1
    assert service.apply.call_args_list == [call(run1.id, "a" * 64), call(run2.id, "a" * 64)]
    assert "applied sources=2 findings=3" in stdout
    assert "error=preview_stale" in stdout
    assert "summary succeeded=1 skipped=0 failed=1" in stdout
    assert repo.list_failed_runs_page.call_args_list[1].kwargs["after_id"] == run2.id
