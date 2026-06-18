"""Progress event contracts and in-memory implementation."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

STAGES = (
    "queued",
    "design",
    "implement",
    "build",
    "validate",
    "document",
    "complete",
)
STATUSES = {"pending", "running", "passed", "failed"}


@dataclass(frozen=True)
class ProgressEventInput:
    shard: str
    stage: str
    status: str
    challenge_id: str = ""
    worker: str = ""
    message: str = ""


class ProgressStore(Protocol):
    def record(
        self,
        *,
        shard: str,
        stage: str,
        status: str,
        challenge_id: str = "",
        worker: str = "",
        message: str = "",
    ) -> dict: ...

    def record_batch(self, events: Sequence[ProgressEventInput]) -> list[dict]: ...

    def events_for_shard(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> list[dict]: ...

    def events_for_challenge(
        self,
        shard: str,
        challenge_id: str,
        *,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[dict]: ...

    def latest_claim_event(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> dict | None: ...

    def reset_snapshots(self, shard: str) -> None: ...

    def dashboard(self, event_limit: int = 60) -> dict: ...


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class InMemoryProgressStore:
    """Append-only progress store for tests and explicit in-process use."""

    def __init__(self) -> None:
        self._next_id = 1
        self._events: list[dict] = []
        self._snapshots: dict[tuple[str, str], dict] = {}

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
                    stage=stage,
                    status=status,
                    challenge_id=challenge_id,
                    worker=worker,
                    message=message,
                )
            ]
        )[0]

    def record_batch(self, events: Sequence[ProgressEventInput]) -> list[dict]:
        prepared = [_prepare_event(event) for event in events]
        timestamp = utc_now()
        results: list[dict] = []
        for event in prepared:
            event_id = self._next_id
            self._next_id += 1
            stored = {
                "id": event_id,
                "shard": event.shard,
                "challenge_id": event.challenge_id,
                "worker": event.worker,
                "stage": event.stage,
                "status": event.status,
                "percent": _percent(event.stage, event.status),
                "message": event.message,
                "created_at": timestamp,
            }
            self._events.append(stored)
            self._upsert_snapshot(stored, timestamp)
            results.append(_event_result(stored, updated_at=timestamp))
        return results

    def events_for_shard(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> list[dict]:
        normalized = _normalize_shard(shard)
        rows = [event for event in self._events if event["shard"] == normalized]
        if before_id is not None:
            rows = [event for event in rows if int(event["id"]) < before_id]
        return [dict(event) for event in rows]

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
        normalized = _normalize_shard(shard)
        rows = [
            event
            for event in self._events
            if event["shard"] == normalized and event["challenge_id"] == challenge_id
        ]
        if after_id is not None:
            rows = [event for event in rows if int(event["id"]) >= after_id]
        if before_id is not None:
            rows = [event for event in rows if int(event["id"]) < before_id]
        return [dict(event) for event in rows]

    def latest_claim_event(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> dict | None:
        normalized = _normalize_shard(shard)
        rows = [
            event
            for event in self._events
            if event["shard"] == normalized
            and event["challenge_id"] == ""
            and event["stage"] == "queued"
            and event["status"] == "running"
        ]
        if before_id is not None:
            rows = [event for event in rows if int(event["id"]) < before_id]
        if not rows:
            return None
        return dict(rows[-1])

    def reset_snapshots(self, shard: str) -> None:
        normalized = _normalize_shard(shard)
        for key in list(self._snapshots):
            if key[0] == normalized:
                del self._snapshots[key]

    def dashboard(self, event_limit: int = 60) -> dict:
        snapshots = sorted(
            (dict(snapshot) for snapshot in self._snapshots.values()),
            key=lambda row: (row["updated_at"], row["shard"], row["challenge_id"]),
            reverse=True,
        )
        events = [dict(event) for event in reversed(self._events[-event_limit:])]
        return {
            "snapshots": snapshots,
            "events": events,
            "storage": {
                "backend": "memory",
                "path": "memory://",
                "fallback": False,
                "warning": "",
            },
        }

    def _upsert_snapshot(self, event: dict, timestamp: str) -> None:
        key = (event["shard"], event["challenge_id"])
        current = self._snapshots.get(key)
        if current is None:
            self._snapshots[key] = _snapshot_from_event(event, timestamp)
            return
        current["worker"] = event["worker"]
        current["message"] = event["message"]
        current["stage"] = event["stage"]
        current["status"] = event["status"]
        current["updated_at"] = timestamp
        if int(event["percent"]) >= int(current["percent"]):
            current["percent"] = event["percent"]


def _prepare_event(event: ProgressEventInput) -> ProgressEventInput:
    if event.stage not in STAGES:
        raise ValueError(f"invalid progress stage: {event.stage}")
    if event.status not in STATUSES:
        raise ValueError(f"invalid progress status: {event.status}")
    return ProgressEventInput(
        shard=_normalize_shard(event.shard),
        challenge_id=event.challenge_id,
        worker=event.worker,
        stage=event.stage,
        status=event.status,
        message=event.message,
    )


def _normalize_shard(shard: str) -> str:
    return Path(shard).name


def _percent(stage: str, status: str) -> int:
    index = STAGES.index(stage)
    if status == "pending":
        return max(0, index * 16 - 8)
    if status == "running":
        return min(95, index * 16 + 5)
    if status == "failed":
        return min(99, index * 16 + 8)
    return 100 if stage == "complete" else min(96, (index + 1) * 16)


def _event_result(event: dict, *, updated_at: str) -> dict:
    return {
        "event_id": event["id"],
        "shard": event["shard"],
        "challenge_id": event["challenge_id"],
        "worker": event["worker"],
        "stage": event["stage"],
        "status": event["status"],
        "percent": event["percent"],
        "message": event["message"],
        "updated_at": updated_at,
    }


def _snapshot_from_event(event: dict, timestamp: str) -> dict:
    return {
        "shard": event["shard"],
        "challenge_id": event["challenge_id"],
        "worker": event["worker"],
        "stage": event["stage"],
        "status": event["status"],
        "percent": event["percent"],
        "message": event["message"],
        "updated_at": timestamp,
    }
