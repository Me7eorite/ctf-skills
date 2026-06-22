"""Reconcile attributed shard filesystem state into PostgreSQL build rows."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.jsonio import read_json
from core.paths import ProjectPaths
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models.progress import ProgressEvent
from persistence.repositories import ExecutionsRepository
from persistence.session import SessionFactory, transaction
from services.build_orchestration_service import BuildOrchestrationService

LOG = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL_SECONDS = 5

# 中文注释：lost 触发的 grace window。原值 60s 太短：
#   - 工作子进程 claim shard 后 ~5s 才写 progress event
#   - 频繁 /api/state 同步 tick 会让 reconciler 撞上文件系统切换中间状态
#   - 真实事故：现网 5 条 attempt 在 61-65s 之间被误标 lost（shard 仍在 pending/running）
# 300s 给真实 worker 完整一轮 design 阶段的最长容忍时间，仍能在 hang 时触发 lost 标记。
LOST_GRACE_SECONDS = 300


def _poll_interval_from_env() -> int:
    raw = os.environ.get("BUILD_RECONCILER_POLL_SECONDS")
    if raw is None:
        LOG.warning(
            "BUILD_RECONCILER_POLL_SECONDS is unset; using %s",
            DEFAULT_POLL_INTERVAL_SECONDS,
        )
        return DEFAULT_POLL_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        LOG.warning(
            "invalid BUILD_RECONCILER_POLL_SECONDS=%r; using %s",
            raw,
            DEFAULT_POLL_INTERVAL_SECONDS,
        )
        return DEFAULT_POLL_INTERVAL_SECONDS
    LOG.warning("using BUILD_RECONCILER_POLL_SECONDS=%s", value)
    return value


POLL_INTERVAL_SECONDS = _poll_interval_from_env()


@dataclass(frozen=True)
class _ObservedShard:
    state: str
    path: Path
    payload: dict[str, Any]
    worker: str | None
    claimed_at: datetime | None


class BuildReconciler:
    """Reflect the attributed file queue into build-attempt history."""

    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        session_factory: SessionFactory | None = None,
        poll_interval_seconds: int | None = None,
    ) -> None:
        self.paths = paths or ProjectPaths.discover()
        self.session_factory = session_factory or SessionFactory()
        self.poll_interval_seconds = (
            POLL_INTERVAL_SECONDS
            if poll_interval_seconds is None
            else poll_interval_seconds
        )
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.orchestration = BuildOrchestrationService(
            paths=self.paths,
            session_factory=self.session_factory,
        )
        self._running = True
        self._tick_lock = threading.Lock()

    def tick(self, session: Session) -> None:
        """Reap expired leases, run staging recovery, then reconcile rows."""
        # Lease reaper (add-execution-lease-and-fencing): terminally mark expired
        # current executions lost. No-op until executions exist (cutover flag).
        ExecutionsRepository(session).reap_expired()
        staged_ids = self.orchestration.recover_staging()
        observations = self._scan_attributed_shards()
        self._reconcile(session, staged_ids, observations)

    def _reconcile(
        self,
        session: Session,
        staged_ids: set[UUID],
        observations: dict[UUID, _ObservedShard],
    ) -> None:
        # 中文注释：只锁活跃行，避免每个 tick 锁住全表历史；终态行的 artifact
        # 状态只有 reconciler 自己会改，因此用非锁查询单独刷新即可。
        active_rows = session.scalars(
            sa.select(build_model.BuildAttempt)
            .where(build_model.BuildAttempt.status.in_(("queued", "running")))
            .order_by(build_model.BuildAttempt.created_at)
            .with_for_update()
        ).all()
        terminal_rows = session.scalars(
            sa.select(build_model.BuildAttempt)
            .where(
                build_model.BuildAttempt.status.in_(("succeeded", "failed", "lost")),
                build_model.BuildAttempt.resulting_challenge_dir.is_not(None),
            )
        ).all()
        for row in terminal_rows:
            self._refresh_terminal_artifact(row)
        now = datetime.now(timezone.utc)
        staged_id_texts = {str(staged_id) for staged_id in staged_ids}
        for row in active_rows:
            observed = observations.get(row.id)
            if observed is not None and (
                str(observed.payload.get("design_task_id"))
                != str(row.design_task_id)
                or not self._basename_matches(row.shard_basename, observed)
            ):
                observed = None
            if observed is None:
                if (
                    str(row.id) not in staged_id_texts
                    and _as_utc(row.created_at) <= now - timedelta(seconds=LOST_GRACE_SECONDS)
                    and not self._payload_present_for_row(row)
                    and self._rescan_still_disappeared(row)
                ):
                    self._finish(
                        session,
                        row,
                        status="lost",
                        now=now,
                        error="attributed shard disappeared from all queue states",
                    )
                continue
            claim = self._latest_claim(session, row.shard_basename)
            worker = observed.worker or (claim.worker if claim else None)
            started_at = observed.claimed_at or (claim.created_at if claim else None)

            if observed.state == "running":
                if row.status == "queued":
                    row.status = "running"
                    row.worker = worker
                    row.started_at = started_at or now
                continue
            if observed.state == "pending":
                continue
            if observed.state == "failed":
                artifact_dir, artifact_status, _solve_status = self._artifact(observed)
                self._finish(
                    session,
                    row,
                    status="failed",
                    now=now,
                    worker=worker,
                    started_at=started_at,
                    resulting_challenge_dir=artifact_dir,
                    artifact_status=artifact_status,
                    error="shard execution failed",
                )
                continue
            if observed.state == "done":
                artifact_dir, artifact_status, solve_status = self._artifact(observed)
                if artifact_dir is None:
                    self._finish(
                        session,
                        row,
                        status="failed",
                        now=now,
                        worker=worker,
                        started_at=started_at,
                        artifact_status="missing",
                        error="artifact directory missing",
                    )
                elif solve_status == "passed":
                    self._finish(
                        session,
                        row,
                        status="succeeded",
                        now=now,
                        worker=worker,
                        started_at=started_at,
                        resulting_challenge_dir=artifact_dir,
                        artifact_status=artifact_status,
                    )
                else:
                    self._finish(
                        session,
                        row,
                        status="failed",
                        now=now,
                        worker=worker,
                        started_at=started_at,
                        resulting_challenge_dir=artifact_dir,
                        artifact_status=artifact_status,
                        error=f"artifact solve_status is {solve_status or 'unknown'}",
                    )

        session.flush()

    def tick_once_sync(self) -> None:
        """Open one short transaction and run a single reconciliation tick.

        Serialized by ``_tick_lock`` so the background thread and any HTTP
        handler triggering a sync tick cannot race; concurrent callers would
        otherwise let one tick observe the filesystem mid-publish while the
        other commits a lost row from the stale snapshot.
        """
        with self._tick_lock:
            staged_ids = self.orchestration.recover_staging()
            observations = self._scan_attributed_shards()
            with transaction(factory=self.session_factory) as session:
                self._reconcile(session, staged_ids, observations)

    def run_forever(self) -> None:
        """Keep reconciling with a fresh session per tick until stopped."""
        while self._running:
            try:
                self.tick_once_sync()
            except Exception as exc:
                LOG.warning("build reconciler tick failed: %s", exc)
            if self._running:
                time.sleep(self.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    def _scan_attributed_shards(self) -> dict[UUID, _ObservedShard]:
        observations: dict[UUID, _ObservedShard] = {}
        priority = {"pending": 0, "running": 1, "failed": 2, "done": 3}
        for state in ("pending", "running", "done", "failed"):
            directory = self.paths.shards / state
            for path in sorted(directory.glob("*.json")):
                # Claim sidecars also end in .json and must never be parsed as shards.
                if path.name.endswith(".claim.json"):
                    continue
                payload = read_json(path, None)
                if not isinstance(payload, dict):
                    continue
                try:
                    attempt_id = UUID(str(payload.get("build_attempt_id")))
                except (TypeError, ValueError):
                    continue
                worker, claimed_at = _claim_metadata(path)
                observed = _ObservedShard(
                    state=state,
                    path=path,
                    payload=payload,
                    worker=worker,
                    claimed_at=claimed_at,
                )
                current = observations.get(attempt_id)
                if current is None or priority[state] > priority[current.state]:
                    observations[attempt_id] = observed
        return observations

    def _rescan_still_disappeared(self, row: build_model.BuildAttempt) -> bool:
        """Re-scan the filesystem before committing a lost row.

        Returns True ONLY when the rescan also fails to see the shard, i.e.
        it is genuinely gone. Returns False when the rescan finds a payload
        for this attempt, meaning the first scan's empty result was likely a
        glob-iteration snapshot artifact (worker mid-mv between
        pending/running/failed) and we should not commit lost this tick —
        next tick will pick the shard up cleanly.
        """
        observations = self._scan_attributed_shards()
        observed = observations.get(row.id)
        if observed is None:
            return True  # confirmed gone in both scans
        # Second scan found the shard. Trust it iff payload/design_task align.
        payload_matches = (
            str(observed.payload.get("design_task_id")) == str(row.design_task_id)
            and self._basename_matches(row.shard_basename, observed)
        )
        return not payload_matches

    def _payload_present_for_row(self, row: build_model.BuildAttempt) -> bool:
        staged = self.paths.build_attempt_staging / f"{row.id}.json"
        if staged.is_file() and _payload_matches_row(read_json(staged, None), row):
            return True
        for state in ("pending", "done", "failed"):
            path = self.paths.shards / state / row.shard_basename
            if path.is_file() and _payload_matches_row(read_json(path, None), row):
                return True
        expected = Path(row.shard_basename)
        for path in (self.paths.shards / "running").glob(
            f"{expected.stem}.*{expected.suffix}"
        ):
            if path.name.endswith(".claim.json"):
                continue
            payload = read_json(path, None)
            if not _payload_matches_row(payload, row):
                continue
            observed = _ObservedShard(
                state="running",
                path=path,
                payload=payload,
                worker=None,
                claimed_at=None,
            )
            if self._basename_matches(row.shard_basename, observed):
                return True
        return False

    def _latest_claim(self, session: Session, shard: str) -> ProgressEvent | None:
        return session.scalars(
            sa.select(ProgressEvent)
            .where(
                ProgressEvent.shard == shard,
                ProgressEvent.challenge_id == "",
                ProgressEvent.stage == "queued",
                ProgressEvent.status == "running",
            )
            .order_by(ProgressEvent.id.desc())
            .limit(1)
        ).one_or_none()

    def _basename_matches(
        self,
        expected: str,
        observed: _ObservedShard,
    ) -> bool:
        if observed.state != "running":
            return observed.path.name == expected
        claim = read_json(_claim_path(observed.path), {})
        if isinstance(claim, dict) and claim.get("source_name"):
            return claim["source_name"] == expected
        expected_path = Path(expected)
        return observed.path.name.startswith(f"{expected_path.stem}.") and (
            observed.path.suffix == expected_path.suffix
        )

    def _artifact(
        self,
        observed: _ObservedShard,
    ) -> tuple[str | None, str, str | None]:
        challenges = observed.payload.get("challenges")
        if not isinstance(challenges, list) or not challenges:
            return None, "missing", None
        challenge = challenges[0]
        if not isinstance(challenge, dict):
            return None, "missing", None
        challenge_id = challenge.get("id")
        category = challenge.get("category")
        if not isinstance(challenge_id, str) or not isinstance(category, str):
            return None, "missing", None
        category_dir = self.paths.challenges / category
        candidates = sorted(category_dir.glob(f"{challenge_id}*/metadata.json"))
        for metadata_path in candidates:
            directory_name = metadata_path.parent.name
            if directory_name != challenge_id and not directory_name.startswith(
                f"{challenge_id}-"
            ):
                continue
            metadata = read_json(metadata_path, None)
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("id", challenge_id)) != challenge_id:
                continue
            relative = str(metadata_path.parent.relative_to(self.paths.root)).replace(
                "\\", "/"
            )
            return relative, "present", _optional_string(metadata.get("solve_status"))
        return None, "missing", None

    def _refresh_terminal_artifact(self, row: build_model.BuildAttempt) -> None:
        if not row.resulting_challenge_dir:
            return
        directory = (self.paths.root / row.resulting_challenge_dir).resolve()
        try:
            directory.relative_to(self.paths.root.resolve())
        except ValueError:
            row.artifact_status = "missing"
            return
        row.artifact_status = (
            "present" if (directory / "metadata.json").is_file() else "missing"
        )

    def _finish(
        self,
        session: Session,
        row: build_model.BuildAttempt,
        *,
        status: str,
        now: datetime,
        worker: str | None = None,
        started_at: datetime | None = None,
        resulting_challenge_dir: str | None = None,
        artifact_status: str | None = None,
        error: str | None = None,
    ) -> None:
        row.status = status
        row.worker = worker or row.worker
        row.started_at = row.started_at or started_at or now
        row.finished_at = now
        row.resulting_challenge_dir = resulting_challenge_dir
        if artifact_status is not None:
            row.artifact_status = artifact_status
        row.error = error
        task = session.get(task_model.DesignTask, row.design_task_id)
        if task is not None:
            task.status = "built" if status == "succeeded" else "build_failed"
            task.updated_at = now


def _claim_path(shard: Path) -> Path:
    return shard.with_suffix(shard.suffix + ".claim.json")


def _claim_metadata(shard: Path) -> tuple[str | None, datetime | None]:
    claim = read_json(_claim_path(shard), {})
    if not isinstance(claim, dict):
        return None, None
    worker = _optional_string(claim.get("worker"))
    claimed_at = _parse_datetime(claim.get("claimed_at"))
    return worker, claimed_at


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_matches_row(payload: Any, row: build_model.BuildAttempt) -> bool:
    return bool(
        isinstance(payload, dict)
        and str(payload.get("build_attempt_id")) == str(row.id)
        and str(payload.get("design_task_id")) == str(row.design_task_id)
    )
