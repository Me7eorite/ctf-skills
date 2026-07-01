"""进度事件的协议定义和内存实现。

定义了系统中所有进度追踪的数据结构、抽象接口和百分比计算规则。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.clock import beijing_now_isoformat

# ========== 阶段与状态常量 ==========

# 题目生成的七个执行阶段（按先后顺序排列）
STAGES = (
    "queued",      # 排队中：分片已入队，等待 Worker 认领
    "design",      # 设计中：AI 正在生成题目设计方案
    "implement",   # 编码中：AI 正在编写题目代码
    "build",       # 构建中：AI 正在编译/构建题目环境
    "validate",    # 校验中：运行确定性验证脚本检查题目质量
    "document",    # 文档化：AI 正在编写题目说明文档
    "complete",    # 完成：所有阶段通过，题目生成完毕
)

# 每个阶段可能出现的四种状态
STATUSES = {"pending", "running", "passed", "failed"}
#               待处理     执行中      通过      失败

# 五个可执行阶段（排除 queued 排队和 complete 终态），用于阶段耗时/恢复计划等场景。
EXECUTION_STAGES: tuple[str, ...] = (
    "design",
    "implement",
    "build",
    "validate",
    "document",
)


# ========== 数据结构 ==========

@dataclass(frozen=True)
class ProgressEventInput:
    """进度事件的输入数据结构（不可变）。

    属性:
        shard: 所属分片标识（如 "web-0001-0005"）
        stage: 当前阶段（必须是 STAGES 中的值）
        status: 当前状态（必须是 STATUSES 中的值）
        challenge_id: 题目 ID，空字符串表示分片级别事件
        worker: 执行该事件的 Worker 名称
        message: 附带的人类可读消息
    """
    shard: str
    stage: str
    status: str
    challenge_id: str = ""
    worker: str = ""
    message: str = ""


# ========== 存储接口（Protocol）==========

class ProgressStore(Protocol):
    """进度存储的抽象接口。

    使用 Python Protocol 而非 ABC 抽象类，
    这样 PostgresProgressStore 和 InMemoryProgressStore
    不需要显式继承，只要实现了相同的方法签名即可通过类型检查。
    """

    def record(
        self,
        *,
        shard: str,
        stage: str,
        status: str,
        challenge_id: str = "",
        worker: str = "",
        message: str = "",
    ) -> dict:
        """记录单个进度事件。返回包含 event_id 和 updated_at 的结果字典。"""
        ...

    def record_batch(self, events: Sequence[ProgressEventInput]) -> list[dict]:
        """批量记录进度事件。在一个事务中完成，返回结果列表。"""
        ...

    def events_for_shard(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> list[dict]:
        """查询指定分片的所有进度事件，支持基于事件 ID 的分页。"""
        ...

    def events_for_challenge(
        self,
        shard: str,
        challenge_id: str,
        *,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """查询指定分片下某个题目的所有进度事件。"""
        ...

    def latest_claim_event(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> dict | None:
        """查询分片的最新认领事件（queued running）。
        用于断点恢复时判断上次执行是由哪个 Worker 启动的。"""
        ...

    def reset_snapshots(self, shard: str) -> None:
        """重置指定分片的看板快照数据。用于分片重新执行前清理旧状态。"""
        ...

    def purge_shards(
        self,
        shards: Collection[str],
        *,
        transaction: object | None = None,
    ) -> None:
        """Remove all events and snapshots for the supplied shard basenames."""
        ...

    def dashboard(self, event_limit: int = 60) -> dict:
        """获取 Dashboard 所需的聚合数据：快照列表 + 最近 N 条事件 + 存储信息。"""
        ...


# ========== 工具函数 ==========

def display_now() -> str:
    """返回当前北京时间的 ISO 8601 格式字符串，用于看板展示。"""
    return beijing_now_isoformat()


# ========== 内存实现（用于测试和单进程使用）==========

class InMemoryProgressStore:
    """仅追加的内存进度存储。

    用途:
      - 单元测试中替代 PostgreSQL 实现
      - 不需要持久化的单进程场景

    内部结构:
      _events: 按插入顺序存储的事件列表
      _snapshots: {(shard, challenge_id) → dict} 的最新快照
      _next_id: 自增的事件 ID
    """

    def __init__(self) -> None:
        self._next_id = 1
        self._events: list[dict] = []
        self._snapshots: dict[tuple[str, str], dict] = {}

    def record(
        self,
        *,
        shard: str,
        stage: str,
        status: str,
        challenge_id: str = "",
        worker: str = "",
        message: str = "",
    ) -> dict:
        """单条记录（委托给 record_batch）。"""
        return self.record_batch(
            [
                ProgressEventInput(
                    shard=shard,
                    stage=stage,
                    status=status,
                    challenge_id=challenge_id,
                    worker=worker,
                    message=message,
                )
            ]
        )[0]

    def record_batch(self, events: Sequence[ProgressEventInput]) -> list[dict]:
        """批量记录进度事件。

        处理流程:
          1. 校验所有输入事件（_prepare_event）
          2. 为每个事件分配 ID 并计算百分比
          3. 存储到内部列表
          4. 更新对应快照数据
        """
        prepared = [_prepare_event(event) for event in events]
        timestamp = display_now()
        results: list[dict] = []
        for event in prepared:
            event_id = self._next_id
            self._next_id += 1
            stored = {
                "id": event_id,
                "shard": event.shard,
                "challenge_id": event.challenge_id,
                "worker": event.worker,
                "stage": event.stage,
                "status": event.status,
                "percent": _percent(event.stage, event.status),
                "message": event.message,
                "created_at": timestamp,
            }
            self._events.append(stored)
            # 更新看板快照（以百分比更高的状态为准）
            self._upsert_snapshot(stored, timestamp)
            results.append(_event_result(stored, updated_at=timestamp))
        return results

    def events_for_shard(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> list[dict]:
        """查询分片的所有事件，可选 'before_id' 分页。"""
        normalized = _normalize_shard(shard)
        rows = [event for event in self._events if event["shard"] == normalized]
        if before_id is not None:
            rows = [event for event in rows if int(event["id"]) < before_id]
        return [dict(event) for event in rows]

    def events_for_challenge(
        self,
        shard: str,
        challenge_id: str,
        *,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """查询分片下某个题目的所有事件。challenge_id 不能为空。"""
        if not challenge_id:
            raise ValueError(
                "challenge_id must be non-empty; use events_for_shard or "
                "latest_claim_event for shard-level queries"
            )
        normalized = _normalize_shard(shard)
        rows = [
            event
            for event in self._events
            if event["shard"] == normalized and event["challenge_id"] == challenge_id
        ]
        if after_id is not None:
            rows = [event for event in rows if int(event["id"]) >= after_id]
        if before_id is not None:
            rows = [event for event in rows if int(event["id"]) < before_id]
        return [dict(event) for event in rows]

    def latest_claim_event(
        self,
        shard: str,
        *,
        before_id: int | None = None,
    ) -> dict | None:
        """查找分片的最新认领事件。

        认领事件的判定条件: challenge_id 为空 且 stage=queued 且 status=running。
        这是 Worker 首次认领分片时写入的标记事件。
        """
        normalized = _normalize_shard(shard)
        rows = [
            event
            for event in self._events
            if event["shard"] == normalized
            and event["challenge_id"] == ""
            and event["stage"] == "queued"
            and event["status"] == "running"
        ]
        if before_id is not None:
            rows = [event for event in rows if int(event["id"]) < before_id]
        if not rows:
            return None
        # 取最后一条（ID 最大的）
        return dict(rows[-1])

    def reset_snapshots(self, shard: str) -> None:
        """删除指定分片的所有快照数据。"""
        normalized = _normalize_shard(shard)
        # 用 list() 创建副本避免在遍历中修改字典
        for key in list(self._snapshots):
            if key[0] == normalized:
                del self._snapshots[key]

    def purge_shards(
        self,
        shards: Collection[str],
        *,
        transaction: object | None = None,
    ) -> None:
        """Atomically remove all in-memory progress for shard basenames."""
        normalized = {_normalize_shard(shard) for shard in shards}
        if not normalized:
            return
        self._events = [
            event for event in self._events if event["shard"] not in normalized
        ]
        for key in list(self._snapshots):
            if key[0] in normalized:
                del self._snapshots[key]

    def dashboard(self, event_limit: int = 60) -> dict:
        """生成 Dashboard 所需数据。

        返回结构:
          - snapshots: 看板快照列表（按时间倒序）
          - events: 最近 N 条事件（按 ID 倒序）
          - storage: 存储后端信息（backend/path/fallback/warning）
        """
        # 快照按时间、分片名、题目名倒序排列
        snapshots = sorted(
            (dict(snapshot) for snapshot in self._snapshots.values()),
            key=lambda row: (row["updated_at"], row["shard"], row["challenge_id"]),
            reverse=True,
        )
        events = [dict(event) for event in reversed(self._events[-event_limit:])]
        return {
            "snapshots": snapshots,
            "events": events,
            "storage": {
                "backend": "memory",
                "path": "memory://",
                "fallback": False,
                "warning": "",
            },
        }

    def _upsert_snapshot(self, event: dict, timestamp: str) -> None:
        """更新或创建看板快照。

        快照始终反映最高进度的状态：
          如果新事件的 percent >= 当前快照的 percent，
          则更新 stage/status/percent 为新值；
          如果新事件的进度更低（可能是迟到的事件），
          则只更新观察字段（worker/message），保留高进度的 stage/status。
        这样可以确保 Dashboard 不会显示倒退的进度。
        """
        key = (event["shard"], event["challenge_id"])
        current = self._snapshots.get(key)
        if current is None:
            # 第一次记录该分片/题目 → 创建新快照
            self._snapshots[key] = _snapshot_from_event(event, timestamp)
            return
        # 总是更新观察字段（worker、message、时间戳）
        current["worker"] = event["worker"]
        current["message"] = event["message"]
        current["updated_at"] = timestamp
        # 只用进度更高的阶段/状态更新（防止进展倒退）
        if int(event["percent"]) >= int(current["percent"]):
            current["stage"] = event["stage"]
            current["status"] = event["status"]
            current["percent"] = event["percent"]


# ========== 内部辅助函数 ==========

def _prepare_event(event: ProgressEventInput) -> ProgressEventInput:
    """校验并规范化进度事件。

    校验内容:
      - stage 必须是 STAGES 中的合法值
      - status 必须是 STATUSES 中的合法值
      - shard 会做路径归一化（_normalize_shard）
    """
    if event.stage not in STAGES:
        raise ValueError(f"invalid progress stage: {event.stage}")
    if event.status not in STATUSES:
        raise ValueError(f"invalid progress status: {event.status}")
    return ProgressEventInput(
        shard=_normalize_shard(event.shard),
        challenge_id=event.challenge_id,
        worker=event.worker,
        stage=event.stage,
        status=event.status,
        message=event.message,
    )


def _normalize_shard(shard: str) -> str:
    """将 shard 路径归一化为纯文件名（去掉目录部分）。

    例如: "/path/to/web-0001.json" → "web-0001.json"
    使用 Path(x).name 也同时做了基本的路径遍历防护。
    """
    return Path(shard).name


def _percent(stage: str, status: str) -> int:
    """根据阶段和状态计算进度百分比。

    计算逻辑:
      每个阶段占 16 个百分点（7 阶段 × 16 = 112，但 complete passed = 100 做截断）。
      pending   = 阶段起始 - 8 个点（不低于 0）
      running   = 阶段起始 + 5 个点（不超过 95）
      failed    = 阶段起始 + 8 个点（不超过 99）
      passed    = 下一阶段起始（不超过 96，complete 直接返回 100）

    示例:
      design pending   →   8%
      design running   →  21%
      design failed    →  24%
      design passed    →  32%
      complete passed  → 100%
    """
    # 获取当前阶段在 STAGES 中的索引位置
    index = STAGES.index(stage)

    if status == "pending":
        # 未开始：位于上一阶段 passed 之后、本阶段 running 之前
        return max(0, index * 16 - 8)
    if status == "running":
        # 执行中：大约在阶段的前 1/3 位置
        return min(95, index * 16 + 5)
    if status == "failed":
        # 失败：大约在阶段的一半位置，最高卡在 99%
        return min(99, index * 16 + 8)
    # status == "passed"：完成，进入下一阶段的起始百分比
    return 100 if stage == "complete" else min(96, (index + 1) * 16)


def _event_result(event: dict, *, updated_at: str) -> dict:
    """将事件字典转换为返回给调用方的结果格式。"""
    return {
        "event_id": event["id"],
        "shard": event["shard"],
        "challenge_id": event["challenge_id"],
        "worker": event["worker"],
        "stage": event["stage"],
        "status": event["status"],
        "percent": event["percent"],
        "message": event["message"],
        "updated_at": updated_at,
    }


def _snapshot_from_event(event: dict, timestamp: str) -> dict:
    """从事件字典生成快照字典（用于创建新快照）。"""
    return {
        "shard": event["shard"],
        "challenge_id": event["challenge_id"],
        "worker": event["worker"],
        "stage": event["stage"],
        "status": event["status"],
        "percent": event["percent"],
        "message": event["message"],
        "updated_at": timestamp,
    }
