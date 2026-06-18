"""Research 规划流程的领域层校验器。

这些函数强制检查无需访问数据库的结构性规则：
  - 难度分布的总和校验、标签白名单校验
  - 类别合法性校验
  - Finding 的 source 引用校验（不重复、至少一个）
需要跨行检查的规则（如 source_id 必须属于同一个 research_run）
位于 Repository 层，但抛出相同的 ResearchValidationError 异常以保持调用方一致。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from domain.research import DIFFICULTY_LABELS, ResearchFindingKind


class ResearchValidationError(ValueError):
    """领域校验器拒绝输入时抛出的异常。"""


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
