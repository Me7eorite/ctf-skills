"""Validate local references and repository shape for CTF design materials."""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "skills"
SKILL_DIRS = sorted(p.parent for p in SKILL_ROOT.glob("*/SKILL.md"))


def _slugify_heading(heading: str) -> str:
    slug = heading.lower().strip()
    slug = re.sub(r"[*`~]", "", slug)
    slug = re.sub(r"<[^>]+>", "", slug)
    slug = re.sub(r"[^\w\s-]", "", slug)
    return slug.replace(" ", "-")


def _strip_fenced_code(text: str) -> str:
    out_lines = []
    in_fence = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out_lines.append("")
            continue
        out_lines.append("" if in_fence else line)
    return "\n".join(out_lines)


def _strip_all_code(text: str) -> str:
    text = _strip_fenced_code(text)
    return re.sub(r"`[^`]*`", "", text)


def _extract_headings(text: str) -> set[str]:
    headings = set()
    for match in re.finditer(r"^#{1,6}\s+(.+)$", _strip_fenced_code(text), re.MULTILINE):
        headings.add(_slugify_heading(match.group(1)))
    return headings


def _extract_local_md_links(text: str) -> list[tuple[str, str | None]]:
    text = _strip_all_code(text)
    links = []
    for match in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", text):
        target = match.group(2)
        if target.startswith(("http://", "https://", "mailto:", "/")):
            continue
        if "#" in target:
            file_part, anchor = target.split("#", 1)
        else:
            file_part, anchor = target, None
        if file_part and file_part.endswith(".md"):
            links.append((file_part, anchor))
    return links


class TestSkillDirectoryShape(unittest.TestCase):
    """The repository should keep design skill plus ctf-* material catalogs."""

    def test_design_skill_and_material_catalogs_remain(self):
        names = {p.name for p in SKILL_DIRS}
        self.assertIn("design-challenges", names)
        self.assertIn("ctf-web", names)
        self.assertIn("ctf-pwn", names)
        self.assertIn("ctf-reverse", names)
        self.assertNotIn("solve-challenge", names)


class TestLocalMarkdownLinks(unittest.TestCase):
    """Markdown links to local files should resolve."""

    def test_local_links_resolve(self):
        for skill_dir in SKILL_DIRS:
            for md_file in [skill_dir / "SKILL.md", *skill_dir.glob("references/*.md")]:
                text = md_file.read_text(encoding="utf-8")
                for file_part, _anchor in _extract_local_md_links(text):
                    target_path = md_file.parent / file_part
                    with self.subTest(source=md_file.name, link=file_part):
                        self.assertTrue(
                            target_path.exists(),
                            f"{md_file} links to {file_part}, which does not exist",
                        )


class TestAnchorLinks(unittest.TestCase):
    """Anchor links should resolve to actual headings."""

    def test_anchors_resolve(self):
        for skill_dir in SKILL_DIRS:
            for md_file in [skill_dir / "SKILL.md", *skill_dir.glob("references/*.md")]:
                text = md_file.read_text(encoding="utf-8")
                for file_part, anchor in _extract_local_md_links(text):
                    if anchor is None:
                        continue
                    target_path = md_file.parent / file_part
                    if not target_path.exists():
                        continue
                    target_headings = _extract_headings(target_path.read_text(encoding="utf-8"))
                    with self.subTest(source=md_file.name, anchor=anchor):
                        self.assertIn(anchor, target_headings)


if __name__ == "__main__":
    unittest.main()
