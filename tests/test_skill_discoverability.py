"""Validate that the challenge-design skill is discoverable."""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / "skills" / "design-challenges" / "SKILL.md"


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return None

    result: dict[str, str] = {}
    current_block: str | None = None
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(":") and ":" not in stripped[:-1]:
            current_block = stripped[:-1]
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip('"')
        if current_block:
            result[f"{current_block}.{key}"] = value
        else:
            result[key] = value
    return result


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./+-]+", text.lower()))


class TestDesignChallengeDiscoverability(unittest.TestCase):
    """The design skill should match realistic authoring prompts."""

    @classmethod
    def setUpClass(cls):
        text = SKILL_PATH.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        if fm is None:
            raise AssertionError("skills/design-challenges/SKILL.md has no frontmatter")
        cls.description = fm["description"]
        cls.description_tokens = _tokens(cls.description)

    def test_description_names_core_authoring_outputs(self):
        expected_terms = {
            "designs",
            "challenge",
            "specs",
            "author",
            "tickets",
            "ctfd",
            "batch-generation",
            "workflows",
        }
        self.assertTrue(
            expected_terms <= self.description_tokens,
            f"Missing discoverability terms: {expected_terms - self.description_tokens}",
        )

    def test_description_names_primary_categories(self):
        for term in {"web", "pwn", "reverse"}:
            with self.subTest(term=term):
                self.assertIn(term, self.description_tokens)

    def test_description_excludes_solve_first_positioning(self):
        description = self.description.lower()
        self.assertNotIn("use when you already have a vulnerable native target", description)
        self.assertNotIn("use when the target is primarily an http application", description)
        self.assertNotIn("use when the main job is to understand", description)


if __name__ == "__main__":
    unittest.main()
