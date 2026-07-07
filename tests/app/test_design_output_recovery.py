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
