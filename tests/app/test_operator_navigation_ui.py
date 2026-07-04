"""Static contracts for the operator navigation shell."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "web" / "static"


def test_navigation_uses_completed_challenges_and_hides_seed_library() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    router = (STATIC / "js" / "router.js").read_text(encoding="utf-8")
    main = (STATIC / "js" / "main.js").read_text(encoding="utf-8")

    assert "完成题目" in index
    assert "完成题目" in router
    assert "题目库" not in index
    assert "种子库" not in index
    assert 'data-target="seeds"' not in index
    assert 'data-view="seeds"' not in index
    assert './views/seeds.js' not in main
    assert "seeds.render" not in main
    assert not (STATIC / "js" / "views" / "seeds.js").exists()
