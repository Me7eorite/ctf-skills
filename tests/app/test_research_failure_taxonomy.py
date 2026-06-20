"""Failure taxonomy tests for research run diagnostics."""

from __future__ import annotations

import pytest

from domain.research_failure_taxonomy import classify_last_error


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Hermes exited with 124", "timeout"),
        ("lease expired", "lease_expired"),
        ("unparseable_output:no_terminal_json_object", "parse_failure"),
        ("unparseable_output:sources_not_list", "parse_failure"),
        ("insufficient_findings:got=3,need=5", "quality_gate"),
        ("url_shape_invalid:not-a-url", "field_validation"),
        ("content_hash_shape_invalid:zzz", "field_validation"),
        ("research output field 'sources' must be a list", "field_validation"),
        ("source field 'url' must be a non-empty string", "field_validation"),
        ("finding source_indices must be a list", "field_validation"),
        ("source index 2 is out of range", "field_validation"),
        ("finding must include source_indices or source_ids", "field_validation"),
        ("profile_not_bound", "binding"),
        ("profile_disabled:default", "binding"),
        ("Hermes profile 'ctf-research-bot' does not exist", "binding"),
        ("Hermes exited with 137", "runtime"),
        ("generation_request 00000000-0000-0000-0000-000000000000 does not exist", "runtime"),
        ("cancelled by operator", "cancelled"),
        ("a wild new error appears", "unknown"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_classifies_current_research_failure_shapes(text: str | None, category: str) -> None:
    assert classify_last_error(text).category == category


def test_timeout_takes_priority_over_generic_runtime() -> None:
    result = classify_last_error("Hermes exited with 124")

    assert result.category == "timeout"
    assert any("hermes-timeout-seconds" in action for action in result.actions)


def test_dynamic_quality_gate_counts_are_in_description() -> None:
    result = classify_last_error("insufficient_findings:got=3,need=5")

    assert result.category == "quality_gate"
    assert "3" in result.description
    assert "5" in result.description


def test_case_insensitive_matching() -> None:
    assert classify_last_error("HERMES EXITED WITH 137").category == "runtime"
    assert classify_last_error("PROFILE_DISABLED:default").category == "binding"


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "x" * 5000,
        "\x00\x01not a known failure",
    ],
)
def test_arbitrary_input_does_not_raise(text: str | None) -> None:
    result = classify_last_error(text)

    assert result.category == "unknown"
    assert result.title


def test_unknown_echoes_original_non_empty_text() -> None:
    result = classify_last_error("new failure code")

    assert result.category == "unknown"
    assert "new failure code" in result.description


@pytest.mark.parametrize(
    "text",
    [
        "Hermes exited with 124",
        "lease expired",
        "unparseable_output:no_terminal_json_object",
        "insufficient_findings:got=1,need=2",
        "source field 'url' must be a non-empty string",
        "profile_not_bound",
        "Hermes exited with 7",
    ],
)
def test_actionable_categories_have_actions(text: str) -> None:
    assert classify_last_error(text).actions


@pytest.mark.parametrize("text", ["cancelled by operator", "unknown error"])
def test_cancelled_and_unknown_may_have_no_actions(text: str) -> None:
    result = classify_last_error(text)

    assert result.category in {"cancelled", "unknown"}
