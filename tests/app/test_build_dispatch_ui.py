"""Static contract checks for constrained build-worker controls."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_ATTEMPTS_JS = ROOT / "src" / "web" / "static" / "js" / "views" / "build-attempts.js"


def test_build_attempt_actions_use_constrained_endpoints():
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")

    assert "/worker/start`" in source
    assert "/revalidate`" in source
    assert 'runAction("worker")' not in source
    assert 'runAction("validate")' not in source
    assert 'id="ba-validate"' not in source
    assert 'id="ba-worker"' in source
    assert "重新校验" in source
    assert "重试构建" in source
    assert '["failed", "lost", "succeeded"].includes(attempt.status)' in source


def test_detail_poll_supports_append_only_event_updates():
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")

    assert "function patchDetailEvents(nextDetail)" in source
    assert 'insertAdjacentHTML("beforeend"' in source
    assert "#ba-progress-event-count" in source
