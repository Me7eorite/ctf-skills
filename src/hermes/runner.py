"""Hermes prompt rendering and shard execution."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from core.jsonio import read_json
from core.paths import ProjectPaths
from core.queue import ShardQueue
from core.state import StateStore
from domain.validation import ChallengeValidator
from hermes.progress import ensure_report, record_final, update_report
from hermes.prompt import render_prompt

DEFAULT_HERMES_COMMAND = "hermes chat -Q --yolo -q"
DEFAULT_HERMES_TIMEOUT = 1200
HERMES_TIMEOUT_RETURNCODE = 124


class HermesRunner:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self.queue = ShardQueue(paths)
        self.state = StateStore(paths)

    def render_prompt(self, shard: Path, report: Path, worker: str) -> str:
        return render_prompt(self.paths, shard, report, worker)

    def run(
        self,
        worker: str,
        *,
        loop: bool = False,
        validate: bool = False,
        dry_run: bool = False,
        max_shards: int = 0,
    ) -> dict:
        self.paths.initialize()
        processed = 0
        failed = 0
        outcomes = []
        while True:
            outcome = self.process_one(worker, validate=validate, dry_run=dry_run)
            if outcome["status"] == "empty":
                break
            outcomes.append(outcome)
            processed += 1
            if outcome["status"] == "failed":
                failed += 1
            if not loop or (max_shards and processed >= max_shards):
                break
        return {"processed": processed, "failed": failed, "outcomes": outcomes}

    def process_one(self, worker: str, *, validate: bool, dry_run: bool) -> dict:
        shard = self.queue.claim(worker)
        if shard is None:
            return {"status": "empty"}

        report = self.paths.reports / f"{shard.stem}.report.json"
        log = self.paths.logs / f"{shard.stem}.log"
        challenge_ids = self.queue.challenge_ids(shard)
        self.state.record(
            shard=shard.name,
            worker=worker,
            stage="queued",
            status="running",
            message=f"Worker claimed {len(challenge_ids)} challenge(s)",
        )
        for challenge_id in challenge_ids:
            self.state.record(
                shard=shard.name,
                challenge_id=challenge_id,
                worker=worker,
                stage="design",
                status="pending",
                message="Waiting for agent authoring",
            )

        try:
            returncode = self._invoke(
                self.render_prompt(shard, report, worker), log, dry_run
            )
        except KeyboardInterrupt:
            record_final(
                self.state,
                shard.name,
                challenge_ids,
                worker,
                "failed",
                "Runner interrupted",
            )
            ensure_report(report, shard, worker, "failed", 130)
            self.queue.complete(shard, "failed")
            raise

        timed_out = returncode == HERMES_TIMEOUT_RETURNCODE
        recovered_after_timeout = False
        if returncode != 0 and not dry_run:
            # The agent often finishes its real work (design + exp test) but the
            # subprocess does not exit cleanly, so subprocess.run raises
            # TimeoutExpired and returns 124. If artifacts are already on disk we
            # should let validation be the source of truth rather than failing
            # outright.
            if timed_out and self._artifacts_complete(challenge_ids):
                recovered_after_timeout = True
                self.state.record(
                    shard=shard.name,
                    worker=worker,
                    stage="document",
                    status="passed",
                    message="Hermes timed out after artifacts were produced; "
                    "recovering via post-run checks",
                )
            else:
                record_final(
                    self.state,
                    shard.name,
                    challenge_ids,
                    worker,
                    "failed",
                    f"Hermes exited with {returncode}",
                )
                ensure_report(report, shard, worker, "failed", returncode)
                self.queue.complete(shard, "failed")
                return {"status": "failed", "shard": shard.name, "returncode": returncode}

        if dry_run:
            runner_status = "dry_run"
        elif recovered_after_timeout:
            runner_status = "completed_after_timeout"
        else:
            runner_status = "completed_by_runner"
        ensure_report(report, shard, worker, runner_status, returncode)
        if validate and not dry_run:
            for challenge_id in challenge_ids:
                self.state.record(
                    shard=shard.name,
                    challenge_id=challenge_id,
                    worker=worker,
                    stage="validate",
                    status="running",
                    message="Running system validation",
                )
            summary = ChallengeValidator(self.paths).validate(challenge_ids)
            if summary["status_counts"].get("passed", 0) != summary["total"]:
                record_final(
                    self.state,
                    shard.name,
                    challenge_ids,
                    worker,
                    "failed",
                    "System validation failed",
                )
                update_report(report, "failed", "challenge validation failed")
                self.queue.complete(shard, "failed")
                return {"status": "failed", "shard": shard.name}

        record_final(
            self.state,
            shard.name,
            challenge_ids,
            worker,
            "passed",
            "Dry run prompt generated" if dry_run else "Generation completed",
        )
        update_report(report, "passed")
        self.queue.complete(shard, "done")
        return {"status": "done", "shard": shard.name}

    def _artifacts_complete(self, challenge_ids: list[str]) -> bool:
        """Return True if every claimed challenge already has buildable artifacts.

        Used to rescue the shard when Hermes hits the wall-clock timeout AFTER
        the agent finished its real work. We trust the validator to be the
        final authority — this only gates whether we let it run.
        """
        if not challenge_ids:
            return False
        for challenge_id in challenge_ids:
            metadata_path = self._find_metadata(challenge_id)
            if metadata_path is None:
                return False
            metadata = read_json(metadata_path, {})
            if not isinstance(metadata, dict):
                return False
            if metadata.get("build_status") != "passed":
                return False
        return True

    def _find_metadata(self, challenge_id: str) -> Path | None:
        for candidate in self.paths.challenges.glob(f"*/{challenge_id}*/metadata.json"):
            return candidate
        return None

    def _invoke(self, prompt: str, log: Path, dry_run: bool) -> int:
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
        timeout = int(os.environ.get("HERMES_TIMEOUT", DEFAULT_HERMES_TIMEOUT))

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
                    timeout=timeout,
                    check=False,
                )
            except FileNotFoundError:
                output.write("Hermes command not found. Set HERMES_CMD or install Hermes.\n")
                return 127
            except subprocess.TimeoutExpired:
                output.write(f"\nHermes command timed out after {timeout}s.\n")
                return 124
        return process.returncode

    def _apply_legacy_custom_provider(self, environment: dict[str, str]) -> bool:
        """Map pre-0.16 custom model fields to the current environment contract."""
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
        """Prevent stale named custom credentials from overriding local config."""
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
                arguments.extend(
                    [
                        "--python",
                        str(python311),
                    ]
                )
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
