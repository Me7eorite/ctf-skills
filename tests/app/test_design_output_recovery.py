"""Unit tests for recovering design JSON emitted into the attempt workspace."""

from __future__ import annotations

import json

from services.challenge_design_service import _parse_design_output_with_workspace_fallback


def test_workspace_fallback_recovers_design_json_when_stdout_is_summary(tmp_path):
    payload = {
        "event": {"flag_format": "flag{...}"},
        "challenges": [{"id": "web-0001"}],
    }
    (tmp_path / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    parsed = _parse_design_output_with_workspace_fallback(
        "Design written to result.json",
        tmp_path,
    )

    assert parsed == payload


def test_workspace_fallback_prefers_state_design_output_json(tmp_path):
    stale = {
        "event": {"flag_format": "flag{...}"},
        "challenges": [{"id": "stale"}],
    }
    payload = {
        "event": {"flag_format": "flag{...}"},
        "challenges": [{"id": "web-0001"}],
    }
    (tmp_path / "a-result.json").write_text(json.dumps(stale), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    (state / "design_output.json").write_text(json.dumps(payload), encoding="utf-8")

    parsed = _parse_design_output_with_workspace_fallback("", tmp_path)

    assert parsed == payload


def test_workspace_fallback_recovers_when_stdout_is_empty_and_state_exists(tmp_path):
    payload = {
        "event": {"flag_format": "flag{...}"},
        "challenges": [{"id": "web-0001"}],
    }
    state = tmp_path / "state"
    state.mkdir()
    (state / "design_output.json").write_text(json.dumps(payload), encoding="utf-8")

    parsed = _parse_design_output_with_workspace_fallback("", tmp_path)

    assert parsed == payload
