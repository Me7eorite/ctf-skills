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
        "artifacts": [
            "README.md",
            "metadata.json",
            "validate.sh",
            "deploy/Dockerfile",
            "deploy/docker-compose.yml",
            "deploy/src/app.py",
            "deploy/_files/start.sh",
            "writenup/wp.md",
            "writenup/exp.py",
        ],
        "flag_location": "FLAG environment variable",
        "validation": "Run exp.py against the local compose service.",
        "implementation_plan": {
            "runtime": "python:3.11-slim",
            "framework": "Flask",
            "service_model": "single docker compose service",
            "entrypoints": [
                {"path": "/", "purpose": "landing page"},
                {"path": "/login", "purpose": "vulnerable auth endpoint"},
                {"path": "/health", "purpose": "readiness check"},
            ],
            "data_model": ["users table", "secrets table stores env flag"],
            "vulnerability": {
                "location": "/login username parameter",
                "mechanism": "string-concatenated SQL query",
            },
            "constraints": ["no docker volumes", "FLAG comes from environment"],
        },
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
    # Unbalanced JSON yields the same diagnostic as "no event/challenges
    # match" because both leave the scan with nothing valid to return.
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"`event` and\s+`challenges`",
    ):
        parse_design_output('prefix {"event": {"x": 1}')


def test_parse_design_output_skips_flag_brace_noise_and_finds_real_json():
    # Mirrors a real Hermes regression: the model emitted a markdown summary
    # that contained ``flag{...}`` BEFORE the actual JSON object. The first
    # ``{`` belongs to the flag string, not the design payload — the parser
    # must skip past it and find the real object further down.
    stdout = (
        "Designed web-0001.\n"
        "Flag: `flag{noisy_brace_inside}`\n"
        '{"event": {"flag_format": "flag{...}"}, '
        '"challenges": [{"id": "web-0001"}]}\n'
    )
    parsed = parse_design_output(stdout)
    assert parsed["challenges"][0]["id"] == "web-0001"


def test_validate_design_payload_accepts_skill_md_shape():
    # The published design-challenges skill uses `player_prompt`,
    # `flag_plan.location`, and `validation` as an object. The validator
    # MUST normalize these into its flat shape so a SKILL.md-conformant
    # agent reply validates without code changes per attempt.
    skill_shape = {
        "event": {"flag_format": "flag{...}"},
        "challenges": [
            {
                "id": "web-0001",
                "title": "Key Confusion",
                "category": "web",
                "difficulty": "medium",
                "points": 300,
                "deployment": "docker compose service on port 8080",
                "port": 8080,
                "primary_technique": "JWT kid path traversal",
                "learning_objective": "Inspect token key selection boundaries",
                # SKILL.md shape uses these three:
                "player_prompt": "Recover the admin note from the service.",
                "flag_plan": {
                    "format": "flag{...}",
                    "location": "FLAG environment variable",
                    "generation": "static",
                },
                "validation": {
                    "reference_solve": "Run exp.py against the local compose service.",
                    "expected_result": "Flag printed to stdout.",
                    "regression_checks": ["exp.py exits 0"],
                },
                "artifacts": ["writeup/wp.md", "solve/solve.py"],
                "hints": [
                    {"stage": 1, "content": "Inspect the JWT header."},
                    {"stage": 2, "content": "The key id influences verification."},
                    {"stage": 3, "content": "Control the key lookup before signing a token."},
                ],
            }
        ],
    }
    validated = validate_design_payload(skill_shape, _parent_task())
    # After normalization the flat fields exist on the persisted payload
    # and the validation_notes is composed from the SKILL.md sub-fields.
    assert (
        validated.challenge["prompt"]
        == "Recover the admin note from the service."
    )
    assert validated.challenge["flag_location"] == "FLAG environment variable"
    assert "Run exp.py" in validated.validation_notes
    assert "Flag printed to stdout." in validated.validation_notes
    assert "exp.py exits 0" in validated.validation_notes
    assert "writenup/wp.md" in validated.challenge["artifacts"]
    assert "writenup/exp.py" in validated.challenge["artifacts"]


def test_validate_design_payload_skill_md_normalization_does_not_clobber_flat_fields():
    # When both shapes coexist (defensive), the validator prefers the
    # flat field that was already there.
    payload = _payload(
        prompt="flat wins",
        player_prompt="should be ignored",
    )
    validated = validate_design_payload(payload, _parent_task())
    assert validated.challenge["prompt"] == "flat wins"


def test_parse_design_output_rejects_summary_only_reply():
    # The "design wrote a file and only summarized" failure mode: stdout
    # has ``{`` characters but none of them open a design-shaped object.
    # We expect a diagnostic that names the Output Contract so the operator
    # can act on it instead of seeing a misleading ``invalid JSON`` error.
    stdout = (
        "Designed web-0001.\n"
        "Machine-readable JSON: `/private/tmp/web-0001-output.json`\n"
        "Flag: `flag{written_to_file_not_returned}`\n"
    )
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"`event` and\s+`challenges`",
    ):
        parse_design_output(stdout)


def test_validate_design_payload_accepts_and_generates_summary():
    result = validate_design_payload(_payload(), _parent_task())

    assert result.flag_format == "flag{...}"
    assert result.validation_notes.startswith("Run exp.py")
    assert "Key Confusion" in result.summary
    assert len(result.summary) <= 280


def test_validate_design_payload_normalizes_object_artifacts_from_logs():
    payload = _payload(
        artifacts={
            "files": ["index.html", "login.php"],
            "services": "HTTP service on port 8080",
            "docker_notes": "Single-service container",
        },
        delivery_format={
            "deploy_tree": {
                "src": ["app.py"],
                "_files": ["start.sh"],
                "dockerfile": "Dockerfile",
                "docker_compose": "docker-compose.yml",
            }
        },
        hints=[
            {"stage": 1, "content": "Look at the token header."},
            {"stage": 2, "content": "Control the key lookup."},
            {"stage": 3, "content": "Sign a forged token."},
        ],
    )

    result = validate_design_payload(payload, _parent_task())

    assert result.challenge["artifacts"] == [
        "deploy/src/index.html",
        "deploy/src/login.php",
        "deploy/src/app.py",
        "deploy/_files/start.sh",
        "deploy/Dockerfile",
        "deploy/docker-compose.yml",
        "README.md",
        "metadata.json",
        "validate.sh",
        "writenup/wp.md",
        "writenup/exp.py",
    ]
    assert result.challenge["hints"] == [
        "Look at the token header.",
        "Control the key lookup.",
        "Sign a forged token.",
    ]


def test_validate_design_payload_ignores_prose_artifacts_but_adds_required_paths():
    payload = _payload(
        artifacts=[
            "Docker container running a PHP web application",
            "Web application accessible on port 8080",
        ]
    )

    result = validate_design_payload(payload, _parent_task())

    assert result.challenge["artifacts"] == [
        "README.md",
        "metadata.json",
        "validate.sh",
        "writenup/wp.md",
        "writenup/exp.py",
        "deploy/Dockerfile",
        "deploy/docker-compose.yml",
        "deploy/src/app.py",
        "deploy/_files/start.sh",
    ]


def test_validate_design_payload_rejects_implementation_level_top_field():
    with pytest.raises(
        ChallengeDesignValidationError,
        match="implementation-level fields: dockerfile_snippet",
    ):
        validate_design_payload(
            _payload(dockerfile_snippet="FROM python:3.11-slim\nCOPY . /app"),
            _parent_task(),
        )


def test_validate_design_payload_rejects_code_in_implementation_plan():
    with pytest.raises(
        ChallengeDesignValidationError,
        match="intent-level",
    ):
        validate_design_payload(
            _payload(
                implementation_plan={
                    "runtime": "python:3.11-slim",
                    "docker": "FROM python:3.11-slim\nRUN apt-get update",
                }
            ),
            _parent_task(),
        )


def test_validate_design_payload_rejects_large_implementation_plan():
    with pytest.raises(
        ChallengeDesignValidationError,
        match="implementation_plan is too large",
    ):
        validate_design_payload(
            _payload(
                implementation_plan={
                    "runtime": "python:3.11-slim",
                    "notes": ["x" * 100 for _ in range(50)],
                }
            ),
            _parent_task(),
        )


def test_validate_design_payload_rejects_long_plan_string():
    with pytest.raises(
        ChallengeDesignValidationError,
        match="longer than",
    ):
        validate_design_payload(
            _payload(
                implementation_plan={
                    "runtime": "python:3.11-slim",
                    "notes": "x" * 600,
                }
            ),
            _parent_task(),
        )


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
