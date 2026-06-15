"""Hermes prompt rendering and shard execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.docker import image_exists as default_image_exists
from core.paths import ProjectPaths
from core.queue import ShardQueue
from core.state import StateStore
from domain.resume import (
    ChallengeResumePlan,
    ShardResumePlan,
    build_evidence,
    carry_forward_message,
    compute_resume_plan,
    design_evidence,
    document_evidence,
    implement_evidence,
)
from domain.validation import ChallengeValidator
from hermes.invocation import (
    DEFAULT_HERMES_COMMAND,
    DEFAULT_HERMES_TIMEOUT,
    HERMES_TIMEOUT_RETURNCODE,
    apply_legacy_custom_provider_config,
    default_hermes_arguments,
    invoke_hermes,
    remove_conflicting_custom_pool_config,
)
from hermes.progress import ensure_report, update_report
from hermes.prompt import render_prompt
from hermes.report import merge_validation_into_report
from hermes.validation import (
    record_per_challenge_complete,
    run_validation,
    validate_gate,
)

__all__ = [
    "DEFAULT_HERMES_COMMAND",
    "DEFAULT_HERMES_TIMEOUT",
    "HERMES_TIMEOUT_RETURNCODE",
    "HermesRunner",
    "merge_validation_into_report",
]


def _carry_forward_pending_message(stage: str) -> str:
    return f"Waiting for {stage} stage execution"


class HermesRunner:
    """Owns shard execution, resume planning, and validate-stage event writes."""

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        image_exists: Callable[[str], bool] | None = None,
    ):
        self.paths = paths
        self.queue = ShardQueue(paths)
        self.state = StateStore(paths)
        self.validator = ChallengeValidator(paths)
        self._image_exists = image_exists or default_image_exists

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def render_prompt(
        self,
        shard: Path,
        report: Path,
        worker: str,
        *,
        original_shard_name: str | None = None,
        resume_plan: ShardResumePlan | None = None,
    ) -> str:
        return render_prompt(
            self.paths,
            shard,
            report,
            worker,
            original_shard_name=original_shard_name,
            resume_plan=resume_plan,
        )

    def run(
        self,
        worker: str,
        *,
        loop: bool = False,
        dry_run: bool = False,
        max_shards: int = 0,
        timeout: int | None = None,
    ) -> dict:
        self.paths.initialize()
        processed = 0
        failed = 0
        outcomes: list[dict] = []
        while True:
            outcome = self.process_one(worker, dry_run=dry_run, timeout=timeout)
            if outcome["status"] == "empty":
                break
            outcomes.append(outcome)
            processed += 1
            if outcome["status"] == "failed":
                failed += 1
            if not loop or (max_shards and processed >= max_shards):
                break
        return {"processed": processed, "failed": failed, "outcomes": outcomes}

    def process_one(
        self,
        worker: str,
        *,
        dry_run: bool,
        timeout: int | None = None,
    ) -> dict:
        shard = self.queue.claim(worker) 
        if shard is None:
            return {"status": "empty"}

        original_shard_name = self.queue.original_name(shard)
        report = self.paths.reports / f"{shard.stem}.report.json"
        log = self.paths.logs / f"{shard.stem}.log"
        challenge_ids = self.queue.challenge_ids(shard)

        if dry_run:
            return self._process_dry_run(
                shard, original_shard_name, worker, report, log, challenge_ids
            )

        return self._process_real(
            shard,
            original_shard_name,
            worker,
            report,
            log,
            challenge_ids,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Dry-run path: claim, plan, render, requeue. No state writes.
    # ------------------------------------------------------------------

    def _process_dry_run(
        self,
        shard: Path,
        original_shard_name: str,
        worker: str,
        report: Path,
        log: Path,
        challenge_ids: list[str],
    ) -> dict:
        try:
            plan = compute_resume_plan(
                state=self.state,
                paths=self.paths,
                shard=original_shard_name,
                challenge_ids=challenge_ids,
                image_exists=self._image_exists,
            )
            prompt = self.render_prompt(
                shard,
                report,
                worker,
                original_shard_name=original_shard_name,
                resume_plan=plan,
            )
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(prompt + "\n", encoding="utf-8")
            return {"status": "dry_run", "shard": original_shard_name}
        finally:
            try:
                self.queue.requeue(shard.name, "running")
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------
    # Real run path
    # ------------------------------------------------------------------

    def _process_real(
        self,
        shard: Path,
        original_shard_name: str,
        worker: str,
        report: Path,
        log: Path,
        challenge_ids: list[str],
        *,
        timeout: int | None,
    ) -> dict:
        # 1. Plan from historical window (before writing this run's queued).
        plan = compute_resume_plan(
            state=self.state,
            paths=self.paths,
            shard=original_shard_name,
            challenge_ids=challenge_ids,
            image_exists=self._image_exists,
        )

        # 2. Reset snapshots; events stay append-only.
        self.state.reset_snapshots(original_shard_name)

        # 3. Write the current claim event.
        self.state.record(
            shard=original_shard_name,
            worker=worker,
            stage="queued",
            status="running",
            message=f"Worker claimed {len(challenge_ids)} challenge(s)",
        )

        # 4. Carry-forward each skipped stage for each challenge.
        plan_by_id: dict[str, ChallengeResumePlan] = {
            cp.challenge_id: cp for cp in plan.challenges
        }
        for cp in plan.challenges:
            for stage in cp.skipped_stages:
                source_id = cp.stage_sources.get(stage, 0)
                self.state.record(
                    shard=original_shard_name,
                    challenge_id=cp.challenge_id,
                    worker=worker,
                    stage=stage,
                    status="passed",
                    message=carry_forward_message(stage, source_id),
                )

        # 5. Full-skip short circuit: no Hermes invocation needed.
        if plan.all_challenges_fully_skipped:
            return self._shortcircuit_all_skipped(
                shard, original_shard_name, worker, report, challenge_ids
            )

        # 6. First-pending stage event per challenge.
        for cp in plan.challenges:
            if cp.first_pending_stage is not None:
                self.state.record(
                    shard=original_shard_name,
                    challenge_id=cp.challenge_id,
                    worker=worker,
                    stage=cp.first_pending_stage,
                    status="pending",
                    message=_carry_forward_pending_message(cp.first_pending_stage),
                )

        # 7. Render and invoke Hermes.
        prompt = self.render_prompt(
            shard,
            report,
            worker,
            original_shard_name=original_shard_name,
            resume_plan=plan,
        )
        try:
            returncode = self._invoke(prompt, log, dry_run=False, timeout=timeout)
        except KeyboardInterrupt:
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                "Runner interrupted",
                130,
            )
            raise

        if returncode != 0:
            timed_out = returncode == HERMES_TIMEOUT_RETURNCODE
            if not timed_out or not self._timeout_recovery_complete(
                original_shard_name, challenge_ids
            ):
                self._mark_shard_failed(
                    shard,
                    original_shard_name,
                    worker,
                    challenge_ids,
                    report,
                    f"Hermes exited with {returncode}",
                    returncode,
                )
                return {
                    "status": "failed",
                    "shard": original_shard_name,
                    "returncode": returncode,
                }

        ensure_report(report, shard, worker, "completed_by_runner", returncode)

        # 8. Mandatory validation per challenge (skip resume-skip).
        per_results = self._run_validation(
            original_shard_name, worker, challenge_ids, plan_by_id
        )
        merge_validation_into_report(report, per_results)

        any_failed = any(
            result.get("solve_status") == "failed" for result in per_results
        )
        if any_failed:
            self._record_per_challenge_complete(
                original_shard_name, worker, per_results
            )
            self.state.record(
                shard=original_shard_name,
                worker=worker,
                stage="complete",
                status="failed",
                message="One or more challenges failed validation",
            )
            update_report(report, "failed", "challenge validation failed")
            self.queue.complete(shard, "failed")
            return {"status": "failed", "shard": original_shard_name}

        # Success path.
        self._record_per_challenge_complete(
            original_shard_name, worker, per_results
        )
        self.state.record(
            shard=original_shard_name,
            worker=worker,
            stage="complete",
            status="passed",
            message="Generation completed",
        )
        update_report(report, "passed")
        self.queue.complete(shard, "done")
        return {"status": "done", "shard": original_shard_name}

    # ------------------------------------------------------------------
    # Short-circuit and recovery helpers
    # ------------------------------------------------------------------

    def _shortcircuit_all_skipped(
        self,
        shard: Path,
        original_shard_name: str,
        worker: str,
        report: Path,
        challenge_ids: list[str],
    ) -> dict:
        per_results = [
            {
                "challenge_id": challenge_id,
                "solve_status": "passed",
                "validation_status": "skipped_resume",
            }
            for challenge_id in challenge_ids
        ]
        merge_validation_into_report(
            report, per_results, shard=shard, worker=worker, runner_status="passed"
        )
        for challenge_id in challenge_ids:
            self.state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="complete",
                status="passed",
                message="carry-forward: all stages already complete",
            )
        self.state.record(
            shard=original_shard_name,
            worker=worker,
            stage="complete",
            status="passed",
            message="resumed: all challenges fully complete",
        )
        update_report(report, "passed")
        self.queue.complete(shard, "done")
        return {"status": "done", "shard": original_shard_name, "short_circuit": True}

    def _timeout_recovery_complete(
        self, original_shard_name: str, challenge_ids: list[str]
    ) -> bool:
        """Return True when every challenge passes design/implement/build/document evidence."""
        recovery_plan = compute_resume_plan(
            state=self.state,
            paths=self.paths,
            shard=original_shard_name,
            challenge_ids=challenge_ids,
            image_exists=self._image_exists,
        )
        for cp in recovery_plan.challenges:
            if cp.directory is None:
                return False
            category = _category_of(cp.directory, self.paths)
            if not design_evidence(cp.directory, cp.challenge_id):
                return False
            if not implement_evidence(cp.directory, category):
                return False
            if not build_evidence(
                cp.directory, category, self._image_exists
            ):
                return False
            if not document_evidence(cp.directory):
                return False
        return True

    # ------------------------------------------------------------------
    # Validation orchestration
    # ------------------------------------------------------------------

    def _run_validation(
        self,
        original_shard_name: str,
        worker: str,
        challenge_ids: list[str],
        plan_by_id: dict[str, ChallengeResumePlan],
    ) -> list[dict[str, Any]]:
        return run_validation(
            state=self.state,
            validator=self.validator,
            paths=self.paths,
            image_exists=self._image_exists,
            original_shard_name=original_shard_name,
            worker=worker,
            challenge_ids=challenge_ids,
            plan_by_id=plan_by_id,
        )

    def _validate_gate(
        self, challenge_id: str, plan: ChallengeResumePlan | None
    ) -> str | None:
        return validate_gate(challenge_id, plan, self.paths, self._image_exists)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _record_per_challenge_complete(
        self,
        original_shard_name: str,
        worker: str,
        per_results: list[dict[str, Any]],
    ) -> None:
        record_per_challenge_complete(
            self.state,
            original_shard_name,
            worker,
            per_results,
        )

    def _mark_shard_failed(
        self,
        shard: Path,
        original_shard_name: str,
        worker: str,
        challenge_ids: list[str],
        report: Path,
        message: str,
        returncode: int,
    ) -> None:
        for challenge_id in challenge_ids:
            self.state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="complete",
                status="failed",
                message=message,
            )
        self.state.record(
            shard=original_shard_name,
            worker=worker,
            stage="complete",
            status="failed",
            message=message,
        )
        ensure_report(report, shard, worker, "failed", returncode)
        self.queue.complete(shard, "failed")

    # ------------------------------------------------------------------
    # Hermes subprocess invocation
    # ------------------------------------------------------------------

    def _invoke(
        self,
        prompt: str,
        log: Path,
        dry_run: bool,
        *,
        timeout: int | None = None,
    ) -> int:
        return invoke_hermes(
            self.paths,
            prompt,
            log,
            dry_run,
            timeout=timeout,
            hermes_arguments=self._hermes_arguments,
            apply_legacy_custom_provider=self._apply_legacy_custom_provider,
            remove_conflicting_custom_pool=self._remove_conflicting_custom_pool,
        )

    def _apply_legacy_custom_provider(self, environment: dict[str, str]) -> bool:
        return apply_legacy_custom_provider_config(self.paths.hermes_home, environment)

    def _remove_conflicting_custom_pool(self) -> bool:
        return remove_conflicting_custom_pool_config(self.paths.hermes_home)

    @staticmethod
    def _hermes_arguments() -> list[str]:
        return default_hermes_arguments()


def _category_of(challenge_dir: Path, paths: ProjectPaths) -> str:
    try:
        relative = challenge_dir.resolve().relative_to(paths.challenges.resolve())
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""
