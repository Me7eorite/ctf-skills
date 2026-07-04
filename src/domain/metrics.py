"""按阶段统计执行耗时的指标计算模块。

从进度事件（progress events）中读取最近一次认领窗口（claim window）的事件，
计算每个阶段的挂钟时间（wall-clock duration）。

阶段耗时的计算规则:
  last_passed.created_at - first_running.created_at
即该阶段第一条 running 事件到该阶段最后一条 passed 事件的时间差。
只有该阶段最终状态为 passed 时才产生有效耗时数据。
断点恢复传递下来的 passed 事件如果没有对应的 running 事件，则该阶段耗时返回 None。
"""

from __future__ import annotations

import calendar
import time
from typing import Any

from core.state import EXECUTION_STAGES as STAGE_ORDER
from core.state import ProgressStore

# UTC 时间戳格式
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_timestamp(value: str) -> float | None:
    """将 ISO 8601 格式的 UTC 时间戳字符串解析为 epoch 秒数。"""
    try:
        return float(calendar.timegm(time.strptime(value, _TIMESTAMP_FORMAT)))
    except (TypeError, ValueError):
        return None


def _latest_event(events: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
    """从事件列表中查找指定阶段的最后一条事件（按 ID 倒序取第一条匹配的）。"""
    for event in reversed(events):
        if event.get("stage") == stage:
            return event
    return None


def _first_running_event(
    events: list[dict[str, Any]], stage: str
) -> dict[str, Any] | None:
    """从事件列表中查找指定阶段的第一条 running 状态事件（按 ID 正序取第一条匹配的）。"""
    for event in events:
        if event.get("stage") == stage and event.get("status") == "running":
            return event
    return None


def duration_breakdown(
    state: ProgressStore, challenge_id: str, shard: str
) -> dict[str, float | None]:
    """计算指定题目在最新认领窗口内各阶段的耗时。

    参数:
        state: 进度存储实例（内存或 PostgreSQL）
        challenge_id: 题目 ID
        shard: 分片名称（必须是归一化后的原始基础名称）

    返回:
        {阶段名: 耗时秒数} 的字典，无法计算的阶段值为 None。

    计算逻辑:
      1. 找到分片的最近一次认领事件（queued running）
      2. 获取该事件之后该题目的所有事件
      3. 对每个阶段，找到最后一条 passed 事件和第一条 running 事件
      4. 如果两者都存在，计算时间差
    """
    # 查找最近的认领事件作为窗口起点
    latest_claim = state.latest_claim_event(shard)
    if latest_claim is None:
        return {stage: None for stage in STAGE_ORDER}

    # 获取认领事件之后的所有题目事件
    events = state.events_for_challenge(
        shard, challenge_id, after_id=int(latest_claim["id"])
    )

    durations: dict[str, float | None] = {}
    for stage in STAGE_ORDER:
        # 找到该阶段的最后一条事件
        latest = _latest_event(events, stage)
        # 如果最终状态不是 passed，说明该阶段未完成，无有效耗时
        if latest is None or latest.get("status") != "passed":
            durations[stage] = None
            continue
        # 找到该阶段的第一条 running 事件
        running = _first_running_event(events, stage)
        if running is None:
            # 断点恢复场景：有 passed 但没有 running（是从上一轮传递来的）
            durations[stage] = None
            continue
        # 解析开始和结束时间戳
        start_ts = _parse_timestamp(str(running.get("created_at", "")))
        end_ts = _parse_timestamp(str(latest.get("created_at", "")))
        if start_ts is None or end_ts is None:
            durations[stage] = None
            continue
        # 确保耗时不小于 0
        durations[stage] = max(0.0, end_ts - start_ts)
    return durations
