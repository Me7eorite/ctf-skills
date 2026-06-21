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

    assert "Math.ceil(Number(request.target_count || 0) * 0.5)" in source
    assert 'request.runtime_constraints || {}' in source
    assert "有效研究结论" in source
    assert "最低要求" in source
    assert "生成设计任务" in source
    assert "暂停 Worker 可能影响正在执行的其他研究需求" in source


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

    assert 'typeof detail === "object"' in source
    assert "detail.code" in source


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
