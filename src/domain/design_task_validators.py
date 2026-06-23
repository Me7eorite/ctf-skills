"""设计任务规划流程的领域校验器。

这些校验规则与 SeedStore.validate_seed 的规则一一对应，
确保从数据库创建的设计任务与从文件系统创建的种子分片格式一致。
需要跨行检查的规则（如 finding 是否属于同一个 research_run）由规划服务处理。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from domain.design_tasks import DesignTaskStatus
from domain.research import DIFFICULTY_LABELS

# 必须为非空字符串的字段
REQUIRED_TEXT_FIELDS: tuple[str, ...] = (
    "challenge_id",       # 题目 ID
    "title",              # 标题
    "category",           # 类别
    "difficulty",         # 难度
    "primary_technique",  # 核心技术
    "learning_objective", # 学习目标
)


class DesignTaskValidationError(ValueError):
    """设计任务候选数据或状态转换不合法时抛出。"""


def validate_candidate(
    candidate: Mapping[str, Any],
    *,
    parent_category: str,
    task_no: int,
) -> None:
    """校验单个设计任务候选数据是否与种子分片格式兼容。

    检查项:
      - 所有必填文本字段不能为空
      - category 必须等于父请求的 category
      - challenge_id 必须以 "{category}-" 开头
      - difficulty 必须在 DIFFICULTY_LABELS 白名单内
      - points 必须是正整数
      - web/pwn 任务必须指定有效端口
      - task_no 必须与预期的序号一致
    """
    # 1. 必填字段非空检查
    for field in REQUIRED_TEXT_FIELDS:
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise DesignTaskValidationError(
                f"task field {field!r} must be a non-empty string"
            )

    # 2. 类别必须与父请求一致
    category = candidate["category"]
    if category != parent_category:
        raise DesignTaskValidationError(
            f"task category {category!r} does not match parent request "
            f"category {parent_category!r}"
        )

    # 3. challenge_id 前缀检查（如 "web-0001" 必须以 "web-" 开头）
    challenge_id = candidate["challenge_id"]
    if not challenge_id.startswith(f"{category}-"):
        raise DesignTaskValidationError(
            f"challenge_id {challenge_id!r} prefix must match category "
            f"{category!r}"
        )

    # 4. 难度白名单检查
    difficulty = candidate["difficulty"]
    if difficulty not in DIFFICULTY_LABELS:
        raise DesignTaskValidationError(
            f"difficulty {difficulty!r} is not allowed; "
            f"allowed: {list(DIFFICULTY_LABELS)}"
        )

    # 5. 分值检查（正整数，不能是布尔型）
    points = candidate.get("points")
    if not isinstance(points, int) or isinstance(points, bool) or points <= 0:
        raise DesignTaskValidationError("points must be a positive integer")

    # 6. 端口检查
    port = candidate.get("port")
    if category in {"web", "pwn"}:
        # web/pwn 必须指定有效端口
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise DesignTaskValidationError(
                "port must be a valid TCP port (1-65535) for web/pwn tasks"
            )
    elif port is not None and (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        # 非 web/pwn 类别如果指定了端口，也必须是有效值
        raise DesignTaskValidationError(
            "port must be null or a valid TCP port (1-65535)"
        )

    # 7. 任务序号检查
    candidate_task_no = candidate.get("task_no")
    if candidate_task_no != task_no:
        raise DesignTaskValidationError(
            f"task_no {candidate_task_no!r} does not match expected {task_no}"
        )

    # 8. finding_ids 格式检查（实际引用的内容由规划服务做跨行校验）
    finding_ids = candidate.get("finding_ids")
    if finding_ids is not None and not isinstance(finding_ids, (list, tuple)):
        raise DesignTaskValidationError("finding_ids must be a list of UUIDs")


def validate_candidate_set(
    candidates: Sequence[Mapping[str, Any]],
    *,
    target_count: int,
    difficulty_distribution: Mapping[str, int],
) -> None:
    """校验候选任务集合的数量和难度分布是否符合要求。

    检查项:
      - 候选数量必须等于 target_count
      - task_no 序列必须是 1, 2, 3, ... target_count
      - 难度分布必须与期望一致
    """
    # 数量检查
    if len(candidates) != target_count:
        raise DesignTaskValidationError(
            f"generated {len(candidates)} task(s) but target_count is {target_count}"
        )

    # task_no 序列检查（必须是连续的 1..N）
    seen_task_nos = sorted(int(c.get("task_no", 0)) for c in candidates)
    expected = list(range(1, target_count + 1))
    if seen_task_nos != expected:
        raise DesignTaskValidationError(
            f"task_no sequence {seen_task_nos} does not equal {expected}"
        )

    # 难度分布统计
    actual_distribution: dict[str, int] = {}
    for candidate in candidates:
        difficulty = candidate.get("difficulty")
        if not isinstance(difficulty, str):
            continue
        actual_distribution[difficulty] = actual_distribution.get(difficulty, 0) + 1

    # 只比较 count > 0 的标签（排除 0 值）
    expected_distribution = {
        label: int(count) for label, count in difficulty_distribution.items() if count
    }
    if actual_distribution != expected_distribution:
        raise DesignTaskValidationError(
            f"difficulty mix {actual_distribution} does not match "
            f"{expected_distribution}"
        )


def validate_status_transition(
    current: str,
    target: str,
    *,
    plan_reviewed_at=None,
    review_exempt: bool = False,
) -> None:
    """校验设计任务的状态转换是否合法。

    当前版本只允许操作者（operator）的状态转换:
      draft → queued      （草稿 → 提交到队列）
      draft → archived     （草稿 → 直接归档）
      queued → archived    （排队中 → 取消归档）

    Worker 端的状态转换（queued → designing → designed → failed）
    保留给未来的 design-worker 实现，此处拒绝这些转换。
    """
    if current not in DesignTaskStatus:
        raise DesignTaskValidationError(
            f"current status {current!r} is not a valid design task status"
        )
    if target not in DesignTaskStatus:
        raise DesignTaskValidationError(
            f"target status {target!r} is not a valid design task status"
        )

    # 定义允许的状态转换规则
    allowed: dict[str, frozenset[str]] = {
        "draft": frozenset({"queued", "archived"}),
        "queued": frozenset({"archived"}),
    }

    if target not in allowed.get(current, frozenset()):
        raise DesignTaskValidationError(
            f"transition {current!r} -> {target!r} is not allowed by the "
            "planning endpoint"
        )
    if current == "draft" and target == "queued" and plan_reviewed_at is None and not review_exempt:
        raise DesignTaskValidationError("plan_not_reviewed")
