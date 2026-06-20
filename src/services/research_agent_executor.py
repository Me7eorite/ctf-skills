"""Research Agent 单次运行编排。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from core.paths import ProjectPaths
from domain import research as dto
from domain.research_validators import (
    ResearchValidationError,
    apply_research_quality_gate,
    extract_terminal_json_object,
)
from hermes.process import HermesProcessResult, profile_exists
from hermes.prompt import render_research_prompt
from hermes.research import invoke_research_agent
from persistence.repositories import ResearchRepository
from persistence.session import SessionFactory, transaction
from services.research_job_service import ResearchJobService, StaleClaimError

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

        if res_data.returncode != 0:
            self._mark_failed_if_owned(
                run,
                agent_id,
                f"Hermes exited with {res_data.returncode}",
                log_path,
            )
            return

        try:
            source_payloads, finding_payloads = _parse_research_output(
                res_data.stdout,
                paths=self.paths,
                run_id=run.id,
                target_count=generation_request.target_count,
            )
        except ResearchValidationError as exc:
            self._mark_failed_if_owned(run, agent_id, str(exc), log_path)
            return

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
    """解析 Hermes stdout，并把可选 raw_text 落到磁盘。"""
    # 中文注释：下游 repository 只存 raw_text_path，完整原文留在 work/research/sources。
    res_data = extract_terminal_json_object(stdout_text)
    if res_data is None:
        raise ResearchValidationError("unparseable_output:no_terminal_json_object")
    ok, error = apply_research_quality_gate(res_data, target_count)
    if not ok:
        raise ResearchValidationError(error or "unparseable_output:quality_gate_failed")

    source_items = res_data.get("sources")
    finding_items = res_data.get("findings")
    if not isinstance(source_items, list):
        raise ResearchValidationError("research output field 'sources' must be a list")
    if not isinstance(finding_items, list):
        raise ResearchValidationError("research output field 'findings' must be a list")

    source_payloads = [
        _normalize_source_payload(source_item, paths=paths, run_id=run_id, source_index=source_index)
        for source_index, source_item in enumerate(source_items)
    ]
    finding_payloads = [
        _normalize_finding_payload(finding_item, source_count=len(source_payloads))
        for finding_item in finding_items
    ]
    return source_payloads, finding_payloads


def _normalize_source_payload(
    source_item: Any,
    *,
    paths: ProjectPaths,
    run_id: UUID,
    source_index: int,
) -> dict[str, Any]:
    """规范化单个 source，并处理 raw_text 文件写入。"""
    # 中文注释：保留 Agent 输出字段，同时把 raw_text 从 payload 中替换成 raw_text_path。
    if not isinstance(source_item, Mapping):
        raise ResearchValidationError("each source must be a JSON object")
    source_payload = dict(source_item)
    for field_name in ("url", "title", "summary", "content_hash"):
        _required_text(source_payload, field_name, "source")
    raw_text = source_payload.pop("raw_text", None)
    if raw_text is not None:
        if not isinstance(raw_text, str):
            raise ResearchValidationError("source raw_text must be a string when present")
        staging_dir = paths.research_sources_staging / str(run_id)
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staging_dir / f"{source_index}.txt"
        staged_path.write_text(raw_text, encoding="utf-8")
        source_payload["raw_text_path"] = str(
            paths.research_sources / str(run_id) / f"{source_index}.txt"
        )
    return source_payload


def _normalize_finding_payload(finding_item: Any, *, source_count: int) -> dict[str, Any]:
    """规范化单个 finding。"""
    # 中文注释：在 parse 阶段提前拒绝缺字段和无效 source_indices，保留真实失败原因。
    if not isinstance(finding_item, Mapping):
        raise ResearchValidationError("each finding must be a JSON object")
    finding_payload = dict(finding_item)
    for field_name in ("kind", "label", "summary"):
        _required_text(finding_payload, field_name, "finding")
    source_indices = finding_payload.get("source_indices")
    if not isinstance(source_indices, list):
        raise ResearchValidationError("finding source_indices must be a list")
    if not source_indices:
        raise ResearchValidationError("finding source_indices must be non-empty")
    for source_index in source_indices:
        if not isinstance(source_index, int) or isinstance(source_index, bool):
            raise ResearchValidationError(
                f"finding source_indices must contain integers, got {source_index!r}"
            )
        if source_index < 0 or source_index >= source_count:
            raise ResearchValidationError(f"source index {source_index} is out of range")
    return finding_payload


def _required_text(payload: Mapping[str, Any], field_name: str, item_name: str) -> str:
    """读取必填文本字段。"""
    # 中文注释：Hermes 输出缺少必填字段时，在 parse 阶段给出明确诊断。
    field_value = payload.get(field_name)
    if not isinstance(field_value, str) or not field_value:
        raise ResearchValidationError(
            f"{item_name} field {field_name!r} must be a non-empty string"
        )
    return field_value
