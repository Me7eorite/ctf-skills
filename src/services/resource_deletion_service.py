"""Cross-store deletion for request, design-task, and build-attempt resources."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from core.state import ProgressStore
from persistence import make_postgres_progress_store
from persistence.models import build_attempts as build_model
from persistence.models import challenge_designs as design_model
from persistence.models import design_tasks as task_model
from persistence.models import research as research_model
from persistence.session import SessionFactory

ResourceType = Literal["generation_request", "design_task", "build_attempt"]
LOG = logging.getLogger(__name__)


class ResourceDeletionNotFoundError(LookupError):
    """Raised when the requested root resource does not exist."""


class ResourceDeletionConflictError(RuntimeError):
    """Raised when active execution makes deletion unsafe."""


@dataclass(frozen=True)
class ArtifactOutcome:
    path: str
    reason: str | None = None


@dataclass
class DeletionResult:
    resource_type: ResourceType
    resource_id: UUID
    deleted: list[str] = field(default_factory=list)
    retained: list[ArtifactOutcome] = field(default_factory=list)
    skipped: list[ArtifactOutcome] = field(default_factory=list)
    quarantined: list[ArtifactOutcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "resource_type": self.resource_type,
            "resource_id": str(self.resource_id),
            "deleted": list(self.deleted),
            "retained": [vars(item) for item in self.retained],
            "skipped": [vars(item) for item in self.skipped],
            "quarantined": [vars(item) for item in self.quarantined],
            "warnings": list(self.warnings),
        }


@dataclass
class _Scope:
    root_type: ResourceType
    root_id: UUID
    generation_request_ids: set[UUID] = field(default_factory=set)
    research_run_ids: set[UUID] = field(default_factory=set)
    research_source_ids: set[UUID] = field(default_factory=set)
    design_task_ids: set[UUID] = field(default_factory=set)
    design_attempt_ids: set[UUID] = field(default_factory=set)
    challenge_design_ids: set[UUID] = field(default_factory=set)
    build_attempt_ids: set[UUID] = field(default_factory=set)
    build_attempt_rows: list[build_model.BuildAttempt] = field(default_factory=list)
    shard_basenames: set[str] = field(default_factory=set)
    artifact_paths: set[str] = field(default_factory=set)
    direct_parent_task_id: UUID | None = None


@dataclass
class _QuarantineEntry:
    source: Path
    destination: Path
    state: str = "planned"
    report_deleted: bool = False


class _DeletionQuarantine:
    def __init__(self, paths: ProjectPaths, result: DeletionResult) -> None:
        self.paths = paths
        self.result = result
        self.root = paths.work / "deletion-quarantine" / str(uuid4())
        self.manifest = self.root / "manifest.json"
        self.entries: list[_QuarantineEntry] = []

    def move(self, source: Path, *, report_deleted: bool = False) -> None:
        if not source.exists() and not source.is_symlink():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{len(self.entries):04d}-{source.name}"
        entry = _QuarantineEntry(
            source=source,
            destination=destination,
            report_deleted=report_deleted,
        )
        self.entries.append(entry)
        self._write_manifest()
        source.replace(destination)
        entry.state = "quarantined"
        self._write_manifest()

    def restore(self) -> None:
        for entry in reversed(self.entries):
            if entry.state != "quarantined" or not entry.destination.exists():
                continue
            entry.source.parent.mkdir(parents=True, exist_ok=True)
            entry.destination.replace(entry.source)
            entry.state = "restored"
        self._write_manifest()
        self._cleanup_if_resolved()

    def purge(self) -> None:
        for entry in self.entries:
            if entry.state != "quarantined" or not entry.destination.exists():
                continue
            try:
                _remove_path(entry.destination)
                entry.state = "deleted"
                if entry.report_deleted:
                    self.result.deleted.append(str(entry.source))
            except OSError as exc:
                self.result.quarantined.append(
                    ArtifactOutcome(str(entry.destination), "cleanup-failed")
                )
                self.result.warnings.append(
                    f"failed to remove quarantined path {entry.destination}: {exc}"
                )
        self._write_manifest()
        self._cleanup_if_resolved()

    def _write_manifest(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest.with_suffix(".json.tmp")
        write_json(
            temporary,
            {
                "root_resource": {
                    "type": self.result.resource_type,
                    "id": str(self.result.resource_id),
                },
                "entries": [
                    {
                        "source": str(entry.source),
                        "destination": str(entry.destination),
                        "state": entry.state,
                        "report_deleted": entry.report_deleted,
                    }
                    for entry in self.entries
                ],
            },
        )
        temporary.replace(self.manifest)

    def _cleanup_if_resolved(self) -> None:
        if any(
            entry.destination.exists() or entry.destination.is_symlink()
            for entry in self.entries
            if entry.state == "quarantined"
        ):
            return
        try:
            shutil.rmtree(self.root)
            self.root.parent.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if self.root.exists():
                self.result.warnings.append(
                    f"failed to remove resolved quarantine {self.root}: {exc}"
                )


class ResourceDeletionService:
    """Delete resources while coordinating PostgreSQL, progress, and files."""

    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        session_factory: SessionFactory | None = None,
        progress: ProgressStore | None = None,
    ) -> None:
        self.paths = paths or ProjectPaths.discover()
        self.session_factory = session_factory or SessionFactory()
        self.progress = progress or make_postgres_progress_store(self.session_factory)

    def delete_generation_request(
        self,
        request_id: UUID,
        *,
        delete_artifacts: bool = False,
    ) -> DeletionResult:
        return self._delete(
            request_id,
            "generation_request",
            delete_artifacts=delete_artifacts,
        )

    def delete_design_task(
        self,
        task_id: UUID,
        *,
        delete_artifacts: bool = False,
    ) -> DeletionResult:
        return self._delete(task_id, "design_task", delete_artifacts=delete_artifacts)

    def delete_build_attempt(
        self,
        attempt_id: UUID,
        *,
        delete_artifacts: bool = False,
    ) -> DeletionResult:
        return self._delete(
            attempt_id,
            "build_attempt",
            delete_artifacts=delete_artifacts,
        )

    def recover_quarantine(self) -> list[str]:
        """Restore rolled-back deletions and purge committed quarantines."""
        warnings: list[str] = []
        quarantine_root = self.paths.work / "deletion-quarantine"
        if not quarantine_root.exists():
            return warnings
        for operation_root in sorted(path for path in quarantine_root.iterdir() if path.is_dir()):
            manifest = read_json(operation_root / "manifest.json", None)
            if not isinstance(manifest, dict):
                warnings.append(f"invalid deletion quarantine manifest: {operation_root}")
                continue
            root = manifest.get("root_resource")
            entries = manifest.get("entries")
            if not isinstance(root, dict) or not isinstance(entries, list):
                warnings.append(f"invalid deletion quarantine manifest: {operation_root}")
                continue
            try:
                root_type = root["type"]
                root_id = UUID(str(root["id"]))
            except (KeyError, TypeError, ValueError):
                warnings.append(f"invalid deletion quarantine root: {operation_root}")
                continue
            if root_type not in {"generation_request", "design_task", "build_attempt"}:
                warnings.append(f"invalid deletion quarantine root type: {operation_root}")
                continue
            with self.session_factory() as session:
                root_exists = self._root_exists(session, root_type, root_id)
            ambiguous = False
            for raw_entry in entries:
                if not isinstance(raw_entry, dict):
                    ambiguous = True
                    continue
                try:
                    source = Path(str(raw_entry["source"]))
                    destination = Path(str(raw_entry["destination"]))
                except KeyError:
                    ambiguous = True
                    continue
                if not _is_within(source, self.paths.work) or not _is_within(
                    destination, operation_root
                ):
                    warnings.append(f"unsafe deletion quarantine entry: {operation_root}")
                    ambiguous = True
                    continue
                source_exists = source.exists() or source.is_symlink()
                destination_exists = destination.exists() or destination.is_symlink()
                if not destination_exists:
                    continue
                if root_exists:
                    if source_exists:
                        warnings.append(
                            f"ambiguous deletion quarantine entry retained: {destination}"
                        )
                        ambiguous = True
                        continue
                    try:
                        source.parent.mkdir(parents=True, exist_ok=True)
                        destination.replace(source)
                    except OSError as exc:
                        warnings.append(
                            f"failed to restore deletion quarantine {destination}: {exc}"
                        )
                        ambiguous = True
                else:
                    try:
                        _remove_path(destination)
                    except OSError as exc:
                        warnings.append(
                            f"failed to purge deletion quarantine {destination}: {exc}"
                        )
                        ambiguous = True
            if ambiguous:
                continue
            try:
                shutil.rmtree(operation_root)
            except FileNotFoundError:
                pass
            except OSError as exc:
                warnings.append(
                    f"failed to remove recovered quarantine {operation_root}: {exc}"
                )
        try:
            quarantine_root.rmdir()
        except OSError:
            pass
        return warnings

    def _delete(
        self,
        resource_id: UUID,
        resource_type: ResourceType,
        *,
        delete_artifacts: bool,
    ) -> DeletionResult:
        result = DeletionResult(resource_type=resource_type, resource_id=resource_id)
        result.warnings.extend(self.recover_quarantine())
        quarantine = _DeletionQuarantine(self.paths, result)
        session = self.session_factory()
        try:
            with session.begin():
                if delete_artifacts:
                    self._lock_artifact_reference_tables(session)
                scope = self._scope(session, resource_type, resource_id)
                self._guard_active(session, scope)
                self._quarantine_operational_files(scope, quarantine)
                self._classify_artifacts(session, scope, delete_artifacts, quarantine, result)
                self.progress.purge_shards(scope.shard_basenames, transaction=session)
                self._delete_rows(session, scope)
            quarantine.purge()
            return result
        except Exception:
            try:
                quarantine.restore()
            except OSError:
                # Preserve the original transaction/business exception. The
                # manifest remains available for startup recovery.
                LOG.exception("failed to restore deletion quarantine %s", quarantine.root)
            raise
        finally:
            session.close()

    @staticmethod
    def _lock_artifact_reference_tables(session: Session) -> None:
        # Acquire before row locks to avoid lock-order inversion with writers.
        # SHARE ROW EXCLUSIVE blocks concurrent INSERT/UPDATE/DELETE while the
        # service checks surviving references and quarantines owned paths.
        session.execute(
            sa.text(
                "LOCK TABLE build_attempts, design_attempts, research_runs, "
                "research_sources IN SHARE ROW EXCLUSIVE MODE"
            )
        )

    @staticmethod
    def _root_exists(session: Session, root_type: str, root_id: UUID) -> bool:
        model = {
            "generation_request": research_model.GenerationRequest,
            "design_task": task_model.DesignTask,
            "build_attempt": build_model.BuildAttempt,
        }[root_type]
        return session.scalar(sa.select(model.id).where(model.id == root_id)) is not None

    def _scope(
        self,
        session: Session,
        resource_type: ResourceType,
        resource_id: UUID,
    ) -> _Scope:
        if resource_type == "generation_request":
            return self._request_scope(session, resource_id)
        if resource_type == "design_task":
            return self._task_scope(session, resource_id)
        return self._attempt_scope(session, resource_id)

    def _request_scope(self, session: Session, request_id: UUID) -> _Scope:
        row = session.scalar(
            sa.select(research_model.GenerationRequest)
            .where(research_model.GenerationRequest.id == request_id)
            .with_for_update()
        )
        if row is None:
            raise ResourceDeletionNotFoundError("request not found")
        scope = _Scope("generation_request", request_id)
        scope.generation_request_ids.add(request_id)
        self._collect_research(session, scope)
        self._collect_tasks(session, scope)
        return scope

    def _task_scope(self, session: Session, task_id: UUID) -> _Scope:
        row = session.scalar(
            sa.select(task_model.DesignTask)
            .where(task_model.DesignTask.id == task_id)
            .with_for_update()
        )
        if row is None:
            raise ResourceDeletionNotFoundError("design task not found")
        scope = _Scope("design_task", task_id)
        scope.design_task_ids.add(task_id)
        self._collect_design_and_build(session, scope)
        return scope

    def _attempt_scope(self, session: Session, attempt_id: UUID) -> _Scope:
        # Match BuildOrchestrationService's lock order (task before attempt).
        # Reading the FK first is safe only as a locator; the locked re-read
        # below remains authoritative. Holding the parent lock prevents a new
        # queued sibling from appearing after the active-sibling guard.
        parent_task_id = session.scalar(
            sa.select(build_model.BuildAttempt.design_task_id).where(
                build_model.BuildAttempt.id == attempt_id
            )
        )
        if parent_task_id is None:
            raise ResourceDeletionNotFoundError("build attempt not found")
        parent = session.scalar(
            sa.select(task_model.DesignTask)
            .where(task_model.DesignTask.id == parent_task_id)
            .with_for_update()
        )
        if parent is None:
            raise ResourceDeletionNotFoundError("build attempt not found")
        row = session.scalar(
            sa.select(build_model.BuildAttempt)
            .where(build_model.BuildAttempt.id == attempt_id)
            .with_for_update()
        )
        if row is None:
            raise ResourceDeletionNotFoundError("build attempt not found")
        scope = _Scope("build_attempt", attempt_id)
        scope.build_attempt_ids.add(attempt_id)
        scope.build_attempt_rows.append(row)
        scope.shard_basenames.add(row.shard_basename)
        scope.direct_parent_task_id = parent.id
        if row.resulting_challenge_dir:
            scope.artifact_paths.add(row.resulting_challenge_dir)
        return scope

    def _collect_research(self, session: Session, scope: _Scope) -> None:
        runs = session.scalars(
            sa.select(research_model.ResearchRun)
            .where(
                research_model.ResearchRun.generation_request_id.in_(
                    scope.generation_request_ids
                )
            )
            .with_for_update()
        ).all()
        for run in runs:
            scope.research_run_ids.add(run.id)
            if run.hermes_log_path:
                scope.artifact_paths.add(run.hermes_log_path)
        if scope.research_run_ids:
            sources = session.scalars(
                sa.select(research_model.ResearchSource)
                .where(
                    research_model.ResearchSource.research_run_id.in_(
                        scope.research_run_ids
                    )
                )
                .with_for_update()
            ).all()
            for source in sources:
                scope.research_source_ids.add(source.id)
                if source.raw_text_path:
                    scope.artifact_paths.add(source.raw_text_path)

    def _collect_tasks(self, session: Session, scope: _Scope) -> None:
        tasks = session.scalars(
            sa.select(task_model.DesignTask)
            .where(
                task_model.DesignTask.generation_request_id.in_(
                    scope.generation_request_ids
                )
            )
            .with_for_update()
        ).all()
        scope.design_task_ids.update(task.id for task in tasks)
        self._collect_design_and_build(session, scope)

    def _collect_design_and_build(self, session: Session, scope: _Scope) -> None:
        if not scope.design_task_ids:
            return
        design_attempts = session.scalars(
            sa.select(design_model.DesignAttempt)
            .where(design_model.DesignAttempt.design_task_id.in_(scope.design_task_ids))
            .with_for_update()
        ).all()
        for attempt in design_attempts:
            scope.design_attempt_ids.add(attempt.id)
            if attempt.prompt_path:
                scope.artifact_paths.add(attempt.prompt_path)
            if attempt.hermes_log_path:
                scope.artifact_paths.add(attempt.hermes_log_path)
        designs = session.scalars(
            sa.select(design_model.ChallengeDesign)
            .where(design_model.ChallengeDesign.design_task_id.in_(scope.design_task_ids))
            .with_for_update()
        ).all()
        scope.challenge_design_ids.update(design.id for design in designs)
        builds = session.scalars(
            sa.select(build_model.BuildAttempt)
            .where(build_model.BuildAttempt.design_task_id.in_(scope.design_task_ids))
            .with_for_update()
        ).all()
        for build in builds:
            if build.id not in scope.build_attempt_ids:
                scope.build_attempt_rows.append(build)
            scope.build_attempt_ids.add(build.id)
            scope.shard_basenames.add(build.shard_basename)
            if build.resulting_challenge_dir:
                scope.artifact_paths.add(build.resulting_challenge_dir)

    def _guard_active(self, session: Session, scope: _Scope) -> None:
        if scope.research_run_ids and session.scalar(
            sa.select(sa.func.count())
            .select_from(research_model.ResearchRun)
            .where(
                research_model.ResearchRun.id.in_(scope.research_run_ids),
                research_model.ResearchRun.status == "running",
            )
        ):
            raise ResourceDeletionConflictError("deletion scope contains running research")
        if scope.design_attempt_ids and session.scalar(
            sa.select(sa.func.count())
            .select_from(design_model.DesignAttempt)
            .where(
                design_model.DesignAttempt.id.in_(scope.design_attempt_ids),
                design_model.DesignAttempt.status == "running",
            )
        ):
            raise ResourceDeletionConflictError("deletion scope contains running design")
        running_attempts = [
            attempt for attempt in scope.build_attempt_rows if attempt.status == "running"
        ]
        if running_attempts:
            raise ResourceDeletionConflictError("deletion scope contains running build")
        if scope.root_type == "build_attempt" and scope.direct_parent_task_id is not None:
            siblings = session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(
                    build_model.BuildAttempt.design_task_id == scope.direct_parent_task_id,
                    build_model.BuildAttempt.id.notin_(scope.build_attempt_ids),
                    build_model.BuildAttempt.status.in_(("queued", "running")),
                )
                .with_for_update()
            ).all()
            if siblings:
                raise ResourceDeletionConflictError(
                    "another sibling build attempt is queued or running"
                )

    def _quarantine_operational_files(
        self,
        scope: _Scope,
        quarantine: _DeletionQuarantine,
    ) -> None:
        for shard in sorted(scope.shard_basenames):
            if self._running_matches(shard):
                raise ResourceDeletionConflictError(
                    f"build shard {shard} is currently running"
                )
        for attempt in scope.build_attempt_rows:
            for path in self._operational_paths(attempt):
                quarantine.move(path)
        for run_id in sorted(scope.research_run_ids):
            quarantine.move(self.paths.research_sources / str(run_id), report_deleted=True)
            quarantine.move(self.paths.research_sources_staging / str(run_id), report_deleted=True)
        for shard in sorted(scope.shard_basenames):
            if self._running_matches(shard):
                quarantine.restore()
                raise ResourceDeletionConflictError(
                    f"build shard {shard} was claimed during deletion"
                )

    def _operational_paths(self, attempt: build_model.BuildAttempt) -> list[Path]:
        paths = [
            self.paths.build_attempt_staging / f"{attempt.id}.json",
            self.paths.shards / "pending" / attempt.shard_basename,
            self.paths.shards / "done" / attempt.shard_basename,
            self.paths.shards / "failed" / attempt.shard_basename,
        ]
        paths.extend(path.with_suffix(path.suffix + ".claim.json") for path in list(paths))
        return paths

    def _running_matches(self, shard_basename: str) -> bool:
        running_dir = self.paths.shards / "running"
        if not running_dir.exists():
            return False
        expected = Path(shard_basename)
        for path in running_dir.glob("*.json"):
            if path.name.endswith(".claim.json"):
                continue
            claim = read_json(path.with_suffix(path.suffix + ".claim.json"), {})
            if isinstance(claim, dict) and claim.get("source_name") == shard_basename:
                return True
            if path.name == shard_basename:
                return True
            if path.name.startswith(f"{expected.stem}.") and path.suffix == expected.suffix:
                return True
        return False

    def _classify_artifacts(
        self,
        session: Session,
        scope: _Scope,
        delete_artifacts: bool,
        quarantine: _DeletionQuarantine,
        result: DeletionResult,
    ) -> None:
        surviving = self._surviving_artifact_paths(session, scope)
        processed: set[str] = set()
        for stored in sorted(scope.artifact_paths):
            candidate = self._resolve_artifact_path(stored)
            if candidate is None:
                result.skipped.append(ArtifactOutcome(stored, "unsafe-path"))
                continue
            key = _path_key(candidate)
            if key in processed:
                continue
            processed.add(key)
            if not candidate.exists() and not candidate.is_symlink():
                result.skipped.append(ArtifactOutcome(stored, "missing"))
                continue
            if key in surviving:
                result.skipped.append(ArtifactOutcome(str(candidate), "shared-reference"))
                continue
            if not delete_artifacts:
                result.retained.append(ArtifactOutcome(str(candidate)))
                continue
            quarantine.move(candidate, report_deleted=True)

    def _resolve_artifact_path(self, stored: str) -> Path | None:
        raw = Path(stored)
        candidate = raw if raw.is_absolute() else self.paths.root / raw
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        allowed_roots = [
            self.paths.challenges,
            self.paths.work / "research",
            self.paths.work / "design",
        ]
        for root in allowed_roots:
            try:
                relative = resolved.relative_to(root.resolve())
                # Never allow a persisted path to remove a whole artifact root
                # or a top-level category/type directory.
                if len(relative.parts) < 2:
                    return None
                return candidate
            except ValueError:
                continue
        return None

    def _surviving_artifact_paths(self, session: Session, scope: _Scope) -> set[str]:
        values: set[str] = set()

        def add(stored: str | None) -> None:
            if not stored:
                return
            resolved = self._resolve_artifact_path(stored)
            if resolved is not None:
                values.add(_path_key(resolved))

        for path in session.scalars(
            sa.select(build_model.BuildAttempt.resulting_challenge_dir).where(
                build_model.BuildAttempt.id.notin_(scope.build_attempt_ids or {uuid4()})
            )
        ):
            add(path)
        for path in session.scalars(
            sa.select(research_model.ResearchRun.hermes_log_path).where(
                research_model.ResearchRun.id.notin_(scope.research_run_ids or {uuid4()})
            )
        ):
            add(path)
        for path in session.scalars(
            sa.select(research_model.ResearchSource.raw_text_path).where(
                research_model.ResearchSource.id.notin_(
                    scope.research_source_ids or {uuid4()}
                )
            )
        ):
            add(path)
        for prompt_path, log_path in session.execute(
            sa.select(
                design_model.DesignAttempt.prompt_path,
                design_model.DesignAttempt.hermes_log_path,
            ).where(
                design_model.DesignAttempt.id.notin_(
                    scope.design_attempt_ids or {uuid4()}
                )
            )
        ):
            add(prompt_path)
            add(log_path)
        return values

    def _delete_rows(self, session: Session, scope: _Scope) -> None:
        if scope.challenge_design_ids:
            session.execute(
                sa.delete(design_model.ChallengeDesign).where(
                    design_model.ChallengeDesign.id.in_(scope.challenge_design_ids)
                )
            )
        if scope.root_type == "build_attempt":
            session.execute(
                sa.delete(build_model.BuildAttempt).where(
                    build_model.BuildAttempt.id.in_(scope.build_attempt_ids)
                )
            )
            if scope.direct_parent_task_id is not None:
                self._recompute_task_status(session, scope.direct_parent_task_id)
            return
        if scope.design_attempt_ids:
            session.execute(
                sa.delete(design_model.DesignAttempt).where(
                    design_model.DesignAttempt.id.in_(scope.design_attempt_ids)
                )
            )
        if scope.root_type == "design_task":
            session.execute(
                sa.delete(task_model.DesignTask).where(
                    task_model.DesignTask.id.in_(scope.design_task_ids)
                )
            )
            return
        session.execute(
            sa.delete(research_model.GenerationRequest).where(
                research_model.GenerationRequest.id.in_(scope.generation_request_ids)
            )
        )

    def _recompute_task_status(self, session: Session, task_id: UUID) -> None:
        latest = session.scalar(
            sa.select(build_model.BuildAttempt)
            .where(build_model.BuildAttempt.design_task_id == task_id)
            .order_by(build_model.BuildAttempt.attempt_no.desc())
            .limit(1)
            .with_for_update()
        )
        task = session.get(task_model.DesignTask, task_id)
        if task is None:
            return
        if latest is None:
            task.status = "designed"
        elif latest.status in {"queued", "running"}:
            task.status = "building"
        elif latest.status == "succeeded":
            task.status = "built"
        else:
            task.status = "build_failed"
        task.updated_at = datetime.now(timezone.utc)


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
