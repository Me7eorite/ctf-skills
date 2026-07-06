"""Unit tests for corpus fingerprint canonicalization."""

from __future__ import annotations

from domain.challenge_corpus import (
    CORPUS_FINGERPRINT_SCHEMA_VERSION,
    canonical_token_fingerprint,
    generate_corpus_fingerprints,
)


def _profile() -> dict[str, object]:
    return {
        "semantic": {"family": "injection", "sub_technique": "sqli"},
        "solve": {
            "analysis_mode": "blackbox",
            "required_action": "payload_injection",
            "chain_shape": "inject-exfiltrate",
            "required_tool_class": "http_client",
        },
        "implementation": {
            "artifact_format": "container",
            "language": "python",
            "runtime": "flask",
            "interaction": "http_form",
            "control_structure": "route_handler",
            "flag_concealment": "database_record",
        },
        "presentation": {
            "scenario_type": "ticket_queue",
            "input_model": "web_form",
        },
    }


def test_token_fingerprint_normalizes_flags_numbers_hex_and_comments() -> None:
    first = canonical_token_fingerprint(
        """
        # setup
        token = "flag{alpha}"
        if user_id == 1337:
            return 0x41414141
        """
    )
    second = canonical_token_fingerprint(
        """
        # renamed comment
        token = "flag{beta}"
        if user_id == 9001:
            return 0x42424242
        """
    )

    assert first.tokens == second.tokens
    assert "<flag>" in first.tokens
    assert "<num>" in first.tokens
    assert "<hex>" in first.tokens
    assert first.sha256 == second.sha256


def test_generate_corpus_fingerprints_uses_profile_signatures_and_token_schema() -> None:
    fingerprints = generate_corpus_fingerprints(
        profile=_profile(),
        category="web",
        policy_version=1,
        source_texts=["app.route('/login')", "query = 'select * from users'"],
        solver_texts=["requests.post('/login', data={'q': '1 or 1=1'})"],
        intended_path={
            "actions": ["payload_injection", "admin_read"],
            "assets": ["session"],
        },
    )

    payload = fingerprints.as_mapping()
    assert payload["schema_version"] == CORPUS_FINGERPRINT_SCHEMA_VERSION
    assert set(payload) == {
        "schema_version",
        "semantic",
        "solve",
        "implementation",
        "combined",
        "source",
        "solver",
        "intended_path",
    }
    assert len(payload["combined"]) == 64
    assert payload["source"]["token_count"] > 0
    assert payload["solver"]["sha256"] != payload["source"]["sha256"]
