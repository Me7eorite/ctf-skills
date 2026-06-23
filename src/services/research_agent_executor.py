"""Research Agent 单次运行编排。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from core.paths import ProjectPaths
from domain import research as dto
from domain.research_validators import ResearchValidationError
from hermes.process import HermesProcessResult, profile_exists
from hermes.prompt import render_research_prompt
from hermes.research import invoke_research_agent
from persistence.repositories import ResearchRepository
from persistence.session import SessionFactory, transaction
from services.research_job_service import ResearchJobService, StaleClaimError
from services.research_output import materialize_research_raw_text, parse_research_output

LOGGER = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 30.0
RESEARCH_BINDING_ROLE = "research"
DEFAULT_PROFILE_NAME = "default"

HermesInvoke = Callable[..., HermesProcessResult]


class ResearchAgentExecutor:
    """把已 claim 的 research run 执行到终态或安全放弃。"""

    def __init__(
        self,
        paths: ProjectPaths,
        repository_factory: SessionFactory | None = None,
        hermes_invoke: HermesInvoke = invoke_research_agent,
    ) -> None:
        self.paths = paths
        self.repository_factory = repository_factory
        self.hermes_invoke = hermes_invoke
        self.job_service = ResearchJobService(repository_factory)

    def execute(
        self,
        run: dto.ResearchRun,
        agent_id: str,
        lease_seconds: int,
        hermes_timeout_seconds: int,
    ) -> None:
        """执行一个已被当前 worker claim 的 research run。"""
        # 中文注释：没有 claim_token 就无法做 token-fenced 写入，直接放弃当前迭代。
        if run.claim_token is None:
            LOGGER.warning("research run %s has no claim_token; skipping", run.id)
            return

        log_path = self.paths.research_logs / f"{run.id}.log"
        try:
            profile_name = self._resolve_profile_name(run.id)
        except ResearchValidationError as exc:
            self._mark_failed_if_owned(run, agent_id, str(exc), log_path)
            return
        if not profile_exists(profile_name):
            self._mark_failed_if_owned(
                run,
                agent_id,
                f"Hermes profile {profile_name!r} does not exist",
                log_path,
            )
            return

        generation_request = self._load_generation_request(run.generation_request_id)
        if generation_request is None:
            self._mark_failed_if_owned(
                run,
                agent_id,
                f"generation_request {run.generation_request_id} does not exist",
                log_path,
            )
            return

        try:
            self.job_service.set_profile_name_used(
                run.id,
                agent_id,
                run.claim_token,
                profile_name,
            )
        except StaleClaimError:
            LOGGER.warning("lost claim before recording profile for run %s", run.id)
            return

        stop_event = threading.Event()
        lost_lease = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(run, agent_id, lease_seconds, stop_event, lost_lease),
            daemon=True,
        )
        heartbeat_thread.start()

        try:
            try:
                # 把 log_path 在 started 时写入数据库，过期清扫路径才能找到这份日志做救援。
                self.job_service.mark_run_started(
                    run.id, agent_id, run.claim_token, log_path=log_path,
                )
            except StaleClaimError:
                LOGGER.warning("lost claim before starting run %s", run.id)
                return

            prompt_text = render_research_prompt(generation_request)
            res_data = self.hermes_invoke(
                prompt=prompt_text,
                profile_name=profile_name,
                log_path=log_path,
                timeout=hermes_timeout_seconds,
                paths=self.paths,
                cancel_event=lost_lease,
            )
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=5)

        if lost_lease.is_set() or res_data.cancelled:
            LOGGER.warning(
                "discarding Hermes output for run %s after lease loss; claim_token=%s",
                run.id,
                run.claim_token,
            )
            return

        try:
            parsed = parse_research_output(
                res_data.stdout,
                target_count=generation_request.target_count,
                category=generation_request.category,
            )
            source_payloads, finding_payloads = materialize_research_raw_text(
                parsed,
                paths=self.paths,
                run_id=run.id,
            )
        except ResearchValidationError as exc:
            last_error = (
                f"Hermes exited with {res_data.returncode}"
                if res_data.returncode != 0
                else str(exc)
            )
            self._mark_failed_if_owned(run, agent_id, last_error, log_path)
            return

        if res_data.returncode != 0:
            LOGGER.warning(
                "Hermes exited with %s but produced valid research output for run %s",
                res_data.returncode,
                run.id,
            )

        try:
            self.job_service.complete_run_with_staged_results(
                run.id,
                agent_id,
                run.claim_token,
                sources=source_payloads,
                findings=finding_payloads,
                binding_role=RESEARCH_BINDING_ROLE,
                log_path=log_path,
                paths=self.paths,
            )
        except StaleClaimError:
            LOGGER.warning(
                "lost claim while completing run %s; claim_token=%s",
                run.id,
                run.claim_token,
            )
        except ResearchValidationError as exc:
            self._mark_failed_if_owned(run, agent_id, str(exc), log_path)

    def _resolve_profile_name(self, run_id: UUID) -> str:
        """解析 research role 绑定；缺失或禁用时回退到 default。"""
        # 中文注释：profile binding 属于数据库配置，executor 只读取并选择实际 profile。
        binding = self.job_service.get_binding(RESEARCH_BINDING_ROLE)
        if binding is None:
            raise ResearchValidationError("profile_not_bound")
        if binding.status != "enabled":
            raise ResearchValidationError(f"profile_disabled:{binding.profile_name}")
        return binding.profile_name

    def _load_generation_request(self, request_id: UUID) -> dto.GenerationRequest | None:
        """读取 run 对应的 generation request。"""
        # 中文注释：提示词必须从持久化 request 渲染，不能依赖提交进程的临时状态。
        with transaction(factory=self.repository_factory) as session:
            return ResearchRepository(session).get_generation_request(request_id)

    def _heartbeat_loop(
        self,
        run: dto.ResearchRun,
        agent_id: str,
        lease_seconds: int,
        stop_event: threading.Event,
        lost_lease: threading.Event,
    ) -> None:
        """后台续租循环，直到 stop_event 触发或租约丢失。"""
        # 中文注释：每次 heartbeat 都走独立短事务，避免跨线程共享 SQLAlchemy session。
        # 单次 DB 抖动不应该毁掉一整次 Hermes 运行——只有连续失败超过 lease 的 1/3
        # 心跳预算（默认 ~10 次）才宣告租约丢失。任何抛出的异常都计入失败计数。
        consecutive_failures = 0
        max_failures = max(
            1, int(lease_seconds / HEARTBEAT_INTERVAL_SECONDS / 3),
        )
        while not stop_event.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                ok = self.job_service.heartbeat(
                    run.id,
                    agent_id,
                    run.claim_token,
                    lease_seconds,
                )
            except Exception as exc:  # noqa: BLE001 — 心跳线程任何异常都计入失败
                LOGGER.warning(
                    "heartbeat error for run %s: %s", run.id, exc,
                )
                ok = False
            if ok:
                consecutive_failures = 0
                continue
            consecutive_failures += 1
            if consecutive_failures >= max_failures:
                lost_lease.set()
                LOGGER.warning(
                    "lost heartbeat lease for run %s after %d consecutive failures; claim_token=%s",
                    run.id,
                    consecutive_failures,
                    run.claim_token,
                )
                return

    def _mark_failed_if_owned(
        self,
        run: dto.ResearchRun,
        agent_id: str,
        last_error: str,
        log_path: Path,
    ) -> None:
        """在仍持有 claim 时把 run 标记失败；失去 claim 则静默跳过。"""
        # 中文注释：StaleClaimError 表示其他 worker 已接管或恢复，当前进程不能再写终态。
        if run.claim_token is None:
            return
        try:
            self.job_service.mark_run_failed(
                run.id,
                agent_id,
                run.claim_token,
                last_error,
                log_path=log_path,
            )
        except StaleClaimError:
            LOGGER.warning(
                "lost claim while failing run %s; claim_token=%s",
                run.id,
                run.claim_token,
            )


def _parse_research_output(
    stdout_text: str,
    *,
    paths: ProjectPaths,
    run_id: UUID,
    target_count: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compatibility wrapper for existing tests and callers."""
    parsed = parse_research_output(stdout_text, target_count=target_count)
    return materialize_research_raw_text(parsed, paths=paths, run_id=run_id)
