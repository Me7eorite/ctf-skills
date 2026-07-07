"""Research run failure classification helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

FailureCategory = Literal[
    "timeout",
    "rate_limit",
    "lease_expired",
    "parse_failure",
    "quality_gate",
    "insufficient_evidence",
    "field_validation",
    "resume_conflict",
    "binding",
    "runtime",
    "cancelled",
    "unknown",
]


@dataclass(frozen=True)
class FailureClassification:
    category: FailureCategory
    title: str
    description: str
    actions: tuple[str, ...]


_CATEGORY_COPY: dict[FailureCategory, tuple[str, str, tuple[str, ...]]] = {
    "timeout": (
        "研究执行超时",
        "Hermes 研究进程超过允许时间后退出，可能是目标数量过大、提示词过重或外部检索耗时过长。",
        ("增大 `--hermes-timeout-seconds`", "降低 `target_count` 后重新研究", "查看日志末尾确认是否已有可恢复输出"),
    ),
    "rate_limit": (
        "研究被限流",
        "Hermes 或其 provider 遇到 429/overload/请求速率限制，通常可以通过退避重试或降低并发缓解。",
        ("降低搜索并发", "增加退避后重试", "检查是否已接近预算并收敛到既有 sources"),
    ),
    "lease_expired": (
        "研究租约过期",
        "Worker 在运行期间未能持续续租，系统已将该次运行标记为失败并按重试规则处理。",
        ("检查 Worker 心跳和数据库连接", "确认是否已有新的重试运行", "查看 Hermes 日志判断是否可从日志恢复"),
    ),
    "parse_failure": (
        "研究输出无法解析",
        "Hermes 输出没有满足研究阶段约定的终端 JSON 或顶层结构不正确。",
        ("查看日志末尾内容", "修正提示词中的输出格式约束", "必要时重新研究"),
    ),
    "quality_gate": (
        "研究质量未达标",
        "Hermes 输出可解析，但有效研究结论数量或结构没有达到质量门要求。",
        ("增加研究目标或种子线索", "降低 `target_count` 或调整难度分布", "查看 findings 是否过少或重复"),
    ),
    "insufficient_evidence": (
        "研究证据不足",
        "Hermes 输出缺少足够的 sources 或 findings，无法形成可落库结果。",
        ("补充 seed URLs 或关键检索词", "降低 `target_count`", "复用已确认的 sources 再收敛 findings"),
    ),
    "field_validation": (
        "研究字段校验失败",
        "Hermes 输出中的 source 或 finding 字段缺失、类型错误或引用关系无效。",
        ("查看原始 JSON 中的字段形状", "修正提示词样例", "重新研究并检查 source_indices"),
    ),
    "resume_conflict": (
        "研究恢复冲突",
        "尝试恢复或补跑时，持久化状态已经包含更新的结果或并发 sibling 冲突。",
        ("重新拉取最新 run 状态", "检查是否已有完成的 sibling run", "避免重复回填同一份证据"),
    ),
    "binding": (
        "研究 Agent 配置不可用",
        "研究阶段需要的 Hermes profile 绑定缺失、停用或指向不存在的 profile。",
        (
            "检查 `challenge-factory profile show research`",
            "重新绑定可用 Hermes profile",
            "确认 profile 未被停用或删除",
        ),
    ),
    "runtime": (
        "研究运行时错误",
        "研究执行遇到非格式类运行错误，可能是 Hermes 非零退出、数据状态异常或持久化依赖失败。",
        ("查看 Hermes 日志和服务端日志", "确认数据库中 request/run 状态一致", "修复环境后重新研究"),
    ),
    "cancelled": (
        "研究已取消",
        "操作员取消了该次研究运行。",
        (),
    ),
    "unknown": (
        "未知研究失败",
        "研究失败原因没有匹配到已知分类。",
        (),
    ),
}


_INSUFFICIENT_RE = re.compile(r"insufficient_findings:got=(\d+),need=(\d+)", re.IGNORECASE)
_INSUFFICIENT_DIVERSITY_RE = re.compile(
    r"insufficient_diversity:distinct=(\d+),need=(\d+)",
    re.IGNORECASE,
)
_HERMES_EXIT_RE = re.compile(r"hermes exited with\s+(-?\d+)", re.IGNORECASE)


def classify_last_error(text: str | None) -> FailureClassification:
    """Map arbitrary research ``last_error`` text to stable operator-facing metadata."""
    raw = "" if text is None else str(text)
    normalized = raw.strip()
    lower = normalized.lower()

    category: FailureCategory
    description_override: str | None = None

    if _is_timeout(lower):
        category = "timeout"
    elif _is_rate_limit(lower):
        category = "rate_limit"
    elif _is_lease_expired(lower):
        category = "lease_expired"
    elif _is_parse_failure(lower):
        category = "parse_failure"
    elif match := _INSUFFICIENT_RE.search(normalized):
        got, need = match.groups()
        category = "quality_gate"
        description_override = f"Hermes 输出只有 {got} 条有效研究结论，低于最低要求 {need} 条。"
    elif match := _INSUFFICIENT_DIVERSITY_RE.search(normalized):
        got, need = match.groups()
        category = "quality_gate"
        description_override = f"Hermes 输出只有 {got} 个不同子技巧，低于最低要求 {need} 个。"
    elif _is_quality_gate(lower):
        category = "quality_gate"
    elif _is_insufficient_evidence(lower):
        category = "insufficient_evidence"
    elif _is_resume_conflict(lower):
        category = "resume_conflict"
    elif _is_field_validation(lower):
        category = "field_validation"
    elif _is_binding(lower):
        category = "binding"
    elif _is_runtime(lower):
        category = "runtime"
    elif _is_cancelled(lower):
        category = "cancelled"
    else:
        category = "unknown"
        if normalized:
            description_override = f"原始失败信息：{normalized}"

    title, description, actions = _CATEGORY_COPY[category]
    return FailureClassification(
        category=category,
        title=title,
        description=description_override or description,
        actions=actions,
    )


def _is_timeout(value: str) -> bool:
    match = _HERMES_EXIT_RE.search(value)
    return bool(match and match.group(1) == "124") or "timeout" in value or "timed out" in value


def _is_rate_limit(value: str) -> bool:
    return (
        "rate_limit" in value
        or "rate limit" in value
        or "overloaded" in value
        or "429" in value
    )


def _is_lease_expired(value: str) -> bool:
    return "lease expired" in value or "lease_expires_at" in value


def _is_parse_failure(value: str) -> bool:
    return value.startswith("unparseable_output:")


def _is_quality_gate(value: str) -> bool:
    return "quality_gate" in value or value.startswith("insufficient_findings")


def _is_insufficient_evidence(value: str) -> bool:
    return value.startswith("insufficient_evidence")


def _is_resume_conflict(value: str) -> bool:
    return "resume_conflict" in value or value.startswith("preview_stale")


def _is_field_validation(value: str) -> bool:
    prefixes = (
        "url_shape_invalid",
        "content_hash_shape_invalid",
        "content_hash_dup",
        "research output field ",
        "each source must be",
        "each finding must be",
        "source raw_text must be",
        "source field ",
        "finding field ",
        "technique_family ",
        "finding source_indices",
        "source index ",
        "finding must include",
        "source_id(s) ",
        "expected datetime for fetched_at",
        "missing required field ",
        "field ",
    )
    return value.startswith(prefixes)


def _is_binding(value: str) -> bool:
    return (
        value == "profile_not_bound"
        or value.startswith("profile_disabled:")
        or (value.startswith("hermes profile ") and value.endswith(" does not exist"))
        or value.startswith("binding role ")
        or value.startswith("agent role ")
    )


def _is_runtime(value: str) -> bool:
    match = _HERMES_EXIT_RE.search(value)
    return bool(match) or value.startswith("generation_request ") or "commit validation failed" in value


def _is_cancelled(value: str) -> bool:
    return "cancelled" in value or "canceled" in value
