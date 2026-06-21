"""PostgreSQL-backed progress store."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.state import (
    ProgressEventInput,
    ProgressStore,
    _normalize_shard,
    _percent,
    _prepare_event,
)
from persistence.models.progress import ProgressEvent, ProgressSnapshot
from persistence.session import (
    SessionFactory,
)
from persistence.session import (
    transaction as session_transaction,
)


class PostgresProgressStore(ProgressStore):
    """ProgressStore implementation whose transaction boundary is each call."""

    def __init__(self, factory: SessionFactory | None = None) -> None:
        self._factory = factory or SessionFactory()

    def record(
        self,
        *,
        shard: str,
        stage: str,
        status: str,
        challenge_id: str = "",
        worker: str = "",
        message: str = "",
    ) -> dict:
        return self.record_batch(
            [
                ProgressEventInput(
                    shard=shard,
                    challenge_id=challenge_id,
                    worker=worker,
                    stage=stage,
                    status=status,
                    message=message,
                )
            ]
        )[0]

    def record_batch(self, events: Sequence[ProgressEventInput]) -> list[dict]:
        prepared = [_prepare_event(event) for event in events]
        with session_transaction(factory=self._factory) as session:
            results: list[dict] = []
            for event in prepared:
                row = ProgressEvent(
                    shard=event.shard,
                    challenge_id=event.challenge_id,
                    worker=event.worker,
                    stage=event.stage,
                    status=event.status,
                    percent=_percent(event.stage, event.status),
                    message=event.message,
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                snapshot = self._upsert_snapshot(session, row)
                results.append(_event_result(row, updated_at=snapshot.updated_at))
            return results

    def events_for_shard(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> list[dict]:
        stmt = (
            sa.select(ProgressEvent)
            .where(ProgressEvent.shard == _normalize_shard(shard))
            .order_by(ProgressEvent.id.asc())
        )
        if before_id is not None:
            stmt = stmt.where(ProgressEvent.id < before_id)
        with session_transaction(factory=self._factory) as session:
            return [_event_dict(row) for row in session.scalars(stmt)]

    def events_for_challenge(
        self,
        shard: str,
        challenge_id: str,
        *,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        if not challenge_id:
            raise ValueError(
                "challenge_id must be non-empty; use events_for_shard or "
                "latest_claim_event for shard-level queries"
            )
        stmt = (
            sa.select(ProgressEvent)
            .where(
                ProgressEvent.shard == _normalize_shard(shard),
                ProgressEvent.challenge_id == challenge_id,
            )
            .order_by(ProgressEvent.id.asc())
        )
        if after_id is not None:
            stmt = stmt.where(ProgressEvent.id >= after_id)
        if before_id is not None:
            stmt = stmt.where(ProgressEvent.id < before_id)
        with session_transaction(factory=self._factory) as session:
            return [_event_dict(row) for row in session.scalars(stmt)]

    def latest_claim_event(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> dict | None:
        stmt = (
            sa.select(ProgressEvent)
            .where(
                ProgressEvent.shard == _normalize_shard(shard),
                ProgressEvent.challenge_id == "",
                ProgressEvent.stage == "queued",
                ProgressEvent.status == "running",
            )
            .order_by(ProgressEvent.id.desc())
            .limit(1)
        )
        if before_id is not None:
            stmt = stmt.where(ProgressEvent.id < before_id)
        with session_transaction(factory=self._factory) as session:
            row = session.scalar(stmt)
            return _event_dict(row) if row else None

    def reset_snapshots(self, shard: str) -> None:
        with session_transaction(factory=self._factory) as session:
            session.execute(
                sa.delete(ProgressSnapshot).where(
                    ProgressSnapshot.shard == _normalize_shard(shard)
                )
            )

    def purge_shards(
        self,
        shards: Collection[str],
        *,
        transaction: object | None = None,
    ) -> None:
        normalized = {_normalize_shard(shard) for shard in shards}
        if not normalized:
            return

        def purge(session: Session) -> None:
            session.execute(
                sa.delete(ProgressEvent).where(ProgressEvent.shard.in_(normalized))
            )
            session.execute(
                sa.delete(ProgressSnapshot).where(
                    ProgressSnapshot.shard.in_(normalized)
                )
            )

        if transaction is not None:
            if not isinstance(transaction, Session):
                raise TypeError("transaction must be a SQLAlchemy Session")
            purge(transaction)
            return
        with session_transaction(factory=self._factory) as session:
            purge(session)

    def dashboard(self, event_limit: int = 60) -> dict:
        with session_transaction(factory=self._factory) as session:
            snapshot_rows = session.scalars(
                sa.select(ProgressSnapshot).order_by(
                    ProgressSnapshot.updated_at.desc(),
                    ProgressSnapshot.shard.asc(),
                    ProgressSnapshot.challenge_id.asc(),
                )
            ).all()
            event_rows = session.scalars(
                sa.select(ProgressEvent)
                .order_by(ProgressEvent.id.desc())
                .limit(event_limit)
            ).all()
        return {
            "snapshots": [_snapshot_dict(row) for row in snapshot_rows],
            "events": [_event_dict(row) for row in event_rows],
            "storage": {
                "backend": "postgresql",
                "path": self._redacted_url(),
                "fallback": False,
                "warning": "",
            },
        }

    def _upsert_snapshot(
        self, session: Session, event: ProgressEvent
    ) -> ProgressSnapshot:
        key = {"shard": event.shard, "challenge_id": event.challenge_id}
        snapshot = session.execute(
            sa.select(ProgressSnapshot)
            .where(
                ProgressSnapshot.shard == key["shard"],
                ProgressSnapshot.challenge_id == key["challenge_id"],
            )
            .with_for_update()
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if snapshot is None:
            snapshot = ProgressSnapshot(
                shard=event.shard,
                challenge_id=event.challenge_id,
                worker=event.worker,
                stage=event.stage,
                status=event.status,
                percent=event.percent,
                message=event.message,
                updated_at=now,
            )
            session.add(snapshot)
        else:
            # Always refresh observation fields; keep (stage, status, percent)
            # of the higher-derived-percent event so the dashboard never shows
            # stage/status from a late-arriving lower-progress event.
            snapshot.worker = event.worker
            snapshot.message = event.message
            snapshot.updated_at = now
            if event.percent >= snapshot.percent:
                snapshot.stage = event.stage
                snapshot.status = event.status
                snapshot.percent = event.percent
        session.flush()
        session.refresh(snapshot)
        return snapshot

    def _redacted_url(self) -> str:
        return self._factory.engine.url.render_as_string(hide_password=True)


def _event_result(event: ProgressEvent, *, updated_at: datetime) -> dict:
    return {
        "event_id": event.id,
        "shard": event.shard,
        "challenge_id": event.challenge_id,
        "worker": event.worker,
        "stage": event.stage,
        "status": event.status,
        "percent": event.percent,
        "message": event.message,
        "updated_at": _format_timestamp(updated_at),
    }


def _event_dict(event: ProgressEvent) -> dict:
    return {
        "id": event.id,
        "shard": event.shard,
        "challenge_id": event.challenge_id,
        "worker": event.worker,
        "stage": event.stage,
        "status": event.status,
        "percent": event.percent,
        "message": event.message,
        "created_at": _format_timestamp(event.created_at),
    }


def _snapshot_dict(snapshot: ProgressSnapshot) -> dict:
    return {
        "shard": snapshot.shard,
        "challenge_id": snapshot.challenge_id,
        "worker": snapshot.worker,
        "stage": snapshot.stage,
        "status": snapshot.status,
        "percent": snapshot.percent,
        "message": snapshot.message,
        "updated_at": _format_timestamp(snapshot.updated_at),
    }


def _format_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)
