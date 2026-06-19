"""research run 的短事务队列操作。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import sqlalchemy as sa

from domain import research as dto
from domain.research_validators import ResearchValidationError
from persistence.models import research as model
from persistence.repositories import ResearchRepository
from persistence.session import SessionFactory, transaction


class _RowcountResult(Protocol):
    rowcount: int


class StaleClaimError(RuntimeError):
    """token-fenced 状态转换不再持有 run 时抛出。"""


class ResearchAttemptError(RuntimeError):
    """持久化 attempt 状态违反重试合同时抛出。"""


class ResearchJobService:
    """负责 research queue 状态变化和事务边界。"""

    def __init__(self, repository_factory: SessionFactory | None = None) -> None:
        # service 自己不持有长期 session；每个公开写操作都会打开一个短事务。
        self.repository_factory = repository_factory

    def submit_request(
        self,
        category: str,
        topic: str,
        target_count: int,
        difficulty_distribution: Mapping[str, int],
        seed_urls: Sequence[str] = (),
        max_attempts: int = 3,
        runtime_constraints: Mapping[str, Any] | None = None,
    ) -> tuple[dto.GenerationRequest, dto.ResearchRun]:
        """提交一个新的 research request，并创建对应的第一条 run 记录。"""
        with transaction(factory=self.repository_factory) as session:
            # 同一个事务里创建 request 和首个 queued run，避免出现只有 request 没有任务的半状态。
            repo = ResearchRepository(session)
            request = repo.create_generation_request(
                category=category,
                topic=topic,
                target_count=target_count,
                difficulty_distribution=difficulty_distribution,
                seed_urls=seed_urls,
                max_attempts=max_attempts,
                runtime_constraints=runtime_constraints,
                status="draft",
            )
            run = repo.create_run(generation_request_id=request.id, attempt=1, status="queued")
            # 返回的是 DTO；事务提交后调用方不会拿到仍绑定 session 的 ORM 对象。
            return request, run

    def claim_next_run(
        self,
        agent_id: str,
        lease_seconds: int,
        *,
        generation_request_id: UUID | None = None,
        expired_recovery_limit: int = 16,
    ) -> dto.ResearchRun | None:
        """恢复过期 run，并 claim 最老的一条 queued run。"""
        # lease 和恢复批量上限必须为正，否则队列状态机没有明确语义。
        if lease_seconds <= 0:
            raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")
        if expired_recovery_limit <= 0:
            raise ValueError(f"expired_recovery_limit must be positive, got {expired_recovery_limit}")

        with transaction(factory=self.repository_factory) as session:
            now = _utcnow()
            # 先惰性恢复过期 running run。skip_locked 让多个 worker 不会互相等待同一批过期行。
            expired_rows = session.scalars(
                sa.select(model.ResearchRun)
                .where(
                    model.ResearchRun.status == "running",
                    model.ResearchRun.lease_expires_at < now,
                )
                .order_by(model.ResearchRun.lease_expires_at, model.ResearchRun.created_at)
                .limit(expired_recovery_limit)
                .with_for_update(skip_locked=True)
            ).all()

            for run in expired_rows:
                # 过期恢复和普通失败共用同一套失败/重试逻辑，确保 request.status 同步规则一致。
                self._apply_run_failed(session, run, "lease expired", log_path=None)

            # 再 claim 最早创建的 queued run。FOR UPDATE SKIP LOCKED 保证并发 worker 拿到不同任务。
            queued_stmt = (
                sa.select(model.ResearchRun)
                .where(model.ResearchRun.status == "queued")
                .order_by(model.ResearchRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if generation_request_id is not None:
                queued_stmt = queued_stmt.where(
                    model.ResearchRun.generation_request_id == generation_request_id
                )
            queued = session.scalars(queued_stmt).first()
            if queued is None:
                return None

            # claim 时生成新的 fencing token；后续 heartbeat/终态写入必须带上这个 token。
            queued.status = "running"
            queued.claimed_by = agent_id
            queued.claim_token = uuid4()
            queued.claimed_at = now
            queued.heartbeat_at = now
            queued.lease_expires_at = now + timedelta(seconds=lease_seconds)
            request = _get_request(session, queued.generation_request_id)
            request.status = "researching"
            request.updated_at = now
            session.flush()
            # flush 后 DTO 中能看到 claim_token、claimed_at、lease_expires_at 等新值。
            return _run_dto(queued)

    def heartbeat(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        lease_seconds: int,
    ) -> bool:
        """续租当前 worker 持有的 running run。"""
        if lease_seconds <= 0:
            raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")
        now = _utcnow()
        with transaction(factory=self.repository_factory) as session:
            # heartbeat 不加 SELECT FOR UPDATE，直接用 owner/token/status 条件做原子 UPDATE。
            result = cast(
                _RowcountResult,
                session.execute(
                    sa.update(model.ResearchRun)
                    .where(
                        model.ResearchRun.id == run_id,
                        model.ResearchRun.status == "running",
                        model.ResearchRun.claimed_by == agent_id,
                        model.ResearchRun.claim_token == claim_token,
                    )
                    .values(
                        heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=lease_seconds),
                    )
                ),
            )
            # rowcount 为 0 表示已经失去 claim、run 已终态、或 token/worker 不匹配。
            return result.rowcount == 1

    def set_profile_name_used(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        profile_name: str,
    ) -> dto.ResearchRun:
        """记录本次运行实际使用的 Hermes profile。"""
        if not profile_name:
            raise ResearchValidationError("profile_name is required")
        with transaction(factory=self.repository_factory) as session:
            # profile_name_used 是取证字段，必须在仍持有 claim 时才能写。
            run = self._get_owned_running_run(session, run_id, agent_id, claim_token)
            run.profile_name_used = profile_name
            session.flush()
            return _run_dto(run)

    def mark_run_started(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
    ) -> dto.ResearchRun:
        """记录 Hermes 子进程开始执行的时间。"""
        with transaction(factory=self.repository_factory) as session:
            # started_at 只记录第一次真正启动 Hermes 的时间，重复调用不会覆盖原值。
            run = self._get_owned_running_run(session, run_id, agent_id, claim_token)
            if run.started_at is None:
                run.started_at = _utcnow()
            session.flush()
            return _run_dto(run)

    def mark_run_completed(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        *,
        log_path: str | Path,
    ) -> dto.ResearchRun:
        """将当前 run 标记为 completed，并同步父 request 状态。"""
        with transaction(factory=self.repository_factory) as session:
            # 所有终态转换都先重新确认 owner/token，防止过期 worker 写入旧 run。
            run = self._get_owned_running_run(session, run_id, agent_id, claim_token)
            self._apply_run_completed(session, run, log_path)
            session.flush()
            return _run_dto(run)

    def mark_run_failed(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        last_error: str,
        *,
        log_path: str | Path | None = None,
    ) -> dto.ResearchRun:
        """将当前 run 标记为 failed，并按 max_attempts 自动创建 retry run。"""
        with transaction(factory=self.repository_factory) as session:
            # 失败路径同样受 token fencing 保护；调用方不能决定是否 retry。
            run = self._get_owned_running_run(session, run_id, agent_id, claim_token)
            was_retried = self._apply_run_failed(session, run, last_error, log_path=log_path)
            session.flush()
            return _run_dto(run, was_retried=was_retried)

    def complete_run_with_results(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        *,
        sources: Sequence[Mapping[str, Any]],
        findings: Sequence[Mapping[str, Any]],
        binding_role: str,
        log_path: str | Path,
    ) -> dto.ResearchRun:
        """原子保存 sources/findings，并把 run 标记为 completed。"""
        with transaction(factory=self.repository_factory) as session:
            # 成功路径必须先确认 claim 仍然属于当前 worker，避免 stale Hermes 输出落库。
            run = self._get_owned_running_run(session, run_id, agent_id, claim_token)
            repo = ResearchRepository(session)
            source_ids: list[UUID] = []
            # 先保存所有 sources，收集数据库生成的 source_id，供 findings 通过 source_indices 引用。
            for source in sources:
                saved = repo.add_source(
                    run_id,
                    url=_required_str(source, "url"),
                    title=_required_str(source, "title"),
                    summary=_required_str(source, "summary"),
                    content_hash=_required_str(source, "content_hash"),
                    fetched_at=_coerce_datetime(source.get("fetched_at")),
                    raw_text_path=_optional_str(source.get("raw_text_path")),
                )
                source_ids.append(saved.id)

            # 再保存 findings。source_indices 会被转换成同一事务内刚创建的 source_id。
            for finding in findings:
                finding_source_ids = _finding_source_ids(finding, source_ids)
                repo.create_finding(
                    run_id,
                    kind=_required_str(finding, "kind"),
                    label=_required_str(finding, "label"),
                    summary=_required_str(finding, "summary"),
                    source_ids=finding_source_ids,
                )

            # 成功落库后更新 profile binding 的最近使用记录，并在同一事务内写 completed。
            repo.touch_binding(binding_role, last_used_at=_utcnow(), last_used_run_id=run_id)
            self._apply_run_completed(session, run, log_path)
            session.flush()
            return _run_dto(run)

    def get_binding(self, role: str) -> dto.HermesProfileBinding | None:
        """读取某个 agent role 对应的 Hermes profile binding。"""
        with transaction(factory=self.repository_factory) as session:
            # 读操作也走短 session，避免 executor 持有跨线程/跨子进程的数据库连接。
            return ResearchRepository(session).get_binding(role)

    def _get_owned_running_run(
        self,
        session,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
    ) -> model.ResearchRun:
        """读取当前 worker 仍然拥有的 running run，并加锁。"""
        # 这个 helper 是所有 token-fenced 写入的共同入口；查不到就说明 claim 已失效。
        run = session.scalars(
            sa.select(model.ResearchRun)
            .where(
                model.ResearchRun.id == run_id,
                model.ResearchRun.status == "running",
                model.ResearchRun.claimed_by == agent_id,
                model.ResearchRun.claim_token == claim_token,
            )
            .with_for_update()
        ).first()
        if run is None:
            # typed exception 让 executor 能区分“租约丢失”和普通业务校验失败。
            raise StaleClaimError(f"run {run_id} is no longer running under this claim")
        return run

    def _apply_run_completed(
        self,
        session,
        run: model.ResearchRun,
        log_path: str | Path,
    ) -> None:
        """应用 completed 状态变更；调用方负责事务和 token fencing。"""
        now = _utcnow()
        # completed run 必须有 finished_at，且清空 last_error。
        run.status = "completed"
        run.finished_at = now
        run.last_error = None
        run.hermes_log_path = str(log_path)
        # request.status 是 run 状态的 denormalized view，终态转换时同步更新。
        request = _get_request(session, run.generation_request_id)
        request.status = "researched"
        request.updated_at = now

    def _apply_run_failed(
        self,
        session,
        run: model.ResearchRun,
        last_error: str,
        *,
        log_path: str | Path | None,
    ) -> bool:
        """标记 run 失败，并根据重试合同决定是否创建新的 retry run。"""
        if not last_error:
            raise ResearchValidationError("last_error is required when marking a run failed")
        # max_attempts 属于父 request 的 operator intent，所以失败分支要读取 request。
        request = _get_request(session, run.generation_request_id)
        if run.attempt > request.max_attempts:
            # 正常流程不会出现 attempt > max_attempts；这里防御直接 SQL 篡改或迁移错误。
            raise ResearchAttemptError(
                f"run attempt {run.attempt} exceeds max_attempts {request.max_attempts}"
            )

        now = _utcnow()
        # 失败 attempt 保留原 claim 信息作为审计证据，只写终态字段和错误原因。
        run.status = "failed"
        run.finished_at = now
        run.last_error = last_error
        run.hermes_log_path = str(log_path) if log_path is not None else run.hermes_log_path

        if run.attempt < request.max_attempts:
            # 每次 retry 都是新 run row，旧 failed row 保持为该次尝试的审计记录。
            retry = model.ResearchRun(
                id=uuid4(),
                generation_request_id=run.generation_request_id,
                parent_run_id=run.id,
                attempt=run.attempt + 1,
                status="queued",
            )
            session.add(retry)
            # 还有 retry 可执行时，父 request 仍处于 researching，执行过应该就是 ing 状态
            request.status = "researching"
            was_retried = True
        else:
            # 达到 max_attempts 后不再创建新 run，父 request 才进入 failed。
            request.status = "failed"
            was_retried = False
        request.updated_at = now
        return was_retried


def _get_request(session, request_id: UUID) -> model.GenerationRequest:
    """按主键读取 generation request，缺失时转成业务校验错误。"""
    request = session.get(model.GenerationRequest, request_id)
    if request is None:
        raise ResearchValidationError(f"generation_request {request_id} does not exist")
    return request


def _utcnow() -> datetime:
    """返回带 UTC 时区的当前时间。"""
    return datetime.now(timezone.utc)


def _coerce_datetime(value: Any) -> datetime:
    """把可选 fetched_at 转成 datetime，缺省时使用当前 UTC 时间。"""
    if value is None:
        return _utcnow()
    if isinstance(value, datetime):
        return value
    raise ResearchValidationError(f"expected datetime for fetched_at, got {type(value).__name__}")


def _optional_str(value: Any) -> str | None:
    """校验可选字符串字段。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResearchValidationError(
            f"expected string or None, got {type(value).__name__}"
        )
    return value


def _required_str(payload: Mapping[str, Any], field: str) -> str:
    """从 payload 读取必填非空字符串字段。"""
    if field not in payload:
        raise ResearchValidationError(f"missing required field {field!r}")
    value = payload[field]
    if not isinstance(value, str) or not value:
        raise ResearchValidationError(
            f"field {field!r} must be a non-empty string, got {value!r}"
        )
    return value


def _finding_source_ids(finding: Mapping[str, Any], source_ids: Sequence[UUID]) -> list[UUID]:
    """把 finding 的 source 引用解析成数据库 source_id 列表。"""
    has_ids = "source_ids" in finding
    has_indices = "source_indices" in finding
    if has_ids and has_indices:
        # 防止调用方同时传两种引用形式导致歧义。
        raise ResearchValidationError(
            "finding must include either source_ids or source_indices, not both"
        )
    if has_ids:
        # 测试和少数内部调用可直接传 source_ids；正常 Hermes 输出使用 source_indices。
        return list(finding["source_ids"])
    if not has_indices:
        raise ResearchValidationError("finding must include source_indices or source_ids")
    indices = finding["source_indices"]
    # 显式要求 list/tuple，避免字符串按字符迭代导致错误信息难以理解。
    if not isinstance(indices, (list, tuple)):
        raise ResearchValidationError(
            f"source_indices must be a list or tuple, got {type(indices).__name__}"
        )
    resolved: list[UUID] = []
    for index in indices:
        # bool 是 int 的子类，必须显式拒绝 True/False。
        if not isinstance(index, int) or isinstance(index, bool):
            raise ResearchValidationError(f"source_indices must contain integers, got {index!r}")
        if index < 0:
            raise ResearchValidationError(f"source index {index} is out of range")
        try:
            # 0-based index 映射到本次事务刚插入的 source_ids。
            resolved.append(source_ids[index])
        except IndexError as exc:
            raise ResearchValidationError(f"source index {index} is out of range") from exc
    return resolved


def _run_dto(row: model.ResearchRun, *, was_retried: bool | None = None) -> dto.ResearchRun:
    """把 ResearchRun ORM row 转成 DTO。"""
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
        was_retried=was_retried,
    )
