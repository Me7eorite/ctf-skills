"""Static contracts for the resource-deletion browser interaction."""

from __future__ import annotations

from pathlib import Path

STATIC_JS = Path(__file__).parents[2] / "src" / "web" / "static" / "js"


def test_confirmation_is_singleton_and_keyboard_accessible() -> None:
    source = (STATIC_JS / "ui" / "delete-dialog.js").read_text()

    assert "if (activeConfirmation) return activeConfirmation" in source
    assert 'event.key === "Escape"' in source
    assert 'event.key !== "Tab"' in source
    assert "previousFocus?.isConnected" in source


def test_deletion_views_guard_submission_and_polling() -> None:
    for relative in (
        "views/research-requests.js",
        "views/design-tasks.js",
        "views/build-attempts.js",
    ):
        source = (STATIC_JS / relative).read_text()
        assert "if (state.flags.deleting) return" in source
        assert "state.flags.deleting = true" in source
        assert "state.flags.deleting = false" in source
        assert source.count("if (state.flags.deleting)") >= 2
        assert "if (choice === null) return" in source
        assert 'choice ? "?delete_artifacts=true" : "?delete_artifacts=false"' in source
        assert "await del(" in source


def test_each_view_exposes_list_and_detail_delete_navigation_contract() -> None:
    research = (STATIC_JS / "views" / "research-requests.js").read_text()
    design = (STATIC_JS / "views" / "design-tasks.js").read_text()
    build = (STATIC_JS / "views" / "build-attempts.js").read_text()

    assert research.count("req-delete") >= 2
    assert research.count("detail-delete-request") >= 2
    assert "state.detailId = null" in research
    assert "await ensureRequests()" in research
    assert design.count("dt-delete") >= 4
    assert "state.detailId = null" in design
    assert "await ensureList()" in design
    assert build.count("ba-delete") >= 3
    assert 'window.location.hash = "#/build-attempts"' in build
