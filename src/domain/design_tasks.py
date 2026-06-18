"""设计任务规划流程的领域 DTO 和值集合。

对应 Alembic 迁移 0003_design_tasks 创建的 design_tasks 表。
DTO 使用 frozen dataclass；允许的状态值以 tuple 常量暴露。

字段命名故意与 core.queue / domain.seeds 保持一致
（challenge_id, title, category, difficulty 等），
以便未来分片导出时无需重命名即可序列化。

校验逻辑位于 domain.design_task_validators；本文件只定义数据形状。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# 设计任务的状态: 草稿 → 排队 → 设计中 → 已设计 → 失败 → 归档
DesignTaskStatus: tuple[str, ...] = (
    "draft",       # 草稿
    "queued",      # 排队
    "designing",   # 设计中
    "designed",    # 已设计
    "failed",      # 失败
    "archived",    # 归档
)


@dataclass(frozen=True)
class DesignTask:
    """一个设计任务。

    由 Research 流程完成后产生，每个任务对应一道待设计的题目。
    任务包含题目的基本信息（类别、难度、技术等）和 AI 生成所需的所有上下文。
    """
    id: UUID                              # 任务 ID
    generation_request_id: UUID           # 所属生成请求 ID
    research_run_id: UUID                 # 引用 Research Run 的研究成果
    task_no: int                          # 任务序号（1-based）
    challenge_id: str                     # 题目 ID（如 "web-0001"）
    title: str                            # 题目标题
    category: str                         # 题目类别
    difficulty: str                       # 难度
    primary_technique: str                # 核心技术（如 "SQL Injection"）
    learning_objective: str              # 学习目标
    points: int                           # 分值
    port: int | None                      # 容器端口（web/pwn 需要）
    scenario: str                         # 场景描述
    constraints: Mapping[str, Any]        # 任务约束
    evidence_summary: str                 # Research 证据摘要
    finding_ids: Sequence[UUID]           # 引用的 Research Finding ID 列表
    status: str                           # 任务状态
    created_at: datetime
    updated_at: datetime
