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


def test_api_error_object_preserves_machine_readable_code() -> None:
    source = (STATIC / "js" / "api.js").read_text(encoding="utf-8")

    assert 'typeof detail === "object"' in source
    assert "detail.code" in source
