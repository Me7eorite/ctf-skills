"""Static contract checks for constrained build-worker controls."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_ATTEMPTS_JS = ROOT / "src" / "web" / "static" / "js" / "views" / "build-attempts.js"
BUILD_ATTEMPTS_CSS = ROOT / "src" / "web" / "static" / "css" / "views" / "build-attempts.css"
WORKER_POOL_JS = ROOT / "src" / "web" / "static" / "js" / "views" / "worker-pool.js"
FORMAT_JS = ROOT / "src" / "web" / "static" / "js" / "ui" / "format.js"


def test_build_attempt_actions_use_constrained_endpoints():
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")

    assert "/worker/start`" in source
    assert "/api/build-attempts/queue/start" in source
    assert "启动全部待运行" in source
    assert "启动选中" in source
    assert "/api/build-attempts/worker/start-sequential" in source
    assert "/api/build-attempts/worker/start-sequential-lanes" in source
    assert "/api/build-attempts/worker/pools" in source
    assert "/api/build-attempts/worker/stop" in source
    assert "const LIST_LIMIT = 200;" in source
    assert 'params.set("limit", String(LIST_LIMIT));' in source
    assert "filterDraft" in source
    assert "captureFilterFocus(root)" in source
    assert "scheduleFilterApply()" in source
    assert "isListInteractionProtected(root)" in source
    assert "markFilterInteraction()" in source
    assert "启动多队列" in source
    assert "多队列执行池" not in source
    assert 'id="ba-lane-count"' in source
    assert "/revalidate`" in source
    assert 'runAction("worker")' not in source
    assert 'runAction("validate")' not in source
    assert 'id="ba-validate"' not in source
    assert 'id="ba-worker"' in source
    assert 'id="ba-stop-worker"' in source
    assert "结束运行中" in source
    assert "重新校验" in source
    assert "分析并修复" in source
    assert 'attempt.status === "failed"' in source
    assert "重试构建" in source
    assert 'item.reason && item.reason !== "missing_profile"' in source
    assert "Profile 配置未就绪" in source
    assert "干净重建" in source
    assert "/clean-rebuild`" in source
    assert "AI 修复记录" in source
    assert "renderRepairRuns(attempt.repair_runs || [])" in source
    assert "执行轮次" in source
    assert "renderExecutions(attempt.executions || [])" in source
    assert "result.iteration_no" in source
    assert "/worker/start`" in source[source.index("async function retryAttempt") :]
    assert "crypto.randomUUID()" in source
    assert "confirmed: true" in source
    assert '["failed", "lost", "succeeded"].includes(attempt.status)' in source
    assert "ba-detail-actions" in source
    assert "ba-card-list" in source


def test_worker_pool_shows_multilane_progress_and_beijing_time():
    source = WORKER_POOL_JS.read_text(encoding="utf-8")
    fmt = FORMAT_JS.read_text(encoding="utf-8")

    assert "实时进度" in source
    assert "/api/build-attempts?limit=" in source
    assert "/api/build-attempts/worker/pools" in source
    assert "normalizeAttempts" in source
    assert "design_task_id" in source
    assert "latest一次尝试" not in source
    assert 'timeZone: "Asia/Shanghai"' in fmt
    assert '"+08:00"' in fmt


def test_build_attempt_view_has_mobile_card_layout():
    index = (ROOT / "src" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    styles = BUILD_ATTEMPTS_CSS.read_text(encoding="utf-8")
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")

    assert '/css/views/build-attempts.css' in index
    assert "renderAttemptCards(rows)" in source
    assert ".ba-card-list" in styles
    assert "@media (max-width: 767px)" in styles
    assert ".ba-table-wrap" in styles
    assert "display: none;" in styles


def test_detail_poll_supports_append_only_event_updates():
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")

    assert "function patchDetailEvents(nextDetail)" in source
    assert 'insertAdjacentHTML("beforeend"' in source
    assert "#ba-progress-event-count" in source


def test_worker_start_and_detail_expose_effective_timeout():
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")

    assert "result.effective_timeout_seconds" in source
    assert "attempt.effective_timeout_seconds" in source
    assert "timeout_source" in source
    assert "Hermes 超时" in source


def test_sequential_aborted_result_is_rendered_as_not_failed():
    source = BUILD_ATTEMPTS_JS.read_text(encoding="utf-8")

    assert 'api("/api/ui-state")' in source
    assert "sequential_worker_result" in source
    assert "status === \"aborted\"" in source
    assert "已中止" in source
    assert "待重提" in source


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
