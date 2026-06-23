"""Research 规划流程的领域 DTO 和值集合。

对应 Alembic 迁移 0002_research_tables 创建的八张数据表。
DTO 使用不可变的 frozen dataclass；合法的状态枚举值以 tuple 常量暴露。
分类（category）和角色（role）的值集不在此处硬编码——
它们是从数据库的查找表（lookup table）中动态查询的。

校验逻辑位于 domain.research_validators；本文件只定义数据形状。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# ---------------------------------------------------------------------------
# 状态枚举常量
# 对应 PostgreSQL 中的三种枚举类型、绑定状态检查约束和难度白名单
# 使用 tuple 确保顺序稳定，便于错误消息展示
# ---------------------------------------------------------------------------

# 生成请求的状态: 草稿 → 研究中 → 已完成研究 → 失败
GenerationRequestStatus: tuple[str, ...] = ("draft", "researching", "researched", "failed")
# Research Run 的状态: 排队 → 执行中 → 完成 → 失败
ResearchRunStatus: tuple[str, ...] = ("queued", "running", "completed", "failed")
# Research Finding 的种类: 技术 / 变体 / 场景 / 前置知识
ResearchFindingKind: tuple[str, ...] = ("technique", "variant", "scenario", "prerequisite")
# Hermes Profile 绑定的状态: 启用 / 禁用
BindingStatus: tuple[str, ...] = ("enabled", "disabled")
# 难度标签白名单
DIFFICULTY_LABELS: tuple[str, ...] = ("easy", "medium", "hard", "expert")


# ---------------------------------------------------------------------------
# Frozen dataclass DTO（数据传输对象）
# 它们是八张表的内存视图，字段名和数据库列一一对应。
# Frozen 只约束字段重新赋值，dict/list 等可变值仍是可变的。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChallengeCategory:
    """题目类别（如 web/pwn/re）。"""
    code: str             # 类别代码（如 "web"）
    display_name: str     # 显示名（如 "Web Security"）
    description: str | None = None  # 类别描述


@dataclass(frozen=True)
class AgentRole:
    """代理角色（如 research/design）。"""
    code: str             # 角色代码（如 "research"）
    display_name: str     # 显示名
    description: str | None = None  # 角色描述


@dataclass(frozen=True)
class HermesProfileBinding:
    """Hermes Profile 与 Agent Role 的绑定关系。

    每个角色可以绑定一个 profile，Worker 执行该角色的任务时使用对应的 profile。
    """
    role: str                    # 角色代码
    profile_name: str            # Hermes profile 名称
    description: str | None      # 绑定描述
    status: str                  # 绑定状态（enabled/disabled）
    last_used_at: datetime | None      # 最近使用时间
    last_used_run_id: UUID | None      # 最近使用的 run ID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class GenerationRequest:
    """一次题目生成请求。

    包含用户提交的所有参数：类别、主题、目标数量、难度分布等。
    """
    id: UUID                              # 请求 ID
    category: str                         # 题目类别
    topic: str                            # 研究主题
    target_count: int                     # 目标生成数量
    difficulty_distribution: Mapping[str, int]  # 难度分布（如 {"easy": 3, "medium": 2}）
    runtime_constraints: Mapping[str, Any]    # 运行时约束（如超时、内存限制等）
    seed_urls: tuple[str, ...]            # 种子 URL 列表（供 AI 研究参考）
    max_attempts: int                     # 最大重试次数
    status: str                           # 请求状态（draft/researching/researched/failed）
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ResearchRun:
    """一次 Research 运行实例。

    每个 GenerationRequest 可以有多次 run（支持重试），
    通过 attempt 字段记录重试次数。
    """
    id: UUID                              # Run ID
    generation_request_id: UUID           # 所属生成请求 ID
    parent_run_id: UUID | None            # 父 Run ID（重试链）
    attempt: int                          # 第几次尝试
    status: str                           # 状态（queued/running/completed/failed）
    claimed_by: str | None                # 认领 Worker 标识
    claim_token: UUID | None              # 认领令牌（token-fencing 的 key）
    claimed_at: datetime | None           # 认领时间
    heartbeat_at: datetime | None         # 最近心跳时间
    lease_expires_at: datetime | None     # 租约过期时间
    started_at: datetime | None           # 开始执行时间
    finished_at: datetime | None          # 结束时间
    last_error: str | None                # 最后一次错误信息
    hermes_log_path: str | None           # Hermes 执行日志路径
    profile_name_used: str | None         # 使用的 Hermes profile 名称
    created_at: datetime
    was_retried: bool | None = None       # 是否曾被重试


@dataclass(frozen=True)
class ResearchSource:
    """Research 过程中搜集到的资料来源。

    每个 source 对应一个 URL 和其内容摘要。
    """
    id: UUID                              # Source ID
    research_run_id: UUID                 # 所属 Run ID
    url: str                              # 资料来源 URL
    title: str                            # 资料标题
    summary: str                          # 内容摘要
    content_hash: str                     # 内容哈希（用于去重）
    fetched_at: datetime                  # 抓取时间
    raw_text_path: str | None = None      # 原始文本存储路径


@dataclass(frozen=True)
class ResearchFinding:
    """Research 发现条目（从 source 中提取的研究资料）。

    每个 finding 关联 1-3 个 source，表示从这些来源中综合提取的信息。
    """
    id: UUID                              # Finding ID
    research_run_id: UUID                 # 所属 Run ID
    kind: str                             # 种类（technique/variant/scenario/prerequisite）
    label: str                            # 标签
    summary: str                          # 摘要
    technique_family: str | None = None   # 技术族（弱约束；缺失时由 label 推导）


@dataclass(frozen=True)
class ResearchFindingSource:
    """Finding 与 Source 的多对多关联。"""
    finding_id: UUID
    source_id: UUID
