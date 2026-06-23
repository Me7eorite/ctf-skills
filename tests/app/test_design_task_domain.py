"""Unit tests for domain.design_task_validators (no DB)."""

from __future__ import annotations

import pytest

from domain.design_task_validators import (
    DesignTaskValidationError,
    validate_candidate,
    validate_candidate_set,
    validate_status_transition,
)
from domain.design_tasks import DesignTaskStatus


def _web_candidate(**overrides):
    candidate = {
        "task_no": 1,
        "challenge_id": "web-0001",
        "title": "SQL injection drill 1",
        "category": "web",
        "difficulty": "easy",
        "primary_technique": "boolean-based blind sqli",
        "learning_objective": "extract data via boolean inference",
        "points": 100,
        "port": 8080,
        "finding_ids": [],
    }
    candidate.update(overrides)
    return candidate


def test_validate_candidate_accepts_web_seed_shape():
    validate_candidate(_web_candidate(), parent_category="web", task_no=1)


def test_validate_candidate_rejects_empty_title():
    with pytest.raises(DesignTaskValidationError, match="title"):
        validate_candidate(_web_candidate(title=""), parent_category="web", task_no=1)


def test_validate_candidate_rejects_cross_category():
    with pytest.raises(DesignTaskValidationError, match="category"):
        validate_candidate(
            _web_candidate(category="pwn", challenge_id="pwn-0001"),
            parent_category="web",
            task_no=1,
        )


def test_validate_candidate_rejects_mismatched_challenge_id_prefix():
    with pytest.raises(DesignTaskValidationError, match="prefix"):
        validate_candidate(
            _web_candidate(challenge_id="re-0001"),
            parent_category="web",
            task_no=1,
        )


def test_validate_candidate_rejects_unknown_difficulty():
    with pytest.raises(DesignTaskValidationError, match="difficulty"):
        validate_candidate(
            _web_candidate(difficulty="trivial"),
            parent_category="web",
            task_no=1,
        )


def test_validate_candidate_rejects_zero_points():
    with pytest.raises(DesignTaskValidationError, match="points"):
        validate_candidate(
            _web_candidate(points=0),
            parent_category="web",
            task_no=1,
        )


def test_validate_candidate_requires_port_for_web():
    with pytest.raises(DesignTaskValidationError, match="port"):
        validate_candidate(
            _web_candidate(port=None),
            parent_category="web",
            task_no=1,
        )


def test_validate_candidate_allows_null_port_for_re():
    candidate = _web_candidate(
        category="re",
        challenge_id="re-0001",
        port=None,
    )
    validate_candidate(candidate, parent_category="re", task_no=1)


def test_validate_candidate_rejects_wrong_task_no():
    with pytest.raises(DesignTaskValidationError, match="task_no"):
        validate_candidate(_web_candidate(task_no=2), parent_category="web", task_no=1)


def test_validate_candidate_set_rejects_wrong_count():
    candidates = [_web_candidate()]
    with pytest.raises(DesignTaskValidationError, match="target_count"):
        validate_candidate_set(
            candidates,
            target_count=3,
            difficulty_distribution={"easy": 1, "medium": 2},
        )


def test_validate_candidate_set_rejects_wrong_distribution():
    candidates = [
        _web_candidate(task_no=1, challenge_id="web-0001", difficulty="easy"),
        _web_candidate(task_no=2, challenge_id="web-0002", difficulty="easy"),
    ]
    with pytest.raises(DesignTaskValidationError, match="difficulty mix"):
        validate_candidate_set(
            candidates,
            target_count=2,
            difficulty_distribution={"easy": 1, "medium": 1},
        )


def test_validate_candidate_set_rejects_non_consecutive_task_no():
    candidates = [
        _web_candidate(task_no=1, challenge_id="web-0001"),
        _web_candidate(task_no=3, challenge_id="web-0003"),
    ]
    with pytest.raises(DesignTaskValidationError, match="sequence"):
        validate_candidate_set(
            candidates,
            target_count=2,
            difficulty_distribution={"easy": 2},
        )


@pytest.mark.parametrize(
    "current,target",
    [("draft", "archived"), ("queued", "archived")],
)
def test_validate_status_transition_allows_planning_paths(current, target):
    validate_status_transition(current, target)


def test_validate_status_transition_allows_reviewed_or_exempt_queue():
    validate_status_transition("draft", "queued", plan_reviewed_at=object())
    validate_status_transition("draft", "queued", review_exempt=True)


def test_validate_status_transition_rejects_unreviewed_queue():
    with pytest.raises(DesignTaskValidationError, match="plan_not_reviewed"):
        validate_status_transition("draft", "queued")


@pytest.mark.parametrize(
    "current,target",
    [
        ("queued", "draft"),
        ("draft", "designing"),
        ("queued", "designed"),
        ("designed", "archived"),
        ("archived", "draft"),
        ("failed", "queued"),
    ],
)
def test_validate_status_transition_rejects_other_paths(current, target):
    with pytest.raises(DesignTaskValidationError, match="not allowed"):
        validate_status_transition(current, target)


def test_design_task_status_includes_build_phase_values():
    assert {"building", "built", "build_failed"} <= set(DesignTaskStatus)


@pytest.mark.parametrize("target", ["building", "built", "build_failed"])
def test_planning_transition_rejects_direct_build_phase_targets(target):
    with pytest.raises(DesignTaskValidationError, match="not allowed"):
        validate_status_transition("designed", target)
