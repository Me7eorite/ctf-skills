"""research-planning 持久化仓储原语。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from domain import research as dto
from domain.research_validators import (
    ResearchValidationError,
    validate_category,
    validate_distribution,
    validate_finding,
)
from persistence.models import research as model


class ResearchRepository:
    """类型化 CRUD/查询原语；事务边界由调用方负责。"""

    def __init__(self, session: Session) -> None:
        # Repository 只持有调用方传入的 session，不自行 commit/rollback。
        self.session = session

    def list_categories(self) -> list[dto.ChallengeCategory]:
        """列出 research 层允许的题目分类。"""
        # challenge_categories 是分类白名单来源，按 code 排序保证输出稳定。
        rows = self.session.scalars(
            sa.select(model.ChallengeCategory).order_by(model.ChallengeCategory.code)
        ).all()
        # ORM row 不向上泄漏，统一转换成 domain DTO。
        return [_category(row) for row in rows]

    def get_generation_request(self, request_id: UUID) -> dto.GenerationRequest | None:
        """按 id 读取 generation request。"""
        # session.get 走主键读取；找不到时返回 None，让上层决定 404/失败策略。
        row = self.session.get(model.GenerationRequest, request_id)
        return _generation_request(row) if row else None

    def lock_generation_request(self, request_id: UUID) -> None:
        # SELECT ... FOR UPDATE 用于在事务内串行化对同一 generation_request
        # 的并发写入（例如设计任务生成时的“无现有行也要避免竞态”场景）。
        # 锁在 execute 时即生效，无需消费结果集；存在性检查由调用方通过
        # get_generation_request 完成。
        stmt = (
            sa.select(model.GenerationRequest.id)
            .where(model.GenerationRequest.id == request_id)
            .with_for_update()
        )
        self.session.execute(stmt)

    def list_generation_requests(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
    ) -> list[dto.GenerationRequest]:
        """按可选 category/status 过滤 generation requests。"""
        stmt = sa.select(model.GenerationRequest).order_by(model.GenerationRequest.created_at)
        # 先构造基础查询，再按传入过滤条件逐步收窄。
        if category is not None:
            # category 过滤用于运营侧按题目类型查看 research 请求。
            stmt = stmt.where(model.GenerationRequest.category == category)
        if status is not None:
            # status 过滤用于队列和 UI 只展示特定生命周期状态。
            stmt = stmt.where(model.GenerationRequest.status == status)
        return [_generation_request(row) for row in self.session.scalars(stmt)]

    def get_run(self, run_id: UUID) -> dto.ResearchRun | None:
        """按 id 读取 research run。"""
        # run 是队列尝试记录，主键查询保持最小开销。
        row = self.session.get(model.ResearchRun, run_id)
        return _run(row) if row else None

    def list_runs(
        self,
        *,
        status: str | None = None,
        claimed_by: str | None = None,
        generation_request_id: UUID | None = None,
        limit: int = 100,
    ) -> list[dto.ResearchRun]:
        """列出 research runs，并支持常用队列过滤条件。"""
        # 默认按 created_at 排序，便于观察队列尝试的时间顺序。
        stmt = sa.select(model.ResearchRun).order_by(model.ResearchRun.created_at).limit(limit)
        if status is not None:
            # status 用于查看 queued/running/completed/failed 子集。
            stmt = stmt.where(model.ResearchRun.status == status)
        if claimed_by is not None:
            # claimed_by 用于定位某个 worker 当前或历史持有的任务。
            stmt = stmt.where(model.ResearchRun.claimed_by == claimed_by)
        if generation_request_id is not None:
            # generation_request_id 用于查看同一请求的重试链。
            stmt = stmt.where(model.ResearchRun.generation_request_id == generation_request_id)
        return [_run(row) for row in self.session.scalars(stmt)]

    def get_latest_run_for_request(
        self,
        generation_request_id: UUID,
    ) -> dto.ResearchRun | None:
        """Read the newest run for a generation request without list pagination."""
        stmt = (
            sa.select(model.ResearchRun)
            .where(model.ResearchRun.generation_request_id == generation_request_id)
            .order_by(model.ResearchRun.created_at.desc(), model.ResearchRun.attempt.desc())
            .limit(1)
        )
        row = self.session.scalar(stmt)
        return _run(row) if row else None

    def get_latest_completed_run_for_request(
        self,
        generation_request_id: UUID,
    ) -> dto.ResearchRun | None:
        """Read the newest completed run whose sources/findings are displayable."""
        stmt = (
            sa.select(model.ResearchRun)
            .where(
                model.ResearchRun.generation_request_id == generation_request_id,
                model.ResearchRun.status == "completed",
            )
            .order_by(
                model.ResearchRun.finished_at.desc().nulls_last(),
                model.ResearchRun.created_at.desc(),
                model.ResearchRun.attempt.desc(),
            )
            .limit(1)
        )
        row = self.session.scalar(stmt)
        return _run(row) if row else None

    def list_runs_with_category(
        self,
        *,
        status: str | None = None,
        claimed_by: str | None = None,
        generation_request_id: UUID | None = None,
        limit: int = 100,
    ) -> list[tuple[dto.ResearchRun, str]]:
        """列出 research runs，并通过 SQL JOIN 一次性带出 category。"""
        # spec 10.7 要求 "joined with category"；用真实 JOIN 替代 N+1 查询，
        # 在 UI 频繁刷新时把数据库 round-trip 数量从 1+N 收敛到 1。
        stmt = (
            sa.select(model.ResearchRun, model.GenerationRequest.category)
            .join(
                model.GenerationRequest,
                model.ResearchRun.generation_request_id == model.GenerationRequest.id,
            )
            .order_by(model.ResearchRun.created_at)
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(model.ResearchRun.status == status)
        if claimed_by is not None:
            stmt = stmt.where(model.ResearchRun.claimed_by == claimed_by)
        if generation_request_id is not None:
            stmt = stmt.where(model.ResearchRun.generation_request_id == generation_request_id)
        return [(_run(row[0]), row[1]) for row in self.session.execute(stmt)]

    def list_sources(self, run_id: UUID) -> list[dto.ResearchSource]:
        """列出某个 run 采集到的 sources。"""
        # 按 fetched_at 和 id 排序，保证同一批结果重复读取顺序稳定。
        rows = self.session.scalars(
            sa.select(model.ResearchSource)
            .where(model.ResearchSource.research_run_id == run_id)
            .order_by(model.ResearchSource.fetched_at, model.ResearchSource.id)
        ).all()
        return [_source(row) for row in rows]

    def list_findings(self, run_id: UUID) -> list[dto.ResearchFinding]:
        """列出某个 run 提炼出的 findings。"""
        # 按 label 和 id 排序，方便 UI/CLI 展示和测试断言稳定。
        rows = self.session.scalars(
            sa.select(model.ResearchFinding)
            .where(model.ResearchFinding.research_run_id == run_id)
            .order_by(model.ResearchFinding.label, model.ResearchFinding.id)
        ).all()
        return [_finding(row) for row in rows]

    def queue_stats(self) -> dict[str, Any]:
        """统计 research queue 的状态概览。"""
        # spec 10.8: "Single query against research_runs with grouped aggregates."
        # 用 FILTER 把四个 status 计数 + oldest_queued + near_expiry 合到一行，
        # 避免顺序触发三次 round-trip。array_agg(... ORDER BY ...) 保留按 lease
        # 到期时间排序，跟旧实现的顺序一致。
        run = model.ResearchRun

        queued_filter = run.status == "queued"
        running_filter = run.status == "running"
        completed_filter = run.status == "completed"
        failed_filter = run.status == "failed"
        near_expiry_filter = sa.and_(
            running_filter,
            run.lease_expires_at < sa.func.now() + sa.text("interval '60 seconds'"),
        )

        row = self.session.execute(
            sa.select(
                sa.func.count().filter(queued_filter).label("queued"),
                sa.func.count().filter(running_filter).label("running"),
                sa.func.count().filter(completed_filter).label("completed"),
                sa.func.count().filter(failed_filter).label("failed"),
                sa.func.extract(
                    "epoch",
                    sa.func.now() - sa.func.min(run.created_at).filter(queued_filter),
                ).label("oldest_queued_age_seconds"),
                sa.func.array_agg(run.id)
                .filter(near_expiry_filter)
                .label("near_expiry"),
            )
        ).one()

        oldest_age = (
            max(0.0, float(row.oldest_queued_age_seconds))
            if row.oldest_queued_age_seconds is not None
            else None
        )

        # array_agg 在没有匹配行时返回 None，统一成空 list。psycopg 已经把 PG
        # uuid[] 解码成 list[UUID]，无需再次转换。
        near_uuids: list[UUID] = list(row.near_expiry or [])

        return {
            "queued": int(row.queued or 0),
            "running": int(row.running or 0),
            "completed": int(row.completed or 0),
            "failed": int(row.failed or 0),
            "oldest_queued_age_seconds": oldest_age,
            "runs_near_lease_expiry": near_uuids,
        }

    def create_generation_request(
        self,
        *,
        category: str,
        topic: str,
        target_count: int,
        difficulty_distribution: Mapping[str, int],
        seed_urls: Sequence[str] = (),
        max_attempts: int = 3,
        runtime_constraints: Mapping[str, Any] | None = None,
        status: str = "draft",
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dto.GenerationRequest:
        """创建 generation request，但不提交事务。"""
        # category 不是 Python 常量，必须从数据库 lookup table 动态读取允许值。
        allowed_codes = [cat.code for cat in self.list_categories()]
        validate_category(category, allowed_codes)
        # difficulty_distribution 属于业务约束，写入前先在 domain 层校验。
        validate_distribution(target_count, difficulty_distribution)
        if max_attempts <= 0:
            raise ResearchValidationError(f"max_attempts must be positive, got {max_attempts}")
        # JSONB 字段统一复制成普通 dict/list，避免持久化外部可变对象引用。
        row = model.GenerationRequest(
            id=uuid4(),
            category=category,
            topic=topic,
            target_count=target_count,
            difficulty_distribution=dict(difficulty_distribution),
            runtime_constraints=dict(runtime_constraints or {}),
            seed_urls=list(seed_urls),
            max_attempts=max_attempts,
            status=status,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        self.session.add(row)
        # flush 让数据库执行约束检查并生成 server default 字段。
        self.session.flush()
        # refresh 读取 created_at/updated_at 等数据库默认值后再转 DTO。
        self.session.refresh(row)
        return _generation_request(row)

    def create_run(
        self,
        *,
        generation_request_id: UUID,
        parent_run_id: UUID | None = None,
        attempt: int = 1,
        status: str = "queued",
    ) -> dto.ResearchRun:
        """创建 research run 队列记录，但不提交事务。"""
        # attempt 从 1 开始，0 或负数会破坏重试链语义。
        if attempt <= 0:
            raise ResearchValidationError(f"attempt must be positive, got {attempt}")
        # parent_run_id 用于把失败重试串成可审计的链。
        row = model.ResearchRun(
            id=uuid4(),
            generation_request_id=generation_request_id,
            parent_run_id=parent_run_id,
            attempt=attempt,
            status=status,
        )
        self.session.add(row)
        # flush 提前触发 FK/unique/check 约束，调用方事务仍可统一回滚。
        self.session.flush()
        self.session.refresh(row)
        return _run(row)

    def add_source(
        self,
        run_id: UUID,
        *,
        url: str,
        title: str,
        summary: str,
        content_hash: str,
        fetched_at: datetime,
        raw_text_path: str | None = None,
    ) -> dto.ResearchSource:
        """为某个 research run 添加 source 元数据。"""
        # raw_text 本体放文件系统；数据库只保存 raw_text_path 和内容摘要。
        row = model.ResearchSource(
            id=uuid4(),
            research_run_id=run_id,
            url=url,
            title=title,
            summary=summary,
            content_hash=content_hash,
            fetched_at=fetched_at,
            raw_text_path=raw_text_path,
        )
        self.session.add(row)
        # 立即 flush 以拿到 id，后续 finding join table 需要引用 source_id。
        self.session.flush()
        return _source(row)

    def create_finding(
        self,
        run_id: UUID,
        *,
        kind: str,
        label: str,
        summary: str,
        source_ids: Sequence[UUID],
    ) -> dto.ResearchFinding:
        """创建 finding 及其 source 引用关系。"""
        # 先校验 kind 和 source_ids 非空/不重复，避免写入孤立 finding。
        validate_finding(kind, source_ids)
        # 一次查询取回所有 source 的 run 归属，用于检测缺失和跨 run 引用。
        rows = self.session.execute(
            sa.select(model.ResearchSource.id, model.ResearchSource.research_run_id).where(
                model.ResearchSource.id.in_(source_ids)
            )
        ).all()
        found = {row.id: row.research_run_id for row in rows}
        # 显式报 missing，而不是依赖数据库 FK 抛低层 IntegrityError。
        missing = [source_id for source_id in source_ids if source_id not in found]
        # finding 只能引用同一个 run 捕获的 source，防止跨请求污染证据链。
        wrong_run = [source_id for source_id, found_run_id in found.items() if found_run_id != run_id]
        if missing:
            raise ResearchValidationError(f"source_id(s) do not exist: {missing}")
        if wrong_run:
            raise ResearchValidationError(f"source_id(s) do not belong to run {run_id}: {wrong_run}")

        finding = model.ResearchFinding(
            id=uuid4(),
            research_run_id=run_id,
            kind=kind,
            label=label,
            summary=summary,
        )
        self.session.add(finding)
        # finding 和 join rows 在同一 session/事务中写入，调用方失败时整体回滚。
        for source_id in source_ids:
            self.session.add(
                model.ResearchFindingSource(
                    finding_id=finding.id,
                    source_id=source_id,
                )
            )
        self.session.flush()
        return _finding(finding)

    def get_binding(self, role: str) -> dto.HermesProfileBinding | None:
        """按 role 读取 Hermes profile binding。"""
        # role 是 hermes_profile_bindings 的主键，直接主键读取。
        row = self.session.get(model.HermesProfileBinding, role)
        return _binding(row) if row else None

    def list_bindings(self) -> list[dto.HermesProfileBinding]:
        """列出所有 Hermes profile bindings。"""
        # 按 role 排序保证运营输出稳定。
        rows = self.session.scalars(
            sa.select(model.HermesProfileBinding).order_by(model.HermesProfileBinding.role)
        ).all()
        return [_binding(row) for row in rows]

    def upsert_binding(
        self,
        role: str,
        profile_name: str,
        description: str | None = None,
    ) -> dto.HermesProfileBinding:
        """新增或更新某个 role 对应的 Hermes profile binding。"""
        # 先校验 role 已在 agent_roles 中注册，避免 FK 错误暴露给调用方。
        self._require_role(role)
        now = _utcnow()
        row = self.session.get(model.HermesProfileBinding, role)
        if row is None:
            # 首次绑定默认启用；operator 可后续显式 disable。
            row = model.HermesProfileBinding(
                role=role,
                profile_name=profile_name,
                description=description,
                status="enabled",
                updated_at=now,
            )
            self.session.add(row)
        else:
            # upsert 不改变 last_used_*，只更新配置值和 updated_at。
            row.profile_name = profile_name
            row.description = description
            row.updated_at = now
        self.session.flush()
        self.session.refresh(row)
        return _binding(row)

    def set_binding_status(self, role: str, status: str) -> dto.HermesProfileBinding:
        """启用或禁用某个 Hermes profile binding。"""
        # status 不是数据库 enum，这里用 domain 常量提前校验。
        if status not in dto.BindingStatus:
            raise ResearchValidationError(
                f"binding status {status!r} is not allowed; allowed: {list(dto.BindingStatus)}"
            )
        row = self.session.get(model.HermesProfileBinding, role)
        if row is None:
            raise ResearchValidationError(f"binding role {role!r} does not exist")
        # 幂等设置同一状态也会刷新 updated_at，便于审计最近操作。
        row.status = status
        row.updated_at = _utcnow()
        self.session.flush()
        self.session.refresh(row)
        return _binding(row)

    def touch_binding(
        self,
        role: str,
        *,
        last_used_at: datetime,
        last_used_run_id: UUID,
    ) -> None:
        """记录某个 binding 最近一次被 research run 使用。"""
        # 执行成功后由 service 调用；不会改变 profile_name/status 配置。
        row = self.session.get(model.HermesProfileBinding, role)
        if row is None:
            raise ResearchValidationError(f"binding role {role!r} does not exist")
        row.last_used_at = last_used_at
        row.last_used_run_id = last_used_run_id
        row.updated_at = _utcnow()
        self.session.flush()

    def _require_role(self, role: str) -> None:
        """确认 agent role 已存在。"""
        # 只查询布尔值，避免把完整 AgentRole row 加载进 session。
        exists = self.session.scalar(sa.select(sa.literal(True)).where(model.AgentRole.code == role))
        if not exists:
            raise ResearchValidationError(f"agent role {role!r} does not exist")


def _utcnow() -> datetime:
    """返回带 UTC 时区的当前时间。"""
    return datetime.now(timezone.utc)


def _category(row: model.ChallengeCategory) -> dto.ChallengeCategory:
    """把 ChallengeCategory ORM row 转成 DTO。"""
    return dto.ChallengeCategory(
        code=row.code,
        display_name=row.display_name,
        description=row.description,
    )


def _binding(row: model.HermesProfileBinding) -> dto.HermesProfileBinding:
    """把 HermesProfileBinding ORM row 转成 DTO。"""
    return dto.HermesProfileBinding(
        role=row.role,
        profile_name=row.profile_name,
        description=row.description,
        status=row.status,
        last_used_at=row.last_used_at,
        last_used_run_id=row.last_used_run_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _generation_request(row: model.GenerationRequest) -> dto.GenerationRequest:
    """把 GenerationRequest ORM row 转成 DTO。"""
    # JSONB 字段复制成普通 dict/list，避免 DTO 暴露 ORM 内部可变状态。
    return dto.GenerationRequest(
        id=row.id,
        category=row.category,
        topic=row.topic,
        target_count=row.target_count,
        difficulty_distribution=dict(row.difficulty_distribution),
        runtime_constraints=dict(row.runtime_constraints),
        seed_urls=tuple(row.seed_urls),
        max_attempts=row.max_attempts,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run(row: model.ResearchRun) -> dto.ResearchRun:
    """把 ResearchRun ORM row 转成 DTO。"""
    # 普通查询场景没有 was_retried 分支信息，统一置为 None。
    return dto.ResearchRun(
        id=row.id,
        generation_request_id=row.generation_request_id,
        parent_run_id=row.parent_run_id,
        attempt=row.attempt,
        status=row.status,
        claimed_by=row.claimed_by,
        claim_token=row.claim_token,
        claimed_at=row.claimed_at,
        heartbeat_at=row.heartbeat_at,
        lease_expires_at=row.lease_expires_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        last_error=row.last_error,
        hermes_log_path=row.hermes_log_path,
        profile_name_used=row.profile_name_used,
        created_at=row.created_at,
        was_retried=None,
    )


def _source(row: model.ResearchSource) -> dto.ResearchSource:
    """把 ResearchSource ORM row 转成 DTO。"""
    return dto.ResearchSource(
        id=row.id,
        research_run_id=row.research_run_id,
        url=row.url,
        title=row.title,
        summary=row.summary,
        content_hash=row.content_hash,
        fetched_at=row.fetched_at,
        raw_text_path=row.raw_text_path,
    )


def _finding(row: model.ResearchFinding) -> dto.ResearchFinding:
    """把 ResearchFinding ORM row 转成 DTO。"""
    return dto.ResearchFinding(
        id=row.id,
        research_run_id=row.research_run_id,
        kind=row.kind,
        label=row.label,
        summary=row.summary,
    )
