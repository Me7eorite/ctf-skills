"""PostgreSQL repository tests for `PostgresProgressStore`.

Runs against ``TEST_DATABASE_URL``; skipped otherwise so the default
``pytest`` run stays green on machines without database access.

Covers:
- insert + snapshot upsert
- the no-regression rule on a real database
- `record_batch` atomic rollback
- fail-loud behavior when the engine raises `OperationalError`
- UTC `YYYY-MM-DDTHH:MM:SSZ` timestamp serialization
- `events_for_*` ordering and id-window semantics
- `latest_claim_event` boundary behavior
- `reset_snapshots` preserves event history
- `dashboard()` storage path password masking (7.2)
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from core.state import ProgressEventInput
from persistence.errors import PersistenceConnectionError
from persistence.repositories.progress import PostgresProgressStore
from persistence.session import SessionFactory

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _reset_schema(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture
def store(pg_url) -> PostgresProgressStore:
    _reset_schema(pg_url)
    env = os.environ.copy()
    env["DATABASE_URL"] = pg_url
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    factory = SessionFactory(create_engine(pg_url))
    try:
        yield PostgresProgressStore(factory)
    finally:
        factory.engine.dispose()
        _reset_schema(pg_url)


def _records(store: PostgresProgressStore) -> list[dict]:
    return store.dashboard(event_limit=100)["events"]


def _snapshot(store: PostgresProgressStore, shard: str, challenge_id: str) -> dict | None:
    for row in store.dashboard()["snapshots"]:
        if row["shard"] == shard and row["challenge_id"] == challenge_id:
            return row
    return None


def test_record_inserts_event_and_creates_snapshot(store):
    result = store.record(
        shard="s.json",
        stage="build",
        status="running",
        challenge_id="c-1",
        worker="w-1",
        message="compiling",
    )

    assert result["event_id"] >= 1
    assert result["shard"] == "s.json"
    assert result["challenge_id"] == "c-1"
    assert result["stage"] == "build"
    assert result["status"] == "running"
    assert isinstance(result["percent"], int)
    assert TS_PATTERN.match(result["updated_at"])

    snapshot = _snapshot(store, "s.json", "c-1")
    assert snapshot is not None
    assert snapshot["stage"] == "build"
    assert snapshot["status"] == "running"
    assert snapshot["percent"] == result["percent"]


def test_snapshot_keeps_higher_progress_on_lower_event(store):
    store.record(shard="s.json", stage="document", status="passed", challenge_id="c-1")
    before = _snapshot(store, "s.json", "c-1")
    assert before is not None
    high_percent = before["percent"]

    store.record(shard="s.json", stage="validate", status="running", challenge_id="c-1")

    after = _snapshot(store, "s.json", "c-1")
    assert after is not None
    # New semantics: snapshot retains the higher-progress (stage, status).
    assert after["stage"] == "document"
    assert after["status"] == "passed"
    assert after["percent"] == high_percent
    # And the lower-progress event is still appended.
    events = _records(store)
    assert any(e["stage"] == "validate" and e["status"] == "running" for e in events)


def test_snapshot_advances_on_equal_or_higher_progress(store):
    store.record(shard="s.json", stage="build", status="running", challenge_id="c-1")
    store.record(shard="s.json", stage="validate", status="running", challenge_id="c-1")

    snapshot = _snapshot(store, "s.json", "c-1")
    assert snapshot is not None
    assert snapshot["stage"] == "validate"
    assert snapshot["status"] == "running"


def test_record_batch_rollback_on_invalid_event(store):
    valid = ProgressEventInput(
        shard="s.json", stage="build", status="running", challenge_id="c-1"
    )
    bad = ProgressEventInput(
        shard="s.json", stage="cleanup", status="running", challenge_id="c-1"
    )

    with pytest.raises(ValueError):
        store.record_batch([valid, bad])

    events = _records(store)
    assert events == [], "no events should be persisted when the batch failed"


def test_events_for_shard_orders_and_windows(store):
    a = store.record(shard="s.json", stage="design", status="running", challenge_id="c-1")
    b = store.record(shard="s.json", stage="design", status="passed", challenge_id="c-1")
    c = store.record(shard="s.json", stage="build", status="running", challenge_id="c-2")

    rows = store.events_for_shard("s.json")
    assert [r["id"] for r in rows] == [a["event_id"], b["event_id"], c["event_id"]]

    rows_bounded = store.events_for_shard("s.json", before_id=c["event_id"])
    assert [r["id"] for r in rows_bounded] == [a["event_id"], b["event_id"]]


def test_events_for_challenge_excludes_other_challenges(store):
    store.record(shard="s.json", stage="design", status="running", challenge_id="c-1")
    store.record(shard="s.json", stage="design", status="running", challenge_id="c-2")
    store.record(shard="s.json", stage="design", status="passed", challenge_id="c-1")

    rows = store.events_for_challenge("s.json", "c-1")
    assert {r["challenge_id"] for r in rows} == {"c-1"}
    assert len(rows) == 2


def test_events_for_challenge_rejects_empty_id(store):
    with pytest.raises(ValueError):
        store.events_for_challenge("s.json", "")


def test_latest_claim_event_returns_most_recent(store):
    store.record(shard="s.json", stage="queued", status="running", worker="w-1")
    later = store.record(shard="s.json", stage="queued", status="running", worker="w-2")

    claim = store.latest_claim_event("s.json")
    assert claim is not None
    assert claim["id"] == later["event_id"]
    assert claim["worker"] == "w-2"

    bounded = store.latest_claim_event("s.json", before_id=later["event_id"])
    assert bounded is not None
    assert bounded["id"] != later["event_id"]


def test_reset_snapshots_preserves_events(store):
    store.record(shard="s.json", stage="build", status="passed", challenge_id="c-1")

    store.reset_snapshots("s.json")

    snapshots = store.dashboard()["snapshots"]
    assert snapshots == []
    events = _records(store)
    assert len(events) == 1


def test_purge_shards_standalone_removes_only_target_progress(store):
    store.record(shard="delete.json", stage="build", status="running")
    store.record(shard="keep.json", stage="build", status="running")

    store.purge_shards(["delete.json"])

    assert store.events_for_shard("delete.json") == []
    assert len(store.events_for_shard("keep.json")) == 1
    assert _snapshot(store, "delete.json", "") is None
    assert _snapshot(store, "keep.json", "") is not None


def test_purge_shards_joins_and_rolls_back_with_caller_transaction(store):
    store.record(shard="rollback.json", stage="build", status="running")
    session = store._factory()
    try:
        with pytest.raises(RuntimeError, match="force rollback"):
            with session.begin():
                store.purge_shards(["rollback.json"], transaction=session)
                raise RuntimeError("force rollback")
    finally:
        session.close()

    assert len(store.events_for_shard("rollback.json")) == 1
    assert _snapshot(store, "rollback.json", "") is not None


def test_dashboard_storage_masks_password(store):
    payload = store.dashboard()
    storage = payload["storage"]
    assert storage["fallback"] is False
    assert storage["warning"] == ""
    # SQLAlchemy renders masked passwords as `***`. Ensure the original
    # plaintext password never leaks into the redacted URL.
    assert "postgres:postgres@" not in storage["path"]
    assert "***" in storage["path"]


def test_dashboard_event_timestamps_use_utc_z_format(store):
    store.record(shard="s.json", stage="build", status="running")
    events = _records(store)
    assert events, "expected at least one event"
    for event in events:
        assert TS_PATTERN.match(event["created_at"]), event["created_at"]
    snapshot = _snapshot(store, "s.json", "")
    assert snapshot is not None
    assert TS_PATTERN.match(snapshot["updated_at"])


# ---------------------------------------------------------------------------
# Unit-level checks that do NOT need a live PG (still under the
# `postgres` mark only to keep all this file together).
# ---------------------------------------------------------------------------


def test_record_fail_loud_when_engine_unreachable():
    """When the underlying engine raises OperationalError, the store wraps it."""

    factory = SessionFactory(
        create_engine("postgresql+psycopg://nobody:nobody@127.0.0.1:1/none")
    )
    store = PostgresProgressStore(factory)
    try:
        with pytest.raises(PersistenceConnectionError):
            store.record(shard="s.json", stage="build", status="running")
    finally:
        factory.engine.dispose()


def test_redacted_url_passes_through_when_no_password():
    """URLs without a password must not gain a synthetic `:***` segment."""

    factory = SessionFactory(
        create_engine("postgresql+psycopg://nobody@127.0.0.1:5432/none")
    )
    store = PostgresProgressStore(factory)
    try:
        url = store._redacted_url()
        assert ":***" not in url
        assert "nobody@127.0.0.1" in url
    finally:
        factory.engine.dispose()
