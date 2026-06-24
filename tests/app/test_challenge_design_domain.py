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
from domain.design.difficulty import _count_techniques
from domain.design.mechanical_transforms import MECHANICAL_TRANSFORMS
from domain.design.technique_taxonomy import resolve_sub_technique
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
        # Phase 2 rubric: medium-and-up requires a non-empty scenario on
        # the parent task. Make the baseline rubric-compliant so existing
        # tests focus on the specific behavior they exercise.
        "scenario": "Internal customer-support note portal with admin review.",
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
        # Phase 2 rubric: medium requires 2–3 distinct techniques.
        "secondary_technique": "Key confusion via kid override",
        "techniques": ["JWT kid path traversal", "Key confusion via kid override"],
        "learning_objective": "Inspect token key selection boundaries",
        # Phase 2 rubric: medium-and-up requires a >= 60-char player prompt
        # so the business context is visible.
        "prompt": (
            "Customer-support agents share notes through this internal portal; "
            "recover the admin's pinned note."
        ),
        # Phase 2 rubric: medium caps intended_path at 5 steps.
        "intended_path": [
            "Inspect the JWT in the support portal session cookie",
            "Notice the kid claim is reflected into a key-lookup path",
            "Sign a forged token using a writable key location to reach the admin note",
        ],
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
        # Phase: medium-and-up requires a single intended path with the
        # considered alternate solutions enumerated and blocked.
        "unintended_solutions": [
            "Brute-forcing the HMAC secret — blocked by a 256-bit random key.",
            "Reading FLAG from the image layers — flag is injected only via env at runtime.",
        ],
        # Phase 0: required asset/capability chain (2 effective transitions so
        # the baseline satisfies both medium and hard alignment tests).
        "asset_flow": [
            {
                "stage": 1,
                "player_input_or_capability": "Authenticated support-agent session",
                "technique": "JWT kid path traversal",
                "produced_asset_or_capability": "Forged admin JWT",
                "why_next_stage_requires_it": "The admin note API only accepts admin-signed tokens.",
            },
            {
                "stage": 2,
                "player_input_or_capability": "Forged admin JWT",
                "technique": "Key confusion via kid override",
                "produced_asset_or_capability": "Admin-only signed export URL",
                "why_next_stage_requires_it": "The flag is served only from the signed export URL.",
            },
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


def test_validate_design_payload_rejects_legacy_player_prompt_field():
    # Phase 3 removed the SKILL.md → flat translation layer. The agent must
    # emit ``prompt`` directly; ``player_prompt`` is no longer accepted, and
    # without ``prompt`` the validator reports the missing required field.
    payload = _payload()
    payload["challenges"][0]["player_prompt"] = payload["challenges"][0].pop("prompt")
    with pytest.raises(
        ChallengeDesignValidationError, match="prompt must be a non-empty string"
    ):
        validate_design_payload(payload, _parent_task())


def test_validate_design_payload_rejects_nested_validation_object():
    # ``validation`` must be a single string. Nested SKILL.md objects are
    # rejected outright rather than translated.
    payload = _payload()
    payload["challenges"][0]["validation"] = {
        "reference_solve": "Run exp.py",
        "expected_result": "Flag printed",
    }
    with pytest.raises(
        ChallengeDesignValidationError, match="validation must be a non-empty string"
    ):
        validate_design_payload(payload, _parent_task())


def test_validate_design_payload_rejects_legacy_artifact_paths():
    # Legacy paths like ``writeup/wp.md`` and bare ``solve.py`` were silently
    # rewritten by the old normalizer. Phase 3 cut that layer; the agent must
    # emit ``writenup/wp.md`` and ``writenup/exp.py`` directly.
    payload = _payload(artifacts=["writeup/wp.md", "solve/solve.py"])
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"artifacts must be local challenge-relative file paths"
        r"|web/pwn artifacts must include",
    ):
        validate_design_payload(payload, _parent_task())


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


def test_validate_design_payload_rejects_object_artifacts():
    # The old normalizer translated ``artifacts`` objects (with deploy_tree,
    # files, services keys) into flat path lists. Phase 3 requires the
    # agent to emit the flat list itself.
    payload = _payload(
        artifacts={
            "files": ["index.html", "login.php"],
            "docker_notes": "Single-service container",
        },
    )
    with pytest.raises(
        ChallengeDesignValidationError, match="artifacts must be a non-empty list"
    ):
        validate_design_payload(payload, _parent_task())


def test_validate_design_payload_rejects_prose_artifacts():
    # Prose strings ("Docker container running a PHP web application") used
    # to be silently dropped while required paths were auto-filled. Now a
    # non-path entry causes a hard reject so the operator catches it.
    payload = _payload(
        artifacts=[
            "Docker container running a PHP web application",
            "Web application accessible on port 8080",
        ]
    )
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"artifacts must be local challenge-relative file paths",
    ):
        validate_design_payload(payload, _parent_task())


@pytest.mark.parametrize(
    "artifact",
    [
        "attachments/crackme",
        "deploy/Makefile",
        "src/crackme.c",
    ],
)
def test_validate_design_payload_accepts_safe_artifacts_without_extensions(
    artifact: str,
):
    payload = _payload()
    payload["challenges"][0]["artifacts"].append(artifact)

    validate_design_payload(payload, _parent_task())


@pytest.mark.parametrize(
    "artifact",
    [
        "attachments/../flag.txt",
        "dist/crackme",
        "dist/../../etc/passwd",
        "deploy/",
        "src//crackme.c",
    ],
)
def test_validate_design_payload_rejects_unsafe_artifact_paths(artifact: str):
    payload = _payload()
    payload["challenges"][0]["artifacts"].append(artifact)

    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"artifacts must be local challenge-relative file paths",
    ):
        validate_design_payload(payload, _parent_task())


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


def test_validate_design_payload_rejects_short_hints():
    # Old behavior auto-padded with generated hints when fewer than 3 were
    # given. Phase 3 requires the agent to emit exactly 3 staged hints.
    with pytest.raises(
        ChallengeDesignValidationError,
        match="hints must contain exactly 3 entries",
    ):
        validate_design_payload(_payload(hints=["only one"]), _parent_task())


def test_validate_design_payload_rejects_mapping_hints():
    # Stage-keyed mappings ({stage_1: ..., stage_2: ...}) are no longer
    # translated; the agent must emit a flat list of 3 strings.
    with pytest.raises(
        ChallengeDesignValidationError,
        match="hints must contain exactly 3 entries",
    ):
        validate_design_payload(
            _payload(
                hints={
                    "stage_1": "Inspect the visible token.",
                    "stage_2": "Control the key lookup.",
                    "stage_3": "Forge the trusted state.",
                }
            ),
            _parent_task(),
        )


def test_validate_design_payload_rejects_web_without_docker():
    with pytest.raises(ChallengeDesignValidationError, match="docker"):
        validate_design_payload(_payload(deployment="static files"), _parent_task())


def test_validate_design_payload_rejects_url_artifact():
    with pytest.raises(ChallengeDesignValidationError, match="relative paths"):
        validate_design_payload(_payload(artifacts=["https://example.test/app.zip"]), _parent_task())


def test_validate_design_payload_accepts_local_validation_url():
    result = validate_design_payload(
        _payload(validation="Run exp.py against http://127.0.0.1:8080/health."),
        _parent_task(),
    )

    assert "127.0.0.1:8080" in result.validation_notes


def test_validate_design_payload_rejects_external_validation_url():
    with pytest.raises(ChallengeDesignValidationError, match="external HTTP URLs"):
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


# ---------- Phase 2 difficulty rubric ----------


def _easy_payload(**overrides):
    """A rubric-compliant easy-tier baseline used by alignment tests."""
    payload = _payload(
        difficulty="easy",
        points=100,
        # easy = exactly 1 technique, at most 4 intended_path steps,
        # short prompt is fine, no implementation_plan required.
        primary_technique="DOM XSS",
        secondary_technique=None,
        techniques=["DOM XSS"],
        prompt="Find the reflected XSS and pop an alert.",
        intended_path=["Inspect the search reflection", "Inject a script tag"],
    )
    payload["challenges"][0].pop("secondary_technique")
    payload["challenges"][0].pop("implementation_plan", None)
    payload["challenges"][0].update(overrides)
    return payload


def _easy_task(**overrides):
    return _parent_task(difficulty="easy", points=100, **overrides)


def test_difficulty_easy_accepts_single_technique():
    result = validate_design_payload(_easy_payload(), _easy_task())
    assert result.challenge["difficulty"] == "easy"


def test_difficulty_easy_rejects_two_techniques():
    payload = _easy_payload(
        secondary_technique="CSP bypass",
        techniques=["DOM XSS", "CSP bypass"],
    )
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"easy allows at most 1 distinct techniques",
    ):
        validate_design_payload(payload, _easy_task())


def test_difficulty_easy_allows_omitting_unintended_solutions():
    # easy may have multiple solve paths; the field is optional.
    payload = _easy_payload()
    payload["challenges"][0].pop("unintended_solutions", None)
    result = validate_design_payload(payload, _easy_task())
    assert result.challenge["difficulty"] == "easy"


def test_difficulty_medium_requires_unintended_solutions():
    payload = _payload()
    payload["challenges"][0].pop("unintended_solutions", None)
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"single intended solve path",
    ):
        validate_design_payload(payload, _parent_task())


def test_difficulty_medium_rejects_empty_unintended_solutions():
    payload = _payload(unintended_solutions=[])
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"unintended_solutions",
    ):
        validate_design_payload(payload, _parent_task())


def test_difficulty_medium_accepts_enumerated_unintended_solutions():
    result = validate_design_payload(_payload(), _parent_task())
    assert result.challenge["difficulty"] == "medium"


def test_difficulty_medium_requires_asset_flow_transition():
    payload = _payload()
    payload["challenges"][0].pop("asset_flow", None)
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"asset/capability chain",
    ):
        validate_design_payload(payload, _parent_task())


def test_difficulty_medium_rejects_filler_only_asset_flow():
    # A stage that produces nothing required is not an effective transition.
    payload = _payload(
        asset_flow=[
            {
                "stage": 1,
                "player_input_or_capability": "login form",
                "technique": "sqli",
                "produced_asset_or_capability": "",
                "why_next_stage_requires_it": "",
            }
        ]
    )
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"at least 1 effective transition",
    ):
        validate_design_payload(payload, _parent_task())


def test_difficulty_hard_requires_two_transitions():
    # The baseline asset_flow has 2 transitions → hard passes; trimming to 1 fails.
    one_transition = _payload()["challenges"][0]["asset_flow"][:1]
    payload = _payload(
        difficulty="hard",
        techniques=["a", "b", "c"],
        primary_technique="a",
        secondary_technique="b",
        asset_flow=one_transition,
    )
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"at least 2 effective transition",
    ):
        validate_design_payload(payload, _parent_task(difficulty="hard"))


def test_difficulty_easy_allows_omitting_asset_flow():
    payload = _easy_payload()
    payload["challenges"][0].pop("asset_flow", None)
    result = validate_design_payload(payload, _easy_task())
    assert result.challenge["difficulty"] == "easy"


def test_difficulty_counts_all_mechanical_transforms_as_one_technique():
    challenge = {
        "techniques": ["xor", "base64"],
        "primary_technique": "xor-decrypt",
        "secondary_technique": "base64-decode",
    }

    assert _count_techniques(challenge) == 1


def test_difficulty_mechanical_transform_is_free_with_real_technique():
    challenge = {
        "techniques": ["sqli", "base64"],
        "primary_technique": "SQL injection",
    }

    assert _count_techniques(challenge) == 1


def test_difficulty_counts_distinct_non_mechanical_techniques():
    challenge = {"techniques": ["sqli", "xss"]}

    assert _count_techniques(challenge) == 2


def test_difficulty_mechanical_fold_is_order_free():
    left = {"techniques": ["base64", "sqli", "xor"]}
    right = {"techniques": ["xor", "sqli", "base64"]}

    assert _count_techniques(left) == _count_techniques(right) == 1


def test_difficulty_counts_analysis_labels_that_resemble_transforms():
    challenge = {"techniques": ["xor key recovery", "logic flaw"]}

    assert _count_techniques(challenge) == 2


def test_difficulty_counts_primary_technique_when_techniques_list_is_empty():
    challenge = {"techniques": [], "primary_technique": "sqli"}

    assert _count_techniques(challenge) == 1


def test_mechanical_transform_boundary_matches_taxonomy_normalization():
    assert (
        resolve_sub_technique({"label": "xor key recovery"})
        not in MECHANICAL_TRANSFORMS
    )
    assert resolve_sub_technique({"label": "xor-decrypt"}) in MECHANICAL_TRANSFORMS


def test_difficulty_easy_accepts_layered_decode_chain_as_one_technique():
    parent = _parent_task(category="re", difficulty="easy", points=100, port=None)
    payload = _easy_payload(
        category="re",
        deployment="static",
        port=None,
        primary_technique="strings",
        techniques=["strings", "base64"],
        intended_path=[
            "Run strings on the binary",
            "Decode the extracted base64 blob",
            "Submit the flag",
        ],
    )

    result = validate_design_payload(payload, parent)

    assert result.challenge["difficulty"] == "easy"
    assert _count_techniques(result.challenge) == 1


def test_difficulty_reporter_decode_chains_both_validate_as_easy():
    cases = [
        (
            "re-strings-0001",
            "strings",
            ["strings", "base64"],
            [
                "Run strings on the binary",
                "Decode the extracted base64 blob",
                "Submit the flag",
            ],
        ),
        (
            "re-ida-0002",
            "xor",
            ["xor-decrypt", "base64-decode"],
            [
                "Open the binary in IDA",
                "Apply the visible xor transform",
                "Decode the resulting base64 blob",
                "Submit the flag",
            ],
        ),
    ]

    for challenge_id, primary, techniques, intended_path in cases:
        parent = _parent_task(
            category="re",
            difficulty="easy",
            points=100,
            port=None,
            challenge_id=challenge_id,
        )
        payload = _easy_payload(
            id=challenge_id,
            category="re",
            deployment="static",
            port=None,
            primary_technique=primary,
            techniques=techniques,
            intended_path=intended_path,
        )

        result = validate_design_payload(payload, parent)

        assert result.challenge["difficulty"] == "easy"
        assert _count_techniques(result.challenge) == 1


def test_difficulty_medium_rejects_short_prompt():
    payload = _payload(prompt="Find the bug.")
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"medium-and-up difficulty requires a player prompt",
    ):
        validate_design_payload(payload, _parent_task())


def test_difficulty_medium_rejects_single_technique():
    payload = _payload(
        secondary_technique=None,
        techniques=["JWT kid path traversal"],
    )
    payload["challenges"][0].pop("secondary_technique")
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"medium requires at least 2 distinct techniques",
    ):
        validate_design_payload(payload, _parent_task())


def test_difficulty_hard_requires_implementation_plan():
    parent = _parent_task(difficulty="hard")
    payload = _payload(
        difficulty="hard",
        techniques=["JWT kid", "Key confusion", "Token replay"],
        secondary_technique="Key confusion via kid override",
        intended_path=[
            "Inspect the JWT in the session cookie",
            "Notice the kid claim drives the key path",
            "Forge the key path to point at a writable file",
            "Sign a forged admin token and read the flag",
        ],
    )
    payload["challenges"][0]["techniques"] = [
        "JWT kid path traversal",
        "Key confusion via kid override",
        "Token replay across services",
    ]
    payload["challenges"][0].pop("implementation_plan")
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"hard requires a non-empty implementation_plan",
    ):
        validate_design_payload(payload, parent)


def test_difficulty_expert_requires_novelty():
    parent = _parent_task(difficulty="expert")
    payload = _payload(
        difficulty="expert",
        intended_path=[
            "Inspect the JWT signed by the in-house JWS library",
            "Recognize the alg parameter is consulted after key lookup",
            "Trigger key reuse across two endpoints with mismatched alg",
            "Forge an admin token by chaining the differential",
        ],
    )
    # Phase 2 rubric demands a substantive `novelty` field for expert.
    payload["challenges"][0].pop("novelty", None)
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"expert difficulty requires a `novelty` field",
    ):
        validate_design_payload(payload, parent)


def test_difficulty_expert_accepts_substantive_novelty():
    parent = _parent_task(difficulty="expert")
    payload = _payload(
        difficulty="expert",
        intended_path=[
            "Inspect the JWT signed by the in-house JWS library",
            "Recognize the alg parameter is consulted after key lookup",
            "Trigger key reuse across two endpoints with mismatched alg",
            "Forge an admin token by chaining the differential",
        ],
        novelty=(
            "Algorithm-confusion across two unsynchronized JWS verifiers "
            "in an in-house library — no public CVE describes this exact path."
        ),
    )
    result = validate_design_payload(payload, parent)
    assert result.challenge["difficulty"] == "expert"


def test_difficulty_rejects_oversized_implementation_plan_for_medium():
    # Medium caps explicitly declared build/deploy components at 7.
    bloat = {
        "runtime": "Python",
        "components": [f"component-{i}" for i in range(8)],
    }
    payload = _payload(implementation_plan=bloat)
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"medium allows at most 7 explicit implementation_plan.components",
    ):
        validate_design_payload(payload, _parent_task())


def test_difficulty_accepts_implementation_plan_at_hard_cap():
    # Hard cap is 10; a plan with exactly 10 explicit components is allowed.
    parent = _parent_task(difficulty="hard")
    payload = _payload(
        difficulty="hard",
        secondary_technique="Key confusion via kid override",
        techniques=[
            "JWT kid path traversal",
            "Key confusion via kid override",
            "Token replay across services",
        ],
        intended_path=[
            "Inspect the JWT in the session cookie",
            "Notice the kid claim drives the key path",
            "Forge the key path to point at a writable file",
            "Sign a forged admin token and read the flag",
        ],
        implementation_plan={
            "runtime": "Python",
            "framework": "Flask",
            "components": [f"component-{i}" for i in range(10)],
        },
    )
    result = validate_design_payload(payload, parent)
    assert result.challenge["difficulty"] == "hard"


def test_difficulty_does_not_count_plan_metadata_as_components():
    metadata_plan = {f"metadata_{i}": f"value {i}" for i in range(12)}
    payload = _payload(implementation_plan=metadata_plan)

    validate_design_payload(payload, _parent_task())


def test_implementation_plan_components_requires_string_array():
    payload = _payload(implementation_plan={"components": "web, database"})
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"implementation_plan.components must be an array",
    ):
        validate_design_payload(payload, _parent_task())


def _easy_re_payload(step_count: int):
    payload = _payload(
        category="re",
        difficulty="easy",
        deployment="static",
        port=None,
        techniques=["static comparison analysis"],
        primary_technique="static comparison analysis",
        secondary_technique=None,
        intended_path=[f"Solve step {index}" for index in range(1, step_count + 1)],
    )
    payload["challenges"][0].pop("secondary_technique")
    return payload


def test_difficulty_easy_accepts_four_intended_path_steps():
    parent = _parent_task(category="re", difficulty="easy", port=None)

    validate_design_payload(_easy_re_payload(4), parent)


def test_difficulty_easy_rejects_five_intended_path_steps():
    parent = _parent_task(category="re", difficulty="easy", port=None)
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"easy allows at most 4 intended_path steps",
    ):
        validate_design_payload(_easy_re_payload(5), parent)


def test_difficulty_hard_accepts_one_intended_path_step_with_enough_techniques():
    parent = _parent_task(difficulty="hard")
    payload = _payload(
        difficulty="hard",
        techniques=[
            "JWT kid path traversal",
            "Key confusion via kid override",
            "Token replay across services",
        ],
        primary_technique="JWT kid path traversal",
        secondary_technique="Key confusion via kid override",
        intended_path=["Chain the three declared techniques to reach the flag."],
    )

    result = validate_design_payload(payload, parent)

    assert result.challenge["difficulty"] == "hard"


def test_difficulty_hard_single_technique_still_fails_technique_min():
    parent = _parent_task(difficulty="hard")
    payload = _payload(
        difficulty="hard",
        techniques=["JWT kid path traversal"],
        primary_technique="JWT kid path traversal",
        secondary_technique=None,
        intended_path=["Exploit the single technique."],
    )
    payload["challenges"][0].pop("secondary_technique")

    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"hard requires at least 3 distinct techniques",
    ):
        validate_design_payload(payload, parent)


def test_difficulty_expert_single_technique_still_fails_technique_min():
    parent = _parent_task(difficulty="expert")
    payload = _payload(
        difficulty="expert",
        techniques=["JWT kid path traversal"],
        primary_technique="JWT kid path traversal",
        secondary_technique=None,
        intended_path=["Exploit the single technique."],
        novelty=(
            "A verifier state desynchronization trick specific to this custom "
            "challenge implementation."
        ),
    )
    payload["challenges"][0].pop("secondary_technique")

    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"expert requires at least 2 distinct techniques",
    ):
        validate_design_payload(payload, parent)


def test_difficulty_legacy_grandfather_skips_alignment():
    # A pre-rubric design with only one technique on a medium task: the
    # alignment check would normally reject it, but operators can pass
    # legacy_grandfather=True to keep the historical row valid.
    payload = _payload(
        secondary_technique=None,
        techniques=["JWT kid path traversal"],
    )
    payload["challenges"][0].pop("secondary_technique")
    validate_design_payload(payload, _parent_task(), legacy_grandfather=True)


def test_difficulty_lenient_mode_logs_and_passes(monkeypatch, caplog):
    # GLM-5 / DeepSeek deployments set DESIGN_DIFFICULTY_ENFORCEMENT=lenient
    # so a design that misses the rubric is logged but not rejected.
    monkeypatch.setenv("DESIGN_DIFFICULTY_ENFORCEMENT", "lenient")
    payload = _payload(
        secondary_technique=None,
        techniques=["JWT kid path traversal"],
    )
    payload["challenges"][0].pop("secondary_technique")

    with caplog.at_level("WARNING", logger="domain.design.difficulty"):
        validate_design_payload(payload, _parent_task())

    assert any(
        "design difficulty soft-passed" in rec.message for rec in caplog.records
    )


def test_difficulty_strict_mode_still_raises(monkeypatch):
    # Default behavior is preserved: env var unset (or 'strict') = raise.
    monkeypatch.setenv("DESIGN_DIFFICULTY_ENFORCEMENT", "strict")
    payload = _payload(
        secondary_technique=None,
        techniques=["JWT kid path traversal"],
    )
    payload["challenges"][0].pop("secondary_technique")
    with pytest.raises(
        ChallengeDesignValidationError,
        match=r"medium requires at least 2 distinct techniques",
    ):
        validate_design_payload(payload, _parent_task())
