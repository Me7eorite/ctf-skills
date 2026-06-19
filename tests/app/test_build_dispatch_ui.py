"""Static contract checks for constrained build-worker controls."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_ATTEMPTS_JS = ROOT / "src" / "web" / "static" / "js" / "views" / "build-attempts.js"


def test_build_attempt_actions_use_constrained_endpoints():
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")

    assert 'endpoint = "/api/build-attempts/worker/start"' in source
    assert "/worker/start`" in source
    assert 'runAction("worker")' not in source
    assert "Choose a category before starting a worker" in source
    assert 'runAction("validate")' in source
