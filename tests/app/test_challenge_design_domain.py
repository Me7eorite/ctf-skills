"""Unit tests for structured challenge design domain validation."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from domain.challenge_design_validators import (
    DEFAULT_FLAG_FORMAT,
    ChallengeDesignValidationError,
    parse_design_output,
    run_quality_gate,
    validate_design_payload,
)
from domain.design_tasks import DesignTask


def _parent_task(**overrides) -> DesignTask:
    values = {
        "id": uuid4(),
        "generation_request_id": uuid4(),
        "research_run_id": uuid4(),
        "task_no": 1,
        "challenge_id": "web-0001",
        "title": "Key Confusion",
        "category": "web",
        "difficulty": "medium",
        "primary_technique": "JWT kid path traversal",
        "learning_objective": "Inspect token key selection boundaries",
        "points": 300,
        "port": 8080,
        "scenario": "",
        "constraints": {},
        "evidence_summary": "",
        "finding_ids": [],
        "status": "queued",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return DesignTask(**values)


def _payload(**challenge_overrides):
    challenge = {
        "id": "web-0001",
        "title": "Key Confusion",
        "category": "web",
        "difficulty": "medium",
        "points": 300,
        "deployment": "single docker compose service on port 8080",
        "port": 8080,
        "primary_technique": "JWT kid path traversal",
        "learning_objective": "Inspect token key selection boundaries",
        "prompt": "Recover the admin note from the service.",
        "artifacts": ["deploy/Dockerfile", "deploy/src/app.py"],
        "flag_location": "FLAG environment variable",
        "validation": "Run solve.py against the local compose service.",
        "hints": [
            "Inspect the JWT header.",
            "The key id influences verification.",
            "Control the key lookup before signing a token.",
        ],
    }
    challenge.update(challenge_overrides)
    return {"event": {"flag_format": "flag{...}"}, "challenges": [challenge]}


def test_parse_design_output_strips_json_fence_and_trailing_text():
    parsed = parse_design_output(
        '```json\n{"event": {}, "challenges": [{"id": "x"}]}\n```\nextra'
    )
    assert parsed["event"] == {}
    assert parsed["challenges"][0]["id"] == "x"


def test_parse_design_output_rejects_unbalanced_json():
    with pytest.raises(ChallengeDesignValidationError, match="unbalanced"):
        parse_design_output('prefix {"event": {"x": 1}')


def test_validate_design_payload_accepts_and_generates_summary():
    result = validate_design_payload(_payload(), _parent_task())

    assert result.flag_format == "flag{...}"
    assert result.validation_notes.startswith("Run solve.py")
    assert "Key Confusion" in result.summary
    assert len(result.summary) <= 280


def test_validate_design_payload_fills_default_flag_format_without_mutating_input():
    payload = _payload()
    del payload["event"]["flag_format"]

    result = validate_design_payload(payload, _parent_task())

    assert result.flag_format == DEFAULT_FLAG_FORMAT
    assert result.payload["event"]["flag_format"] == DEFAULT_FLAG_FORMAT
    assert "flag_format" not in payload["event"]


def test_validate_design_payload_rejects_parent_mismatch():
    with pytest.raises(ChallengeDesignValidationError, match="category"):
        validate_design_payload(_payload(category="pwn"), _parent_task())


def test_validate_design_payload_rejects_bad_hint_count():
    with pytest.raises(ChallengeDesignValidationError, match="hints"):
        validate_design_payload(_payload(hints=["only one"]), _parent_task())


def test_validate_design_payload_rejects_web_without_docker():
    with pytest.raises(ChallengeDesignValidationError, match="docker"):
        validate_design_payload(_payload(deployment="static files"), _parent_task())


def test_validate_design_payload_rejects_url_artifact():
    with pytest.raises(ChallengeDesignValidationError, match="relative paths"):
        validate_design_payload(_payload(artifacts=["https://example.test/app.zip"]), _parent_task())


def test_validate_design_payload_rejects_validation_url():
    with pytest.raises(ChallengeDesignValidationError, match="HTTP URLs"):
        validate_design_payload(
            _payload(validation="Open https://example.test and solve it"),
            _parent_task(),
        )


def test_validate_design_payload_truncates_generated_summary():
    payload = _payload(
        title="T" * 180,
        primary_technique="P" * 180,
        learning_objective="L" * 180,
    )

    result = validate_design_payload(payload, _parent_task())

    assert len(result.summary) == 280


def test_run_quality_gate_passes_valid_payload():
    passed, notes = run_quality_gate(_payload())

    assert passed is True
    assert notes == []


def test_run_quality_gate_reports_explicit_predicates():
    payload = _payload(hints=["one"], artifacts=["/etc/passwd"], deployment="static")

    passed, notes = run_quality_gate(payload)

    assert passed is False
    assert "hints are not staged as three entries" in notes
    assert "artifacts must be relative paths" in notes
    assert "web/pwn deployment must be containerized" in notes
