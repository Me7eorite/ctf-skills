"""Static contracts for the Chinese design-task workspace."""

from __future__ import annotations

from pathlib import Path


STATIC = Path(__file__).parents[2] / "src" / "web" / "static"


def test_design_task_view_uses_chinese_operator_copy() -> None:
    source = (STATIC / "js" / "views" / "design-tasks.js").read_text(
        encoding="utf-8"
    )

    for label in (
        "题目设计",
        "待设计",
        "可构建",
        "提交设计",
        "开始设计",
        "开始构建",
        "研究依据",
        "最新设计方案",
        "设计记录",
    ):
        assert label in source
    assert "Loading design tasks" not in source
    assert "Build selected" not in source
    assert "Quality passed" not in source


def test_design_task_statuses_have_chinese_stage_mapping() -> None:
    source = (STATIC / "js" / "ui" / "format.js").read_text(encoding="utf-8")

    for label in (
        "草稿",
        "等待设计",
        "设计中",
        "设计完成",
        "设计失败",
        "构建中",
        "构建完成",
        "构建失败",
    ):
        assert label in source
    assert "designTaskStage" in source
    assert "designTaskStatusPill" in source


def test_design_task_styles_are_isolated_from_research_view() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    design_css = (STATIC / "css" / "views" / "design-tasks.css").read_text(
        encoding="utf-8"
    )
    research_css = (
        STATIC / "css" / "views" / "research-requests.css"
    ).read_text(encoding="utf-8")

    assert '/css/views/design-tasks.css' in index
    assert ".dt-summary-grid" in design_css
    assert ".dt-detail-layout" in design_css
    assert ".design-json" in design_css
    assert ".design-task-actions" not in research_css


def test_list_exposes_context_and_state_driven_primary_actions() -> None:
    source = (STATIC / "js" / "views" / "design-tasks.js").read_text(
        encoding="utf-8"
    )

    assert "dt-context-banner" in source
    assert "renderTaskProgress(task)" in source
    assert "renderDetailPrimaryAction" in source
    assert "dt-bulk-bar" in source
    assert "开发者数据 · 原始 Payload" in source


def test_bulk_build_exposes_enqueue_and_ordered_execution_modes() -> None:
    source = (STATIC / "js" / "views" / "design-tasks.js").read_text(
        encoding="utf-8"
    )

    assert "仅加入队列" in source
    assert "按勾选顺序构建" in source
    assert "顺序模式同一时间只运行一个" in source
    assert "/api/build-attempts/worker/start-sequential" in source
    assert "build_attempt_ids: result.build_attempt_ids" in source
