"""结构化题目设计的领域 DTO 和值集合。

定义了设计尝试（DesignAttempt）和题目设计（ChallengeDesign）的数据结构。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# 设计尝试的状态: 执行中 / 完成 / 失败
DesignAttemptStatus: tuple[str, ...] = ("running", "completed", "failed")
# 题目设计的状态: 草稿 / 已接受 / 已替代
ChallengeDesignStatus: tuple[str, ...] = ("draft", "accepted", "superseded")


@dataclass(frozen=True)
class DesignAttempt:
    """一次设计尝试（AI 生成题目设计的过程记录）。

    每个设计任务可以有多次尝试（支持重试）。
    """
    id: UUID                        # 尝试 ID
    design_task_id: UUID            # 所属设计任务 ID
    attempt: int                    # 第几次尝试
    status: str                     # 状态（running/completed/failed）
    claimed_by: str | None          # 认领 Worker 标识
    claim_token: UUID               # 认领令牌（token-fencing）
    started_at: datetime | None     # 开始时间
    finished_at: datetime | None    # 结束时间
    profile_name_used: str          # 使用的 Hermes profile 名称
    prompt_path: str | None         # 使用的 prompt 文件路径
    hermes_log_path: str | None     # Hermes 执行日志路径
    last_error: str | None          # 最后一次错误信息
    created_at: datetime


@dataclass(frozen=True)
class ChallengeDesign:
    """一份完整的题目设计方案。

    由 DesignAttempt 成功后产生，包含 AI 生成的题目设计 JSON 及其校验结果。
    """
    id: UUID                        # 设计 ID
    design_task_id: UUID            # 所属设计任务 ID
    design_attempt_id: UUID         # 所属设计尝试 ID
    payload: Mapping[str, Any]      # 完整的设计 JSON payload
    summary: str                    # 设计摘要
    flag_format: str                # Flag 格式（如 "flag{...}"）
    validation_notes: str           # 校验备注
    quality_gate_passed: bool       # 是否通过质量门
    status: str                     # 状态（draft/accepted/superseded）
    created_at: datetime
    updated_at: datetime
    # Phase 2: rows pre-dating the difficulty rubric are flagged so a
    # future backfill / re-design script can pick them up explicitly.
    # New rows default False.
    legacy_grandfather: bool = False
