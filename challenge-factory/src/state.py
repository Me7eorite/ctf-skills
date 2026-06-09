"""SQLite-backed progress events and latest snapshots."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir

from paths import ProjectPaths

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


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class StateStore:
    """Persists append-only progress events and one snapshot per work item."""

    def __init__(self, paths: ProjectPaths):
        self.preferred_path = paths.state_database
        self.path = self.preferred_path
        self.warning = ""
        try:
            self._initialize()
        except (OSError, sqlite3.OperationalError) as exc:
            root_key = sha256(
                str(paths.root.resolve()).encode("utf-8")
            ).hexdigest()[:12]
            self.path = (
                Path(gettempdir())
                / "challenge-factory"
                / root_key
                / "state.sqlite3"
            )
            self.warning = (
                f"Cannot use {self.preferred_path}: {exc}. "
                f"Using fallback database {self.path}."
            )
            self._initialize()

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
        if stage not in STAGES:
            raise ValueError(f"invalid progress stage: {stage}")
        if status not in STATUSES:
            raise ValueError(f"invalid progress status: {status}")

        timestamp = utc_now()
        percent = self._percent(stage, status)
        values = (
            Path(shard).name,
            challenge_id,
            worker,
            stage,
            status,
            percent,
            message,
            timestamp,
        )
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO progress_events (
                        shard, challenge_id, worker, stage, status,
                        percent, message, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                connection.execute(
                    """
                    INSERT INTO progress_snapshots (
                        shard, challenge_id, worker, stage, status,
                        percent, message, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(shard, challenge_id) DO UPDATE SET
                        worker = excluded.worker,
                        stage = excluded.stage,
                        status = excluded.status,
                        percent = excluded.percent,
                        message = excluded.message,
                        updated_at = excluded.updated_at
                    """,
                    values,
                )
        return {
            "shard": values[0],
            "challenge_id": challenge_id,
            "worker": worker,
            "stage": stage,
            "status": status,
            "percent": percent,
            "message": message,
            "updated_at": timestamp,
        }

    def dashboard(self, event_limit: int = 60) -> dict:
        with closing(self._connect()) as connection:
            snapshots = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT shard, challenge_id, worker, stage, status,
                           percent, message, updated_at
                    FROM progress_snapshots
                    ORDER BY updated_at DESC, shard, challenge_id
                    """
                )
            ]
            events = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT id, shard, challenge_id, worker, stage, status,
                           percent, message, created_at
                    FROM progress_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (event_limit,),
                )
            ]
        return {
            "snapshots": snapshots,
            "events": events,
            "storage": {
                "path": str(self.path),
                "fallback": self.path != self.preferred_path,
                "warning": self.warning,
            },
        }

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS progress_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        shard TEXT NOT NULL,
                        challenge_id TEXT NOT NULL DEFAULT '',
                        worker TEXT NOT NULL DEFAULT '',
                        stage TEXT NOT NULL,
                        status TEXT NOT NULL,
                        percent INTEGER NOT NULL,
                        message TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS progress_snapshots (
                        shard TEXT NOT NULL,
                        challenge_id TEXT NOT NULL DEFAULT '',
                        worker TEXT NOT NULL DEFAULT '',
                        stage TEXT NOT NULL,
                        status TEXT NOT NULL,
                        percent INTEGER NOT NULL,
                        message TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (shard, challenge_id)
                    );

                    CREATE INDEX IF NOT EXISTS progress_events_created
                        ON progress_events(created_at DESC);
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @staticmethod
    def _percent(stage: str, status: str) -> int:
        index = STAGES.index(stage)
        if status == "pending":
            return max(0, index * 16 - 8)
        if status == "running":
            return min(95, index * 16 + 5)
        if status == "failed":
            return min(99, index * 16 + 8)
        return 100 if stage == "complete" else min(96, (index + 1) * 16)
