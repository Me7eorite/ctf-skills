"""Unit tests for the build-attempt domain values and DTO."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from domain.build_attempts import BuildAttempt, BuildAttemptStatus


def test_build_attempt_status_values_match_persistence_contract():
    assert BuildAttemptStatus == (
        "queued",
        "running",
        "succeeded",
        "failed",
        "lost",
    )


def test_build_attempt_is_frozen():
    attempt = BuildAttempt(
        id=uuid4(),
        design_task_id=uuid4(),
        attempt_no=1,
        status="queued",
        shard_basename="build-attempt.json",
        worker=None,
        resulting_challenge_dir=None,
        artifact_status="unknown",
        error=None,
        created_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
    )

    with pytest.raises(FrozenInstanceError):
        attempt.status = "running"  # type: ignore[misc]
