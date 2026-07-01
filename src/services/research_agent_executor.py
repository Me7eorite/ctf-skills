"""Research Agent 单次运行编排。"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from core.paths import ProjectPaths
from domain import research as dto
from domain.research_validators import ResearchValidationError, minimum_research_findings
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
DEFAULT_FINALIZE_TIMEOUT_SECONDS = 180
FINALIZE_STDOUT_MAX_CHARS = 12000
ITERATION_BUDGET_MARKERS = (
    "Iteration budget exhausted",
    "Reached maximum iterations",
)

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

            prompt_text = _with_supplement_context(
                _with_previous_failure_context(
                    render_research_prompt(generation_request),
                    previous_error=self._previous_run_error(run),
                ),
                repository_factory=getattr(self.job_service, "repository_factory", None),
                run=run,
                request=generation_request,
            )
            res_data = self.hermes_invoke(
                prompt=prompt_text,
                profile_name=profile_name,
                log_path=log_path,
                timeout=hermes_timeout_seconds,
                paths=self.paths,
                cancel_event=lost_lease,
            )

            parse_result = self._parse_or_finalize(
                generation_request=generation_request,
                run_id=run.id,
                profile_name=profile_name,
                log_path=log_path,
                hermes_timeout_seconds=hermes_timeout_seconds,
                primary_result=res_data,
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

        if parse_result.error is not None:
            self._mark_failed_if_owned(run, agent_id, parse_result.error, log_path)
            return
        source_payloads = parse_result.sources
        finding_payloads = parse_result.findings

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

    def _parse_or_finalize(
        self,
        *,
        generation_request: dto.GenerationRequest,
        run_id: UUID,
        profile_name: str,
        log_path: Path,
        hermes_timeout_seconds: int,
        primary_result: HermesProcessResult,
        cancel_event: threading.Event,
    ) -> "_ResearchParseResult":
        primary = _parse_result_payload(
            primary_result.stdout,
            paths=self.paths,
            run_id=run_id,
            target_count=generation_request.target_count,
            category=generation_request.category,
        )
        if primary.error is None:
            return primary

        failure_reason = _classify_research_failure(primary_result, primary.error)
        if not _should_finalize_research_failure(primary_result, primary.error):
            return _ResearchParseResult(error=failure_reason)
        if primary_result.cancelled or cancel_event.is_set():
            return _ResearchParseResult(error=failure_reason)

        finalize_timeout = _finalize_timeout_seconds(hermes_timeout_seconds)
        finalize_log_path = log_path.with_name(log_path.name + ".finalize.log")
        finalize_prompt = _render_finalize_prompt(
            generation_request,
            failure_reason=failure_reason,
            stdout_text=primary_result.stdout,
        )
        LOGGER.warning(
            "research run %s primary output failed (%s); invoking finalize for %ss",
            run_id,
            failure_reason,
            finalize_timeout,
        )
        finalize_result = self.hermes_invoke(
            prompt=finalize_prompt,
            profile_name=profile_name,
            log_path=finalize_log_path,
            timeout=finalize_timeout,
            paths=self.paths,
            cancel_event=cancel_event,
        )
        if finalize_result.cancelled or cancel_event.is_set():
            return _ResearchParseResult(error=failure_reason)

        finalized = _parse_result_payload(
            finalize_result.stdout,
            paths=self.paths,
            run_id=run_id,
            target_count=generation_request.target_count,
            category=generation_request.category,
        )
        if finalized.error is None:
            if primary_result.returncode != 0:
                LOGGER.warning(
                    "Hermes primary exited with %s but finalize produced valid research output for run %s",
                    primary_result.returncode,
                    run_id,
                )
            return finalized

        finalize_reason = _classify_research_failure(finalize_result, finalized.error)
        return _ResearchParseResult(
            error=f"{failure_reason}; finalize_failed:{finalize_reason}"
        )

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

    def _previous_run_error(self, run: dto.ResearchRun) -> str | None:
        if run.parent_run_id is None:
            return None
        with transaction(factory=self.repository_factory) as session:
            previous = ResearchRepository(session).get_run(run.parent_run_id)
        if previous is None:
            return None
        return previous.last_error

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


class _ResearchParseResult:
    def __init__(
        self,
        *,
        sources: list[dict[str, Any]] | None = None,
        findings: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        self.sources = sources or []
        self.findings = findings or []
        self.error = error


def _parse_result_payload(
    stdout_text: str,
    *,
    paths: ProjectPaths,
    run_id: UUID,
    target_count: int,
    category: str | None,
) -> _ResearchParseResult:
    repaired_stdout = _repair_common_json_key_glitches(stdout_text)
    if repaired_stdout != stdout_text:
        repaired = _try_parse_result_payload(
            repaired_stdout,
            paths=paths,
            run_id=run_id,
            target_count=target_count,
            category=category,
        )
        if repaired.error is None:
            LOGGER.warning("recovered research stdout after repairing duplicated JSON key quote")
            return repaired

    parsed = _try_parse_result_payload(
        stdout_text,
        paths=paths,
        run_id=run_id,
        target_count=target_count,
        category=category,
    )
    if parsed.error != "unparseable_output:no_terminal_json_object":
        return parsed
    return parsed


def _try_parse_result_payload(
    stdout_text: str,
    *,
    paths: ProjectPaths,
    run_id: UUID,
    target_count: int,
    category: str | None,
) -> _ResearchParseResult:
    try:
        parsed = parse_research_output(
            stdout_text,
            target_count=target_count,
            category=category,
        )
        sources, findings = materialize_research_raw_text(
            parsed,
            paths=paths,
            run_id=run_id,
        )
        return _ResearchParseResult(sources=sources, findings=findings)
    except ResearchValidationError as exc:
        error = str(exc)
        if _is_supplementable_quality_error(error):
            try:
                parsed = parse_research_output(
                    stdout_text,
                    target_count=target_count,
                    category=category,
                    enforce_quality=False,
                )
                sources, findings = materialize_research_raw_text(
                    parsed,
                    paths=paths,
                    run_id=run_id,
                )
            except ResearchValidationError:
                return _ResearchParseResult(error=error)
            LOGGER.warning(
                "persisting partial research output despite quality gate failure: %s",
                error,
            )
            return _ResearchParseResult(sources=sources, findings=findings)
        return _ResearchParseResult(error=error)


def _is_supplementable_quality_error(error: str) -> bool:
    return error.startswith(("insufficient_findings:", "insufficient_diversity:"))


def _repair_common_json_key_glitches(stdout_text: str) -> str:
    repaired = stdout_text
    for key in ("sources", "findings"):
        repaired = repaired.replace(f'""{key}"', f'"{key}"')
    return repaired


def _classify_research_failure(
    result: HermesProcessResult,
    parse_error: str | None,
) -> str:
    stdout = result.stdout or ""
    if result.returncode != 0 and not stdout.strip():
        return f"Hermes exited with {result.returncode}:empty_stdout"
    if result.returncode == 124:
        if not stdout.strip():
            return "hermes_timeout:empty_stdout"
        return f"hermes_timeout:{parse_error or 'no_valid_json'}"
    if not stdout.strip():
        return "empty_stdout"
    if any(marker in stdout for marker in ITERATION_BUDGET_MARKERS):
        return f"iteration_budget_exhausted:{parse_error or 'no_valid_json'}"
    return parse_error or f"Hermes exited with {result.returncode}"


def _should_finalize_research_failure(
    result: HermesProcessResult,
    parse_error: str | None,
) -> bool:
    stdout = result.stdout or ""
    if result.returncode != 0 or not stdout.strip():
        return True
    if any(marker in stdout for marker in ITERATION_BUDGET_MARKERS):
        return True
    return parse_error == "unparseable_output:no_terminal_json_object"


def _finalize_timeout_seconds(hermes_timeout_seconds: int) -> int:
    raw = os.environ.get("RESEARCH_FINALIZE_TIMEOUT_SECONDS")
    if raw:
        try:
            configured = int(raw)
        except ValueError:
            LOGGER.warning(
                "invalid RESEARCH_FINALIZE_TIMEOUT_SECONDS=%r; using default %s",
                raw,
                DEFAULT_FINALIZE_TIMEOUT_SECONDS,
            )
        else:
            if configured > 0:
                return configured
            LOGGER.warning(
                "RESEARCH_FINALIZE_TIMEOUT_SECONDS=%r must be positive; using default %s",
                raw,
                DEFAULT_FINALIZE_TIMEOUT_SECONDS,
            )
    return max(30, min(DEFAULT_FINALIZE_TIMEOUT_SECONDS, hermes_timeout_seconds))


def _render_finalize_prompt(
    request: dto.GenerationRequest,
    *,
    failure_reason: str,
    stdout_text: str,
) -> str:
    stdout_excerpt = stdout_text[-FINALIZE_STDOUT_MAX_CHARS:] if stdout_text else "(empty stdout)"
    return (
        "You are in FINALIZE-ONLY mode for a CTF research run that already spent its "
        "broad-search pass.\n"
        "Do not perform new web searches, open new pages, spawn subagents, or continue exploration.\n"
        "Hard stop: do not perform new web searches, open new pages, spawn subagents, "
        "ask clarifying questions, run commands, or continue exploration.\n"
        "Use only recoverable facts from the previous stdout excerpt below and your "
        "current conversation context. If the excerpt contains only meta-commentary "
        "or progress text, do not invent sources.\n\n"
        "Your only task is to emit exactly one valid JSON object matching the research schema: "
        "`sources` array and `findings` array.\n"
        "Output contract (strict):\n"
        "- Emit exactly one JSON object and nothing else.\n"
        "- The first non-whitespace character must be `{` and the last non-whitespace "
        "character must be `}`.\n"
        "- Do not write markdown, prose, code fences, explanations, apologies, "
        "analysis, or a sentence such as \"Let me build...\".\n"
        "- The object must contain exactly the two top-level arrays `sources` and "
        "`findings`.\n"
        "- Every source object must include non-empty string fields `url`, `title`, "
        "`summary`, and `content_hash`.\n"
        "- Every finding object must include non-empty string fields `kind`, `label`, "
        "`summary`, optional `technique_family`, and non-empty integer array "
        "`source_indices`.\n"
        "- Every `source_indices` value must be a 0-based index into `sources`.\n"
        "- If a relied-on source hash is unknown, use any stable lowercase "
        "64-character hex sha256-shaped placeholder; the host will normalize "
        "non-authoritative hashes later.\n"
        "- If no source can be recovered from the excerpt/context, return "
        "{\"sources\":[],\"findings\":[]} rather than prose.\n\n"
        f"Category: {request.category}\n"
        f"Topic: {request.topic}\n"
        f"Target challenge count: {request.target_count}\n"
        f"Previous failure reason: {failure_reason}\n\n"
        "Required JSON shape:\n"
        "{\n"
        "  \"sources\": [\n"
        "    {\"url\":\"https://example.com/source\",\"title\":\"Source title\","
        "\"summary\":\"What this source supports.\","
        "\"content_hash\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}\n"
        "  ],\n"
        "  \"findings\": [\n"
        "    {\"kind\":\"technique\",\"label\":\"Short finding label\","
        "\"technique_family\":\"other\","
        "\"summary\":\"A substantiated finding derived only from the recovered source.\","
        "\"source_indices\":[0]}\n"
        "  ]\n"
        "}\n\n"
        "Previous stdout excerpt:\n"
        "```text\n"
        f"{stdout_excerpt}\n"
        "```\n\n"
        "Return the JSON object now. Do not prefix it with any words."
    )


def _with_previous_failure_context(prompt: str, *, previous_error: str | None) -> str:
    if not previous_error:
        return prompt
    return (
        prompt
        + "\n\n## Previous attempt failure\n\n"
        + "The previous attempt for this same request failed before research results were persisted.\n"
        + f"Failure reason: `{previous_error}`\n"
        + "Keep the same broad search scope, but correct this delivery failure in the current run. "
        + "In particular, do not finish with empty stdout, progress-only stdout, or malformed JSON.\n"
    )


def _with_supplement_context(
    prompt: str,
    *,
    repository_factory: SessionFactory | None,
    run: dto.ResearchRun,
    request: dto.GenerationRequest,
) -> str:
    if run.parent_run_id is None:
        return prompt
    with transaction(factory=repository_factory) as session:
        repo = ResearchRepository(session)
        sources = repo.list_sources(run.parent_run_id)
        findings = repo.list_findings(run.parent_run_id)
    if not findings:
        return prompt

    minimum = minimum_research_findings(request.target_count)
    missing_minimum = max(0, minimum - len(findings))
    missing_target = max(0, request.target_count - len(findings))
    source_lines = [
        f"- [{idx}] {source.title}: {source.url}"
        for idx, source in enumerate(sources[:12])
    ]
    finding_lines = [
        f"- {finding.label} ({finding.kind}): {finding.summary}"
        for finding in findings[:20]
    ]
    return (
        prompt
        + "\n\n## Supplemental research context\n\n"
        + "This run is continuing a completed-but-underfilled research result. "
        + "Do not restart from scratch and do not discard useful previous findings.\n"
        + f"Existing findings: {len(findings)}. Minimum needed: {minimum}. "
        + f"Missing to minimum: {missing_minimum}. Missing to target count: {missing_target}.\n"
        + "Return one complete consolidated JSON object containing the existing usable findings "
        + "plus enough new distinct findings to meet the target count when possible. "
        + "Avoid duplicate labels, duplicate sub-techniques, and repeated source-only restatements.\n\n"
        + "Existing sources to preserve or reuse when still relevant:\n"
        + ("\n".join(source_lines) if source_lines else "- (none recorded)")
        + "\n\nExisting findings to preserve:\n"
        + "\n".join(finding_lines)
        + "\n"
    )
