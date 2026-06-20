"""Operator-initiated recovery of failed research runs from Hermes logs."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa

from core.paths import ProjectPaths
from domain.research_validators import ResearchValidationError
from persistence.models import research as model
from persistence.session import SessionFactory
from services.research_job_service import (
    ResearchJobService,
    _cleanup_final_sources,
    _cleanup_staged_sources,
    _extract_stdout_block,
    _promote_staged_sources,
)
from services.research_log_utils import SafeResearchLogError, read_safe_research_log
from services.research_output import materialize_research_raw_text, parse_research_output

LOG = logging.getLogger(__name__)

BackfillErrorCode = Literal[
    "run_not_found",
    "already_completed",
    "run_not_terminal",
    "superseded_run",
    "active_sibling_run",
    "already_has_results",
    "preview_stale",
    "invalid_request",
    "no_log_file",
    "unsafe_log_path",
    "log_too_large",
    "log_unreadable",
    "parse_failed",
    "quality_gate_failed",
]

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ResearchBackfillError(RuntimeError):
    """Stable service error surfaced by the HTTP backfill endpoint."""

    def __init__(self, code: BackfillErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class BackfillPreview:
    run_id: UUID
    generation_request_id: UUID
    log_path: str
    log_sha256: str
    would_insert_sources: int
    would_insert_findings: int
    current_run_status: str
    would_run_status: str
    current_request_status: str
    would_request_status: str


@dataclass(frozen=True)
class BackfillResult:
    run_id: UUID
    generation_request_id: UUID
    log_sha256: str
    inserted_sources: int
    inserted_findings: int
    run_status: str
    request_status: str


class ResearchBackfillService:
    def __init__(
        self,
        paths: ProjectPaths,
        repository_factory: SessionFactory | None = None,
    ) -> None:
        self.paths = paths
        self.repository_factory = repository_factory
        self.job_service = ResearchJobService(repository_factory)

    def preview(self, run_id: UUID) -> BackfillPreview:
        session = (self.repository_factory or SessionFactory())()
        try:
            run, request = self._load_and_check_eligible(session, run_id, lock=False)
            safe_log = self._read_log(run)
            parsed = self._parse_safe_log(safe_log.text, request.target_count)
            return BackfillPreview(
                run_id=run.id,
                generation_request_id=run.generation_request_id,
                log_path=str(safe_log.path),
                log_sha256=_sha256_hex(safe_log.data),
                would_insert_sources=len(parsed.sources),
                would_insert_findings=len(parsed.findings),
                current_run_status=run.status,
                would_run_status="completed",
                current_request_status=request.status,
                would_request_status="researched",
            )
        finally:
            session.close()

    def apply(self, run_id: UUID, expected_log_sha256: str) -> BackfillResult:
        if not _DIGEST_RE.match(expected_log_sha256):
            raise ResearchBackfillError("invalid_request", "expected_log_sha256 must be 64 lowercase hex characters")

        session = (self.repository_factory or SessionFactory())()
        promoted = False
        try:
            try:
                session.connection()
                self._lock_run(session, run_id)
                run, request = self._load_and_check_eligible(session, run_id, lock=True)
                safe_log = self._read_log(run)
                log_sha256 = _sha256_hex(safe_log.data)
                if log_sha256 != expected_log_sha256:
                    raise ResearchBackfillError("preview_stale", "Hermes log changed after preview")

                parsed = self._parse_safe_log(safe_log.text, request.target_count)
                source_payloads, finding_payloads = materialize_research_raw_text(
                    parsed,
                    paths=self.paths,
                    run_id=run.id,
                )
                self.job_service._persist_rescue_payload(
                    session,
                    run,
                    source_payloads,
                    finding_payloads,
                    str(safe_log.path),
                )
                session.flush()
                _promote_staged_sources(run.id, self.paths)
                promoted = True
                session.commit()
                LOG.info(
                    "research backfill applied",
                    extra={
                        "run_id": str(run.id),
                        "sources": len(source_payloads),
                        "findings": len(finding_payloads),
                    },
                )
                return BackfillResult(
                    run_id=run.id,
                    generation_request_id=run.generation_request_id,
                    log_sha256=log_sha256,
                    inserted_sources=len(source_payloads),
                    inserted_findings=len(finding_payloads),
                    run_status=run.status,
                    request_status="researched",
                )
            except BaseException as exc:
                session.rollback()
                if promoted:
                    _cleanup_final_sources(run_id, self.paths)
                else:
                    _cleanup_staged_sources(run_id, self.paths)
                if isinstance(exc, ResearchBackfillError):
                    LOG.info(
                        "research backfill rejected",
                        extra={"run_id": str(run_id), "code": exc.code},
                    )
                else:
                    LOG.warning("research backfill failed for run %s: %s", run_id, exc)
                raise
        finally:
            session.close()

    def _load_and_check_eligible(
        self,
        session,
        run_id: UUID,
        *,
        lock: bool,
    ) -> tuple[model.ResearchRun, model.GenerationRequest]:
        stmt = sa.select(model.ResearchRun).where(model.ResearchRun.id == run_id)
        if lock:
            stmt = stmt.with_for_update()
        run = session.scalar(stmt)
        if run is None:
            raise ResearchBackfillError("run_not_found", f"research run {run_id} was not found")
        if run.status == "completed":
            raise ResearchBackfillError("already_completed", f"research run {run_id} is already completed")
        if run.status != "failed":
            raise ResearchBackfillError("run_not_terminal", f"research run {run_id} is not a failed terminal run")

        request = session.get(model.GenerationRequest, run.generation_request_id)
        if request is None:
            raise ResearchBackfillError("run_not_found", f"generation request for run {run_id} was not found")

        siblings = session.scalars(
            sa.select(model.ResearchRun)
            .where(model.ResearchRun.generation_request_id == run.generation_request_id)
            .order_by(model.ResearchRun.attempt)
        ).all()
        for sibling in siblings:
            if sibling.id == run.id:
                continue
            if sibling.status in {"queued", "running"}:
                raise ResearchBackfillError("active_sibling_run", "another retry is queued or running")
        for sibling in siblings:
            if sibling.id == run.id:
                continue
            if sibling.status == "completed" or sibling.attempt > run.attempt:
                raise ResearchBackfillError("superseded_run", "failed run has a newer or completed sibling")

        source_count, finding_count = self._result_counts(session, run.id)
        if source_count or finding_count:
            raise ResearchBackfillError("already_has_results", "research run already has persisted results")
        return run, request

    def _result_counts(self, session, run_id: UUID) -> tuple[int, int]:
        source_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(model.ResearchSource)
            .where(model.ResearchSource.research_run_id == run_id)
        )
        finding_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(model.ResearchFinding)
            .where(model.ResearchFinding.research_run_id == run_id)
        )
        return int(source_count or 0), int(finding_count or 0)

    def _read_log(self, run: model.ResearchRun):
        try:
            return read_safe_research_log(self.paths, run.hermes_log_path)
        except SafeResearchLogError as exc:
            raise ResearchBackfillError(exc.code, exc.detail) from exc

    def _parse_safe_log(self, log_text: str, target_count: int):
        stdout = _extract_stdout_block(log_text)
        if not stdout:
            raise ResearchBackfillError("parse_failed", "Hermes log does not contain a complete stdout block")
        try:
            return parse_research_output(stdout, target_count=target_count)
        except ResearchValidationError as exc:
            detail = str(exc)
            raise ResearchBackfillError(_parse_error_code(detail), detail) from exc

    def _lock_run(self, session, run_id: UUID) -> None:
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            return
        key = int.from_bytes(run_id.bytes[:8], "big", signed=True)
        session.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_error_code(detail: str) -> BackfillErrorCode:
    if detail.startswith(
        (
            "insufficient_findings:",
            "url_shape_invalid:",
            "content_hash_shape_invalid:",
            "content_hash_dup:",
            "unparseable_output:sources_not_list",
            "unparseable_output:findings_not_list",
            "unparseable_output:source_not_object",
        )
    ):
        return "quality_gate_failed"
    return "parse_failed"


def preview_dict(preview: BackfillPreview) -> dict[str, Any]:
    return _dataclass_dict(preview)


def result_dict(result: BackfillResult) -> dict[str, Any]:
    return _dataclass_dict(result)


def _dataclass_dict(value: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, item in value.__dict__.items():
        payload[key] = str(item) if isinstance(item, (UUID, Path)) else item
    return payload
