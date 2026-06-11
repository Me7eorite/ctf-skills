"""Hermes prompt rendering and shard execution."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.docker import image_exists as default_image_exists
from core.jsonio import read_json, write_json
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
    validator_message,
)
from domain.validation import ChallengeValidator
from hermes.progress import ensure_report, update_report
from hermes.prompt import render_prompt

DEFAULT_HERMES_COMMAND = "hermes chat -Q --yolo -q"
DEFAULT_HERMES_TIMEOUT = 1500
HERMES_TIMEOUT_RETURNCODE = 124


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
        results: list[dict[str, Any]] = []
        for challenge_id in challenge_ids:
            plan = plan_by_id.get(challenge_id)
            if plan is not None and "validate" in plan.skipped_stages:
                results.append(
                    {
                        "challenge_id": challenge_id,
                        "solve_status": "passed",
                        "validation_status": "skipped_resume",
                    }
                )
                continue

            gate_error = self._validate_gate(challenge_id, plan)
            if gate_error is not None:
                self.state.record(
                    shard=original_shard_name,
                    challenge_id=challenge_id,
                    worker=worker,
                    stage="validate",
                    status="failed",
                    message=validator_message(
                        status="contract_failed", error=gate_error
                    ),
                )
                results.append(
                    {
                        "challenge_id": challenge_id,
                        "solve_status": "failed",
                        "validation_status": "contract_failed",
                        "validation_error": gate_error,
                    }
                )
                continue

            self.state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="running",
                message=validator_message(status="running"),
            )
            outcome = self.validator.validate_challenge(challenge_id)
            elapsed = outcome.get("elapsed")
            if outcome.get("status") == "passed":
                self.state.record(
                    shard=original_shard_name,
                    challenge_id=challenge_id,
                    worker=worker,
                    stage="validate",
                    status="passed",
                    message=validator_message(
                        status="passed", elapsed=elapsed, flag_matched=True
                    ),
                )
                results.append(
                    {
                        "challenge_id": challenge_id,
                        "solve_status": "passed",
                        "validation_status": "passed",
                        "validation_elapsed": elapsed,
                    }
                )
            else:
                status = str(outcome.get("status", "failed"))
                error = outcome.get("error")
                self.state.record(
                    shard=original_shard_name,
                    challenge_id=challenge_id,
                    worker=worker,
                    stage="validate",
                    status="failed",
                    message=validator_message(
                        status=status, elapsed=elapsed, error=error
                    ),
                )
                results.append(
                    {
                        "challenge_id": challenge_id,
                        "solve_status": "failed",
                        "validation_status": status,
                        "validation_elapsed": elapsed,
                        "validation_error": error,
                    }
                )
        return results

    def _validate_gate(
        self, challenge_id: str, plan: ChallengeResumePlan | None
    ) -> str | None:
        if plan is None:
            return "no resume plan entry"
        if plan.directory is None:
            return plan.lookup_status
        category = _category_of(plan.directory, self.paths)
        if not design_evidence(plan.directory, challenge_id):
            return "design evidence incomplete"
        if not implement_evidence(plan.directory, category):
            return "implement evidence incomplete"
        if not build_evidence(plan.directory, category, self._image_exists):
            return "build evidence incomplete"
        if not document_evidence(plan.directory):
            return "document evidence incomplete"
        if not (plan.directory / "validate.sh").is_file():
            return "validate.sh missing"
        if not (plan.directory / "solve" / "solve.py").is_file():
            return "solve/solve.py missing"
        return None

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _record_per_challenge_complete(
        self,
        original_shard_name: str,
        worker: str,
        per_results: list[dict[str, Any]],
    ) -> None:
        for result in per_results:
            status = (
                "passed" if result.get("solve_status") == "passed" else "failed"
            )
            self.state.record(
                shard=original_shard_name,
                challenge_id=result["challenge_id"],
                worker=worker,
                stage="complete",
                status=status,
                message=str(result.get("validation_status", "")),
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
        log.parent.mkdir(parents=True, exist_ok=True)
        if dry_run:
            log.write_text(prompt + "\n", encoding="utf-8")
            return 0

        arguments = self._hermes_arguments()
        environment = os.environ.copy()
        if self.paths.hermes_home.exists() and not environment.get("HERMES_HOME"):
            environment["HERMES_HOME"] = str(self.paths.hermes_home)
        if self._apply_legacy_custom_provider(environment):
            self._remove_conflicting_custom_pool()
            query_index = arguments.index("-q") if "-q" in arguments else len(arguments)
            arguments[query_index:query_index] = ["--provider", "custom"]
        arguments.append(prompt)
        effective_timeout = timeout if timeout is not None else DEFAULT_HERMES_TIMEOUT

        with log.open("w", encoding="utf-8") as output:
            output.write(
                f"$ {' '.join(shlex.quote(arg) for arg in arguments[:-1])} <prompt>\n\n"
            )
            try:
                process = subprocess.run(
                    arguments,
                    cwd=self.paths.root,
                    env=environment,
                    text=True,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=effective_timeout,
                    check=False,
                )
            except FileNotFoundError:
                output.write("Hermes command not found. Set HERMES_CMD or install Hermes.\n")
                return 127
            except subprocess.TimeoutExpired:
                output.write(
                    f"\nHermes command timed out after {effective_timeout}s.\n"
                )
                return HERMES_TIMEOUT_RETURNCODE
        return process.returncode

    def _apply_legacy_custom_provider(self, environment: dict[str, str]) -> bool:
        config = self.paths.hermes_home / "config.yaml"
        try:
            lines = config.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False

        model: dict[str, str] = {}
        in_model = False
        for line in lines:
            if line and not line[0].isspace():
                in_model = line.rstrip() == "model:"
                continue
            if not in_model or ":" not in line:
                continue
            key, value = line.strip().split(":", 1)
            model[key] = value.strip().strip("'\"")

        if model.get("provider") != "custom":
            return False
        if model.get("base_url"):
            environment.setdefault("CUSTOM_BASE_URL", model["base_url"])
        if model.get("api_key"):
            environment.setdefault("CUSTOM_API_KEY", model["api_key"])
        return bool(model.get("base_url"))

    def _remove_conflicting_custom_pool(self) -> bool:
        auth_path = self.paths.hermes_home / "auth.json"
        try:
            payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        pool = payload.get("credential_pool")
        if not isinstance(pool, dict):
            return False
        filtered = {
            key: value
            for key, value in pool.items()
            if not str(key).startswith("custom:")
        }
        if len(filtered) == len(pool):
            return False
        payload["credential_pool"] = filtered
        auth_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return True

    @staticmethod
    def _hermes_arguments() -> list[str]:
        command = os.environ.get("HERMES_CMD")
        if command:
            return shlex.split(command)

        hermes = shutil.which("hermes")
        if hermes:
            return [hermes, "chat", "-Q", "--yolo", "-q"]

        uvx = shutil.which("uvx")
        python311 = Path.home() / ".local" / "bin" / "python3.11.exe"
        if uvx:
            arguments = [uvx]
            if python311.exists():
                arguments.extend(["--python", str(python311)])
            arguments.extend(
                [
                    "--from",
                    "hermes-agent",
                    "hermes",
                    "chat",
                    "-Q",
                    "--yolo",
                    "-q",
                ]
            )
            return arguments
        return shlex.split(DEFAULT_HERMES_COMMAND)


def _category_of(challenge_dir: Path, paths: ProjectPaths) -> str:
    try:
        relative = challenge_dir.resolve().relative_to(paths.challenges.resolve())
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""


def merge_validation_into_report(
    report: Path,
    per_results: list[dict[str, Any]],
    *,
    shard: Path | None = None,
    worker: str | None = None,
    runner_status: str | None = None,
) -> None:
    """Merge per-challenge validation results into the shard report.

    Repairs malformed Hermes-written report structures rather than dropping
    validation outcomes. ``shard`` / ``worker`` / ``runner_status`` are only
    used when a report file does not yet exist (for example in the all-skipped
    short-circuit path).
    """
    raw = read_json(report, {})
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("challenges"), list):
        raw["challenges"] = []
    challenges_list = raw["challenges"]

    by_id: dict[str, dict[str, Any]] = {}
    for entry in challenges_list:
        if isinstance(entry, dict):
            challenge_id = entry.get("id") or entry.get("challenge_id")
            if isinstance(challenge_id, str):
                by_id[challenge_id] = entry

    repaired: list[dict[str, Any]] = []
    any_failed = False
    for result in per_results:
        challenge_id = result["challenge_id"]
        target = by_id.get(challenge_id)
        if target is None:
            target = {"id": challenge_id}
            challenges_list.append(target)
        target.setdefault("id", challenge_id)
        target["solve_status"] = result.get("solve_status", "failed")
        target["validation_status"] = result.get(
            "validation_status", target.get("validation_status", "")
        )
        if "validation_elapsed" in result:
            target["validation_elapsed"] = result["validation_elapsed"]
        if "validation_error" in result:
            target["validation_error"] = result["validation_error"]
        repaired.append(target)
        if target["solve_status"] == "failed":
            any_failed = True

    if shard is not None:
        raw.setdefault("shard", str(shard))
    if worker is not None:
        raw.setdefault("worker", worker)
    if runner_status is not None:
        raw["runner_status"] = "failed" if any_failed else runner_status

    write_json(report, raw)
