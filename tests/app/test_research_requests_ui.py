"""Static UI contracts for the Chinese research request management flow."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "web" / "static"


def test_request_list_uses_operator_facing_display_status() -> None:
    source = (STATIC / "js" / "views" / "research-requests.js").read_text(
        encoding="utf-8"
    )

    assert 'p.set("display_status", state.filter.displayStatus)' in source
    assert 'p.set("status", state.filter' not in source
    assert 'const REQUEST_STATUSES = ["draft", "queued"' in source
    assert "研究需求" in source
    assert "执行状态" in source
    assert "重置筛选" in source
    assert 'id="req-refresh"' in source
    assert "forceReloadRequests().finally(initIcons)" in source
    assert "刷新" in source


def test_request_detail_exposes_quality_gate_and_runtime_constraints() -> None:
    source = (STATIC / "js" / "views" / "research-requests.js").read_text(
        encoding="utf-8"
    )

    assert "request.minimum_findings" in source
    assert 'request.runtime_constraints || {}' in source
    assert "有效研究结论" in source
    assert "最低要求" in source
    assert "补充研究" in source
    assert "可继续补充研究" in source
    assert "生成设计任务" in source
    assert "暂停 Worker 可能影响正在执行的其他研究需求" in source
    assert 'if (latest && (latest.status === "queued" || latest.status === "running"))' in source
    assert "补充研究" in source
    assert "启动研究" in source


def test_request_detail_prompts_to_start_when_latest_run_is_queued_but_worker_is_idle() -> None:
    source = (STATIC / "js" / "views" / "research-requests.js").read_text(
        encoding="utf-8"
    )

    assert 'if (latest && (latest.status === "queued" || latest.status === "running"))' in source
    assert 'if (workerRunning) {' in source
    assert 'return `<button class="btn btn-primary detail-open-runs"><i data-lucide="activity"></i> 查看运行状态</button>`;' in source
    assert 'return `<button class="btn btn-primary" id="detail-run-loop"${!available ? " disabled" : ""}><i data-lucide="rotate-cw"></i> 持续处理该需求</button>`;' in source
    assert 'if (request.status === "researched") {' in source
    assert '研究已完成，仍可继续补充研究；设计任务生成基于最新 completed research run。' in source


def test_research_submit_supports_search_keywords() -> None:
    source = (STATIC / "js" / "views" / "research-submit.js").read_text(
        encoding="utf-8"
    )

    assert "form-search-keywords" in source
    assert "search_keywords" in source
    assert "runtimeConstraints.search_keywords = searchKeywords" in source
    assert "话题 + 关键字" in source
    assert "考点关键字" in source


def test_research_submit_supports_generation_policy() -> None:
    source = (STATIC / "js" / "views" / "research-submit.js").read_text(
        encoding="utf-8"
    )

    assert "form-generation-policy" in source
    assert "DEFAULT_RE_POLICY" in source
    assert "runtimeConstraints.generation_policy = f.generation_policy.trim()" in source
    assert "填入 Re 策略模板" in source
    assert "XOR 类题最多 9 题" in source
    assert "solve.py 必须复现算法" in source
    assert "生成策略" in source


def test_research_submit_preserves_form_editing_during_refresh() -> None:
    source = (STATIC / "js" / "views" / "research-submit.js").read_text(
        encoding="utf-8"
    )

    assert "shouldDeferFormRender(root)" in source
    assert "isFormInteractionProtected(root)" in source
    assert "markFormInteraction()" in source
    assert "form-seed-urls" in source


def test_request_detail_header_is_a_state_aware_summary_card() -> None:
    source = (STATIC / "js" / "views" / "research-requests.js").read_text(
        encoding="utf-8"
    )
    styles = (STATIC / "css" / "views" / "research-requests.css").read_text(
        encoding="utf-8"
    )

    assert 'class="rq-hero rq-hero-${heroState.tone}"' in source
    assert "requestHeroState(displayStatus, qualityPassed)" in source
    assert "研究结果已就绪" in source
    assert "研究执行失败" in source
    assert "renderDifficultySummary(request.difficulty_distribution)" in source
    assert "formatShortDate(request.created_at)" in source
    assert ".rq-hero-success" in styles
    assert ".rq-hero-danger" in styles
    assert ".rq-hero-actions" in styles
    assert "renderHeroCategory(request.category)" in source
    for category in (".rq-category-web", ".rq-category-pwn", ".rq-category-re"):
        assert category in styles
    for status in (
        ".rq-status-queued",
        ".rq-status-researching",
        ".rq-status-researched",
        ".rq-status-failed",
    ):
        assert status in styles


def test_research_status_and_diagnostics_are_chinese() -> None:
    source = (STATIC / "js" / "ui" / "format.js").read_text(encoding="utf-8")

    for label in ("草稿", "等待研究", "研究中", "研究完成", "研究失败"):
        assert label in source
    for diagnostic in (
        "未绑定研究 Agent 配置",
        "研究 Worker 启动失败",
        "有效研究结论不足",
        "研究来源存在重复内容",
    ):
        assert diagnostic in source


def test_research_failure_alert_uses_api_classification_fields() -> None:
    source = (STATIC / "js" / "views" / "research-requests.js").read_text(
        encoding="utf-8"
    )
    format_source = (STATIC / "js" / "ui" / "format.js").read_text(encoding="utf-8")
    styles = (STATIC / "css" / "views" / "research-requests.css").read_text(
        encoding="utf-8"
    )

    assert "failureMeta(run.last_error_category)" in source
    assert "run.last_error_title" in source
    assert "run.last_error_description" in source
    assert "run.last_error_actions" in source
    assert "<details class=\"rq-alert-details\">" in source
    assert "researchErrorMessage(latest.last_error)" not in source
    assert "export function failureMeta(category)" in format_source
    for category in ("timeout", "lease_expired", "parse_failure", "quality_gate"):
        assert category in format_source
    for selector in (
        ".rq-alert-actions ul",
        ".rq-alert-details summary",
        ".rq-history-failure-col",
    ):
        assert selector in styles


def test_research_run_history_shows_failure_reason_column() -> None:
    source = (STATIC / "js" / "views" / "research-requests.js").read_text(
        encoding="utf-8"
    )

    assert "失败原因" in source
    assert "run.last_error_title" in source
    assert 'run.status === "failed"' in source


def test_api_error_object_preserves_machine_readable_code() -> None:
    source = (STATIC / "js" / "api.js").read_text(encoding="utf-8")

    assert "inflightGetRequests" in source
    assert "inflightGetRequests.has(dedupeKey)" in source
    assert "inflightGetRequests.delete(dedupeKey)" in source
    assert 'typeof detail === "object"' in source
    assert "detail.code" in source


def test_shell_uses_local_system_fonts_without_external_font_requests() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    tokens = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")

    assert "fonts.googleapis.com" not in index
    assert "fonts.gstatic.com" not in index
    assert "--font-sans: ui-sans-serif" in tokens


def test_research_backfill_ui_contract() -> None:
    source = (STATIC / "js" / "views" / "research-requests.js").read_text(
        encoding="utf-8"
    )
    dialog_source = (STATIC / "js" / "ui" / "backfill-dialog.js").read_text(
        encoding="utf-8"
    )
    styles = (STATIC / "css" / "views" / "research-requests.css").read_text(
        encoding="utf-8"
    )

    assert 'import { confirmBackfill } from "../ui/backfill-dialog.js"' in source
    assert "async function requestBackfillPreview" in source
    assert "async function requestBackfillApply" in source
    assert "expected_log_sha256: preview.log_sha256" in source
    assert "payload.code" in source
    assert "preview_stale" in source
    assert "confirmBackfill({ preview: null, error: err })" in source
    assert "run.recoverable === true" in source
    assert "尝试从日志恢复结果" in source
    assert ".rq-backfill-run" in source
    assert "postJson(`/api/research/runs/${encodeURIComponent(runId)}/backfill`" not in source

    assert "export function confirmBackfill" in dialog_source
    assert "error = null" in dialog_source
    assert "预览失败" in dialog_source
    assert 'hasError ? " disabled" : ""' in dialog_source
    for field in (
        "preview?.log_path",
        "preview?.log_sha256",
        "preview?.would_insert_sources",
        "preview?.would_insert_findings",
        "preview?.current_run_status",
        "preview?.would_run_status",
        "preview?.current_request_status",
        "preview?.would_request_status",
    ):
        assert field in dialog_source
    assert "候选不保证恢复一定成功" in dialog_source
    assert "确认恢复" in dialog_source
    assert ".rq-alert-backfill" in styles
