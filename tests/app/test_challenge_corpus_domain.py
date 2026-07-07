"""Unit tests for corpus fingerprint canonicalization."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from domain.challenge_corpus import (
    CORPUS_FINGERPRINT_SCHEMA_VERSION,
    CorpusComparisonTarget,
    CorpusDecision,
    CorpusDecisionScope,
    CorpusDecisionValue,
    CorpusGatePolicy,
    CorpusReviewDecision,
    CorpusReviewDecisionValue,
    ObservationReviewDecision,
    ObservationReviewDecisionValue,
    aggregate_corpus_decision,
    canonical_token_fingerprint,
    corpus_decision_is_effectively_accepted,
    corpus_review_allows_acceptance,
    evaluate_corpus_member,
    generate_corpus_fingerprints,
    observation_review_allows_acceptance,
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


def test_effective_acceptance_helpers_keep_review_separate_from_decision() -> None:
    now = datetime.now(timezone.utc)
    decision = CorpusDecision(
        id=uuid4(),
        batch_id=uuid4(),
        member_id=uuid4(),
        scope=CorpusDecisionScope.MEMBER.value,
        decision=CorpusDecisionValue.REVIEW_REQUIRED.value,
        reasons=("source_similarity",),
        policy_version=1,
        is_current=True,
        created_at=now,
        superseded_at=None,
    )
    corpus_review = CorpusReviewDecision(
        id=uuid4(),
        corpus_decision_id=decision.id,
        decision=CorpusReviewDecisionValue.APPROVED.value,
        actor="operator",
        reason="acceptable variation",
        scope="production-publication",
        created_at=now,
    )
    observation_review = ObservationReviewDecision(
        id=uuid4(),
        artifact_observation_id=uuid4(),
        decision=ObservationReviewDecisionValue.ACCEPTED.value,
        actor="operator",
        reason="observer cannot infer toolchain",
        scope="validation-success",
        created_at=now,
    )

    assert not corpus_decision_is_effectively_accepted(decision)
    assert corpus_review_allows_acceptance(corpus_review)
    assert corpus_decision_is_effectively_accepted(
        decision,
        has_allowed_review=corpus_review_allows_acceptance(corpus_review),
    )
    assert decision.decision == CorpusDecisionValue.REVIEW_REQUIRED.value
    assert observation_review_allows_acceptance(observation_review)


def test_renamed_constant_only_clone_blocks_on_source_similarity() -> None:
    candidate = {
        "combined": "candidate",
        "source": canonical_token_fingerprint(
            "def solve(user_id):\n return user_id == 1337 and 'flag{a}'\n"
        ).as_mapping(),
        "solver": canonical_token_fingerprint("print('new')").as_mapping(),
    }
    renamed = {
        "combined": "different",
        "source": canonical_token_fingerprint(
            "def solve(account_id):\n return account_id == 9001 and 'flag{b}'\n"
        ).as_mapping(),
        "solver": canonical_token_fingerprint("print('old')").as_mapping(),
    }

    result = evaluate_corpus_member(
        member_fingerprints=candidate,
        history_targets=[CorpusComparisonTarget(fingerprints=renamed)],
    )

    assert result.decision == CorpusDecisionValue.BLOCKED.value
    assert "source_similarity_block" in result.reasons
    assert result.matches[0].fingerprint_type == "source"


def test_borderline_solver_similarity_routes_to_review() -> None:
    candidate = {
        "combined": "candidate",
        "source": canonical_token_fingerprint("unique source").as_mapping(),
        "solver": {"tokens": ["a", "b", "c", "d", "e"], "sha256": "candidate"},
    }
    similar = {
        "combined": "different",
        "source": canonical_token_fingerprint("other source").as_mapping(),
        "solver": {"tokens": ["a", "b", "c", "x", "y"], "sha256": "similar"},
    }

    result = evaluate_corpus_member(
        member_fingerprints=candidate,
        history_targets=[CorpusComparisonTarget(fingerprints=similar)],
        policy=CorpusGatePolicy(solver_review_threshold=0.4, solver_block_threshold=0.9),
    )

    assert result.decision == CorpusDecisionValue.REVIEW_REQUIRED.value
    assert "solver_similarity_review" in result.reasons


def test_distinct_implementation_profiles_pass() -> None:
    result = evaluate_corpus_member(
        member_fingerprints={
            "combined": "one",
            "source": canonical_token_fingerprint("alpha beta").as_mapping(),
            "solver": canonical_token_fingerprint("solve alpha").as_mapping(),
        },
        history_targets=[
            CorpusComparisonTarget(
                fingerprints={
                    "combined": "two",
                    "source": canonical_token_fingerprint("gamma delta").as_mapping(),
                    "solver": canonical_token_fingerprint("solve gamma").as_mapping(),
                }
            )
        ],
    )

    assert result.decision == CorpusDecisionValue.PASSED.value
    assert result.reasons == ()


def test_aggregate_blocks_when_any_member_blocked_even_with_review() -> None:
    now = datetime.now(timezone.utc)
    blocked = CorpusDecision(
        id=uuid4(),
        batch_id=uuid4(),
        member_id=uuid4(),
        scope=CorpusDecisionScope.MEMBER.value,
        decision=CorpusDecisionValue.BLOCKED.value,
        reasons=("exact_combined_duplicate",),
        policy_version=1,
        is_current=True,
        created_at=now,
        superseded_at=None,
    )
    reviewed = CorpusDecision(
        id=uuid4(),
        batch_id=blocked.batch_id,
        member_id=uuid4(),
        scope=CorpusDecisionScope.MEMBER.value,
        decision=CorpusDecisionValue.REVIEW_REQUIRED.value,
        reasons=("source_similarity_review",),
        policy_version=1,
        is_current=True,
        created_at=now,
        superseded_at=None,
    )

    aggregate = aggregate_corpus_decision(
        [blocked, reviewed],
        member_acceptance=[False, True],
    )

    assert aggregate.decision == CorpusDecisionValue.BLOCKED.value
    assert "member_blocked:exact_combined_duplicate" in aggregate.reasons
