"""Static contract checks for constrained build-worker controls."""

from __future__ import annotations

import subprocess
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


def test_detail_poll_writes_no_dom_for_five_unchanged_cycles():
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")
    start = source.index("function detailWithoutEvents")
    end = source.index("function rebuildDetailEventNodes")
    functions = source[start:end]
    script = f"""
const state = {{ detail: null }};
const detailEventNodes = new Map();
function renderProgressEvent(event) {{ return `<div>${{event.id}}</div>`; }}
{functions}
const detail = {{
  id: "ca789ee5",
  status: "failed",
  progress_events: Array.from({{ length: 22 }}, (_, index) => ({{
    id: index + 1,
    stage: "build",
    status: "running",
    message: "unchanged",
  }})),
}};
state.detail = detail;
let domQueries = 0;
globalThis.document = {{
  querySelector() {{ domQueries += 1; throw new Error("unexpected DOM query"); }},
}};
for (let cycle = 0; cycle < 5; cycle += 1) {{
  const next = JSON.parse(JSON.stringify(detail));
  if (!patchDetailEvents(next)) throw new Error("unchanged detail requested a full render");
  state.detail = next;
}}
if (domQueries !== 0) throw new Error(`expected zero DOM queries, got ${{domQueries}}`);
"""
    subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
