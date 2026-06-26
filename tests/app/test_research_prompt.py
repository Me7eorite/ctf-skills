"""Hermes Research Agent 提示词渲染测试。

验证 `prompts/research_prompt.md` 渲染后包含靠前的 category、持久化 seed URLs、
JSON 输出合同、source_indices 约束、category 范围规则，以及带当前 category 的示例。
测试覆盖 `web`、`re` 和动态 category `crypto`，证明提示词没有硬编码初始三类。
"""

from __future__ import annotations

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
        # 中文注释：category 必须出现在前 600 个字符内，而不是只藏在后面的 JSON 规范里。
        head = prompt[:600]
        self.assertIn(f"`{category}`", head)

        # 中文注释：确认提示词包含 JSON 输出合同的核心字段。
        self.assertIn("sources", prompt)
        self.assertIn("findings", prompt)
        self.assertIn("technique_family", prompt)
        self.assertIn("source_indices", prompt)

        # 中文注释：确认 source_indices 明确要求非空，并且使用 0-based 索引。
        self.assertRegex(prompt, r"length\s*≥\s*1|non-empty")
        self.assertIn("0-based", prompt)

        # 中文注释：确认提示词要求拒绝跨 category 材料。
        self.assertIn("Refuse cross-category material", prompt)

        # 中文注释：确认示例使用当前 category，并给出非空 source_indices。
        self.assertIn(f"Sample technique within {category}", prompt)
        self.assertRegex(prompt, r'"technique_family":\s*"other"')
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
        self.assertIn("`injection`", prompt)
        self.assertIn("`server_side`", prompt)

    def test_renders_seeded_re_category(self):
        request = _make_request("re", topic="GLIBC ROP gadget chains")
        prompt = render_research_prompt(request)

        self._common_assertions(prompt, "re")
        self.assertIn("GLIBC ROP gadget chains", prompt)
        self.assertIn("`vm_bytecode`", prompt)

    def test_renders_dynamic_category_not_hardcoded(self):
        # 中文注释：crypto 不在初始三类中，用它验证提示词不会硬编码 web/pwn/re。
        request = _make_request(
            "crypto",
            topic="Padding oracle on AES-CBC",
            seed_urls=("https://example.com/po-aes",),
        )
        prompt = render_research_prompt(request)

        self._common_assertions(prompt, "crypto")
        self.assertIn("Padding oracle on AES-CBC", prompt)
        self.assertIn("https://example.com/po-aes", prompt)

        # 中文注释：动态 category 的提示词里不应泄漏初始 category 代码。
        self.assertNotIn("`web`", prompt)
        self.assertNotIn("`pwn`", prompt)
        self.assertNotIn("`re`", prompt)
        self.assertIn("`other`", prompt)

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

        # 中文注释：运行约束应以 JSON 对象文本形式出现在提示词正文中。
        self.assertRegex(prompt, r'"runtime":\s*"node"')
        self.assertRegex(prompt, r'"framework":\s*"Express"')

    def test_search_keywords_render_as_search_plan_inputs(self):
        request = _make_request(
            "web",
            topic="JWT authentication bypass",
            runtime_constraints={
                "search_keywords": ["kid header traversal", "JWKS cache poisoning"],
            },
        )
        prompt = render_research_prompt(request)

        self.assertIn("Search keywords / key points", prompt)
        self.assertIn("- kid header traversal", prompt)
        self.assertIn("- JWKS cache poisoning", prompt)
        self.assertIn("Build search queries", prompt)
        self.assertIn("JWT authentication bypass", prompt)

    def test_generation_policy_renders_as_dedicated_section(self):
        request = _make_request(
            "re",
            topic="batch reversing constraints",
            runtime_constraints={
                "generation_policy": (
                    "XOR 类题最多 9 题。\n"
                    "solve.py 必须复现算法或从二进制中提取参数。\n"
                    "困难题必须包含两阶段以上变换。"
                )
            },
        )
        prompt = render_research_prompt(request)

        self.assertIn("## Generation policy", prompt)
        self.assertIn("XOR 类题最多 9 题", prompt)
        self.assertIn("solve.py 必须复现算法", prompt)
        self.assertIn("困难题必须包含两阶段以上变换", prompt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
