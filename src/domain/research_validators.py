"""Research 规划流程的领域层校验器。

这些函数强制检查无需访问数据库的结构性规则：
  - 难度分布的总和校验、标签白名单校验
  - 类别合法性校验
  - Finding 的 source 引用校验（不重复、至少一个）
需要跨行检查的规则（如 source_id 必须属于同一个 research_run）
位于 Repository 层，但抛出相同的 ResearchValidationError 异常以保持调用方一致。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from domain.design.technique_taxonomy import resolve_sub_technique
from domain.research import DIFFICULTY_LABELS, ResearchFindingKind

_LOGGER = logging.getLogger(__name__)


def _quality_ratio() -> float:
    """Fraction of ``target_count`` that must materialize as findings.

    GLM-5 and other lower-recall models often produce 4–5 findings when
    asked for 10. The legacy 0.5 default rejects those runs; operators
    can lower the ratio via ``RESEARCH_QUALITY_RATIO`` (e.g. ``0.3``)
    for GLM-5-first deployments without touching the validator.
    """
    raw = os.environ.get("RESEARCH_QUALITY_RATIO", "0.5")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "invalid RESEARCH_QUALITY_RATIO=%r; falling back to 0.5", raw
        )
        return 0.5
    if not 0.0 < value <= 1.0:
        _LOGGER.warning(
            "RESEARCH_QUALITY_RATIO=%r out of (0, 1] range; falling back to 0.5",
            raw,
        )
        return 0.5
    return value


def _quality_soft_pass_slack() -> int:
    """How many findings below `needed` the gate accepts with a warning.

    GLM-5 has run-to-run variance of ±2 findings on the same prompt;
    operators can set ``RESEARCH_QUALITY_SOFT_PASS_BELOW_BY=1`` so that
    a single missing finding logs a warning but does not waste the whole
    batch. Default ``0`` preserves strict behavior.
    """
    raw = os.environ.get("RESEARCH_QUALITY_SOFT_PASS_BELOW_BY", "0")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "invalid RESEARCH_QUALITY_SOFT_PASS_BELOW_BY=%r; falling back to 0",
            raw,
        )
        return 0
    if value < 0:
        _LOGGER.warning(
            "RESEARCH_QUALITY_SOFT_PASS_BELOW_BY=%r negative; falling back to 0",
            raw,
        )
        return 0
    return value


def _diversity_soft_pass_slack() -> int:
    """How many distinct sub-techniques below ``target_count`` the gate accepts.

    Mirrors ``RESEARCH_QUALITY_SOFT_PASS_BELOW_BY`` for the diversity floor:
    set ``RESEARCH_DIVERSITY_SOFT_PASS_BELOW_BY=1`` to tolerate one duplicate
    sub-technique with a warning instead of failing the whole run. Default
    ``0`` is strict (every task must get a distinct sub-technique).
    """
    raw = os.environ.get("RESEARCH_DIVERSITY_SOFT_PASS_BELOW_BY", "0")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "invalid RESEARCH_DIVERSITY_SOFT_PASS_BELOW_BY=%r; falling back to 0",
            raw,
        )
        return 0
    if value < 0:
        _LOGGER.warning(
            "RESEARCH_DIVERSITY_SOFT_PASS_BELOW_BY=%r negative; falling back to 0",
            raw,
        )
        return 0
    return value
class ResearchValidationError(ValueError):
    """领域校验器拒绝输入时抛出的异常。"""


URL_RE = re.compile(r"^https?://[^\s]+$")
CONTENT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_CONSTRAINT_KEYS = {
    "runtime",
    "framework",
    "language",
    "compiler",
    "target_format",
    "architecture",
    "port",
    "mitigations",
    "target_platform",
    "strip",
    "search_keywords",
    "generation_policy",
}
TARGET_FORMATS = {"elf", "exe", "wasm", "jar", "container"}
TARGET_PLATFORMS = {"linux/amd64", "linux/arm64", "linux/arm", "windows/amd64"}


def validate_distribution(
    target_count: int, distribution: Mapping[str, int]
) -> None:
    """校验难度分布是否合法。

    规则:
      - target_count 必须 > 0
      - distribution 不能为空
      - 所有难度标签必须在 DIFFICULTY_LABELS 白名单内
      - 所有数量必须 >= 0
      - 总数量必须等于 target_count
    """
    if target_count <= 0:
        raise ResearchValidationError(
            f"target_count must be positive, got {target_count}"
        )
    if not distribution:
        raise ResearchValidationError(
            "difficulty_distribution is empty; "
            f"expected sum to equal target_count={target_count}"
        )
    # 检查未知难度标签
    unknown = sorted(label for label in distribution if label not in DIFFICULTY_LABELS)
    if unknown:
        raise ResearchValidationError(
            f"unknown difficulty label(s) {unknown}; "
            f"allowed: {list(DIFFICULTY_LABELS)}"
        )
    # 检查负数值
    negatives = sorted(label for label, count in distribution.items() if count < 0)
    if negatives:
        raise ResearchValidationError(
            f"difficulty counts must be non-negative; negative for: {negatives}"
        )
    # 检查总数量
    total = sum(distribution.values())
    if total != target_count:
        raise ResearchValidationError(
            f"difficulty_distribution sums to {total} but target_count is {target_count}"
        )


def validate_runtime_constraints(payload: Any) -> dict[str, Any]:
    """Validate operator-supplied runtime constraints shared by HTTP and CLI."""
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ResearchValidationError("runtime_constraints must be an object")
    result: dict[str, Any] = {}
    for raw_key, value in payload.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise ResearchValidationError("runtime_constraints keys must be non-empty strings")
        key = raw_key
        if key.startswith("experimental."):
            if not isinstance(value, str):
                raise ResearchValidationError(
                    f"runtime_constraints[{key!r}] must be a string"
                )
            result[key] = value
            continue
        if key not in RUNTIME_CONSTRAINT_KEYS:
            allowed = sorted(RUNTIME_CONSTRAINT_KEYS) + ["experimental.*"]
            raise ResearchValidationError(
                f"unknown runtime_constraints key {key!r}; allowed: {allowed}"
            )
        result[key] = _validate_runtime_constraint_value(key, value)
    return result


def _validate_runtime_constraint_value(key: str, value: Any) -> Any:
    if key in {"runtime", "framework", "language", "compiler", "architecture"}:
        if not isinstance(value, str):
            raise ResearchValidationError(f"runtime_constraints[{key!r}] must be a string")
        return value
    if key == "target_format":
        if not isinstance(value, str) or value not in TARGET_FORMATS:
            raise ResearchValidationError(
                f"runtime_constraints['target_format'] must be one of {sorted(TARGET_FORMATS)}"
            )
        return value
    if key == "target_platform":
        if not isinstance(value, str) or value not in TARGET_PLATFORMS:
            raise ResearchValidationError(
                f"runtime_constraints['target_platform'] must be one of {sorted(TARGET_PLATFORMS)}"
            )
        return value
    if key == "port":
        if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 65535):
            raise ResearchValidationError("runtime_constraints['port'] must be an integer 1..65535")
        return value
    if key == "mitigations":
        if not isinstance(value, Mapping) or not all(
            isinstance(k, str) and isinstance(v, bool) for k, v in value.items()
        ):
            raise ResearchValidationError(
                "runtime_constraints['mitigations'] must be an object mapping string to bool"
            )
        return dict(value)
    if key == "strip":
        if not isinstance(value, bool):
            raise ResearchValidationError("runtime_constraints['strip'] must be a bool")
        return value
    if key == "search_keywords":
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",")]
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            items = [str(item).strip() for item in value]
        else:
            raise ResearchValidationError(
                "runtime_constraints['search_keywords'] must be a string or array"
            )
        keywords = [item for item in items if item]
        if not keywords:
            raise ResearchValidationError(
                "runtime_constraints['search_keywords'] must contain at least one keyword"
            )
        return keywords
    if key == "generation_policy":
        if not isinstance(value, str):
            raise ResearchValidationError(
                "runtime_constraints['generation_policy'] must be a string"
            )
        policy = value.strip()
        if not policy:
            raise ResearchValidationError(
                "runtime_constraints['generation_policy'] must not be empty"
            )
        return policy
    raise ResearchValidationError(f"unknown runtime_constraints key {key!r}")


def extract_terminal_json_object(stdout: str) -> dict[str, Any] | None:
    """Extract the last parseable top-level JSON object from noisy stdout."""
    for end in range(len(stdout) - 1, -1, -1):
        if stdout[end] != "}":
            continue
        start = _matching_object_start(stdout, end)
        if start is None:
            continue
        try:
            parsed = json.loads(stdout[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _matching_object_start(text: str, end: int) -> int | None:
    depth = 0
    in_string: str | None = None
    escaped = False
    for index in range(end, -1, -1):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = None
            continue
        if char in {'"', "'"}:
            in_string = char
            continue
        if char == "}":
            depth += 1
        elif char == "{":
            depth -= 1
            if depth == 0:
                return index
    return None


def apply_research_quality_gate(
    parsed: Mapping[str, Any],
    target_count: int,
) -> tuple[bool, str | None]:
    sources = parsed.get("sources")
    findings = parsed.get("findings")
    if not isinstance(sources, list):
        return False, "unparseable_output:sources_not_list"
    if not isinstance(findings, list):
        return False, "unparseable_output:findings_not_list"

    seen_hashes: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            return False, "unparseable_output:source_not_object"
        url = source.get("url")
        if not isinstance(url, str) or not URL_RE.match(url) or not urlparse(url).hostname:
            return False, f"url_shape_invalid:{url}"
        content_hash = source.get("content_hash")
        if not isinstance(content_hash, str) or not CONTENT_HASH_RE.match(content_hash):
            return False, f"content_hash_shape_invalid:{content_hash}"
        if content_hash in seen_hashes:
            return False, f"content_hash_dup:{content_hash}"
        seen_hashes.add(content_hash)

    needed = max(1, math.ceil(target_count * _quality_ratio()))
    slack = _quality_soft_pass_slack()
    soft_floor = max(1, needed - slack)
    got = len(findings)
    if got < soft_floor:
        return False, f"insufficient_findings:got={got},need={needed}"
    if got < needed:
        _LOGGER.warning(
            "research quality gate soft-passed: got=%d, needed=%d, slack=%d "
            "(set RESEARCH_QUALITY_SOFT_PASS_BELOW_BY=0 to disable soft pass)",
            got,
            needed,
            slack,
        )

    # Diversity floor: the planner can only diversify within the findings pool,
    # so research must supply at least ``target_count`` distinct sub-techniques
    # or duplicate 考点 become unavoidable downstream. Counts distinct canonical
    # sub-techniques and applies the same soft-pass pattern as findings count.
    distinct = len({resolve_sub_technique(finding) for finding in findings})
    diversity_needed = max(1, target_count)
    diversity_slack = _diversity_soft_pass_slack()
    diversity_floor = max(1, diversity_needed - diversity_slack)
    if distinct < diversity_floor:
        return (
            False,
            f"insufficient_diversity:distinct={distinct},need={diversity_needed}",
        )
    if distinct < diversity_needed:
        _LOGGER.warning(
            "research diversity gate soft-passed: distinct=%d, needed=%d, slack=%d "
            "(set RESEARCH_DIVERSITY_SOFT_PASS_BELOW_BY=0 to disable soft pass)",
            distinct,
            diversity_needed,
            diversity_slack,
        )
    return True, None


def validate_category(category: str | None, allowed_codes: Iterable[str]) -> None:
    """校验题目类别是否合法。

    参数:
        category: 待校验的类别字符串
        allowed_codes: 允许的类别代码集合（由调用方从数据库查询）

    设计说明:
        允许的类别不是 Python 常量，而是从数据库的 challenge_categories 表查询的。
        这样可以在不修改代码的情况下增删类别。
    """
    if not category:
        raise ResearchValidationError("category is required; got missing/empty value")
    allowed = set(allowed_codes)
    if category not in allowed:
        raise ResearchValidationError(
            f"category {category!r} is not allowed; "
            f"allowed: {sorted(allowed)}"
        )


def validate_finding(kind: str, source_ids: Sequence[UUID]) -> None:
    """校验 Research Finding 的数据合法性。

    规则:
      - kind 必须在 ResearchFindingKind 白名单内
      - 至少引用一个 source
      - 不允许重复引用同一个 source

    注意:
        source_id 必须属于同一个 research_run_id 的跨行检查不在本函数中，
        由 Repository 层负责（因为需要数据库查询）。
    """
    if kind not in ResearchFindingKind:
        raise ResearchValidationError(
            f"finding kind {kind!r} is not allowed; "
            f"allowed: {list(ResearchFindingKind)}"
        )
    if not source_ids:
        raise ResearchValidationError(
            "finding must reference at least one source (source_ids is empty)"
        )
    # 检查重复引用
    seen: set[UUID] = set()
    duplicates: list[UUID] = []
    for sid in source_ids:
        if sid in seen and sid not in duplicates:
            duplicates.append(sid)
        seen.add(sid)
    if duplicates:
        raise ResearchValidationError(
            f"finding source_ids contain duplicate(s): {duplicates}"
        )
