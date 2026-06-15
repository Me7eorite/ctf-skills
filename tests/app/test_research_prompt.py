"""Render-time tests for the Hermes Research Agent prompt.

Asserts that `prompts/research_prompt.md` is rendered with the category
declared up front, the persisted seed URLs, the JSON output contract, the
source-index constraint phrase, the category-scope rule, and a worked
example that names the rendered category. Parametrized over `web`, `re`,
and a fresh dynamic category `crypto` to prove the prompt does NOT
hardcode the seeded trio.
"""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timezone
from types import MappingProxyType
from uuid import uuid4

from domain.research import GenerationRequest
from hermes.prompt import render_research_prompt


def _make_request(
    category: str,
    *,
    topic: str = "SQL injection bypass",
    target_count: int = 5,
    difficulty_distribution=None,
    seed_urls=(),
    runtime_constraints=None,
) -> GenerationRequest:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return GenerationRequest(
        id=uuid4(),
        category=category,
        topic=topic,
        target_count=target_count,
        difficulty_distribution=MappingProxyType(
            dict(difficulty_distribution or {"easy": 5})
        ),
        runtime_constraints=MappingProxyType(dict(runtime_constraints or {})),
        seed_urls=tuple(seed_urls),
        max_attempts=3,
        status="draft",
        created_at=now,
        updated_at=now,
    )


class RenderResearchPromptTests(unittest.TestCase):
    def _common_assertions(self, prompt: str, category: str) -> None:
        # Category must appear prominently — within the first ~600 chars
        # (the "Category" section near the top), not only inside the JSON
        # spec further down.
        head = prompt[:600]
        self.assertIn(f"`{category}`", head)

        # JSON output contract terms
        self.assertIn("sources", prompt)
        self.assertIn("findings", prompt)
        self.assertIn("source_indices", prompt)

        # Source-index constraint phrase: each integer is a 0-based index
        # into sources[] and the list must be non-empty.
        self.assertRegex(prompt, r"length\s*≥\s*1|non-empty")
        self.assertIn("0-based", prompt)

        # Category-scope rule
        self.assertIn("Refuse cross-category material", prompt)

        # Worked example mentions the rendered category and a non-empty
        # source_indices.
        self.assertIn(f"Sample technique within {category}", prompt)
        self.assertRegex(prompt, r'"source_indices":\s*\[\s*0\s*\]')

    def test_renders_seeded_web_category(self):
        request = _make_request(
            "web",
            seed_urls=("https://owasp.org/Top10/", "https://portswigger.net/web-security"),
            difficulty_distribution={"easy": 2, "medium": 2, "hard": 1},
        )
        prompt = render_research_prompt(request)

        self._common_assertions(prompt, "web")
        self.assertIn("https://owasp.org/Top10/", prompt)
        self.assertIn("https://portswigger.net/web-security", prompt)
        self.assertIn("easy=2", prompt)
        self.assertIn("medium=2", prompt)
        self.assertIn("hard=1", prompt)

    def test_renders_seeded_re_category(self):
        request = _make_request("re", topic="GLIBC ROP gadget chains")
        prompt = render_research_prompt(request)

        self._common_assertions(prompt, "re")
        self.assertIn("GLIBC ROP gadget chains", prompt)

    def test_renders_dynamic_category_not_hardcoded(self):
        # `crypto` is NOT in the seeded trio (web/pwn/re). If the prompt
        # were hardcoded against that trio, this rendering would either
        # crash, miss the category, or substitute a wrong worked example.
        request = _make_request(
            "crypto",
            topic="Padding oracle on AES-CBC",
            seed_urls=("https://example.com/po-aes",),
        )
        prompt = render_research_prompt(request)

        self._common_assertions(prompt, "crypto")
        self.assertIn("Padding oracle on AES-CBC", prompt)
        self.assertIn("https://example.com/po-aes", prompt)

        # And no leakage of the seeded category codes into this prompt.
        self.assertNotIn("`web`", prompt)
        self.assertNotIn("`pwn`", prompt)
        self.assertNotIn("`re`", prompt)

    def test_empty_seed_urls_renders_placeholder(self):
        request = _make_request("web", seed_urls=())
        prompt = render_research_prompt(request)

        self.assertIn("no seed URLs provided", prompt)

    def test_runtime_constraints_serialized_as_json(self):
        request = _make_request(
            "web",
            runtime_constraints={"runtime": "node", "framework": "Express"},
        )
        prompt = render_research_prompt(request)

        # JSON object literal appears somewhere in the prompt body.
        self.assertRegex(prompt, r'"runtime":\s*"node"')
        self.assertRegex(prompt, r'"framework":\s*"Express"')


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
