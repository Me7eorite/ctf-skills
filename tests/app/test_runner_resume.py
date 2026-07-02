"""End-to-end runner tests using temp project layouts and a fake validator."""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from core.jsonio import read_json, write_json
from core.state import InMemoryProgressStore, ProgressStore
from hermes import process as hermes_process
from hermes.host_build import HostBuildError, NoopHostBuilder
from hermes.runner import HermesRunner
from persistence import PersistenceConnectionError


@dataclass(frozen=True)
class _Paths:
    root: Path

    @property
    def repository(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def challenges(self) -> Path:
        return self.root / "work" / "challenges"

    @property
    def shards(self) -> Path:
        return self.root / "work" / "shards"

    @property
    def reports(self) -> Path:
        return self.root / "work" / "reports"

    @property
    def logs(self) -> Path:
        return self.root / "work" / "logs"

    @property
    def prompt_template(self) -> Path:
        return self.root / "prompts" / "shard_prompt.md"

    @property
    def generation_profile(self) -> Path:
        return self.root / "generation-profiles.json"

    @property
    def design_skill(self) -> Path:
        return self.root / "skills" / "design-challenges" / "SKILL.md"

    @property
    def design_references(self) -> Path:
        return self.root / "skills" / "design-challenges" / "references"

    @property
    def hermes_home(self) -> Path:
        return self.root / ".hermes"

    @property
    def executions(self) -> Path:
        return self.root / "work" / "executions"

    @property
    def locks_root(self) -> Path:
        return self.root / "work" / "locks"

    @property
    def build_publisher_locks(self) -> Path:
        return self.locks_root / "build-publisher"

    def initialize(self) -> None:
        for state in ("pending", "running", "done", "failed"):
            (self.shards / state).mkdir(parents=True, exist_ok=True)
        for category in ("web", "pwn", "re"):
            (self.challenges / category).mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.build_publisher_locks.mkdir(parents=True, exist_ok=True)


def _copy_real_prompt(target: _Paths) -> None:
    real = Path(__file__).resolve().parents[2] / "prompts" / "shard_prompt.md"
    target.prompt_template.parent.mkdir(parents=True, exist_ok=True)
    target.prompt_template.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    target.generation_profile.write_text("{}", encoding="utf-8")
    target.design_skill.parent.mkdir(parents=True, exist_ok=True)
    target.design_skill.write_text("# s\n", encoding="utf-8")
    target.design_references.mkdir(parents=True, exist_ok=True)
    for filename in (
        "design-core.md",
        "category-tactics.md",
        "difficulty-rubric.md",
        "shared_generation_strategy.md",
    ):
        (target.design_references / filename).write_text(f"# {filename}\n", encoding="utf-8")


def _make_shard(
    paths: _Paths,
    shard_name: str,
    challenge_ids: list[str],
    *,
    envelope: dict | None = None,
    challenge_extra: dict | None = None,
) -> Path:
    payload = {
        **(envelope or {}),
        "challenges": [
            {"id": cid, "category": cid.split("-", 1)[0], **(challenge_extra or {})} for cid in challenge_ids
        ],
    }
    pending = paths.shards / "pending" / shard_name
    pending.write_text(json.dumps(payload), encoding="utf-8")
    return pending


def _make_web_challenge(
    paths: _Paths,
    challenge_id: str,
    *,
    category: str = "web",
    slug: str = "demo",
    docker_image: str = "demo:latest",
    metadata_extra: dict | None = None,
) -> Path:
    directory = paths.challenges / category / f"{challenge_id}-{slug}"
    deploy = directory / "deploy"
    (deploy / "src").mkdir(parents=True, exist_ok=True)
    (deploy / "src" / "app.py").write_text("print('vuln')\n", encoding="utf-8")
    (deploy / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    (deploy / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (directory / "validate.sh").write_text("#!/bin/bash\necho flag{x}\n", encoding="utf-8")
    (directory / "writenup").mkdir(parents=True, exist_ok=True)
    (directory / "writenup" / "exp.py").write_text("pass\n", encoding="utf-8")
    (directory / "writenup" / "wp.md").write_text(
        "# title\n\n## A\n\n" + ("x" * 600) + "\n\n## B\nmore\n",
        encoding="utf-8",
    )
    (directory / "README.md").write_text(
        "# title\n\n## A\n\n" + ("y" * 600) + "\n\n## B\nmore\n",
        encoding="utf-8",
    )
    metadata = {
        "id": challenge_id,
        "title": "demo",
        "category": category,
        "difficulty": "easy",
        "docker_image": docker_image,
        "build_command": "docker build -t demo:latest .",
        "build_status": "passed",
        "solve_status": "passed",
        "flag": "flag{example}",
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return directory


def _seed_passed(
    store: ProgressStore,
    shard: str,
    challenge_id: str,
    stages: list[str],
) -> None:
    store.record(shard=shard, stage="queued", status="running")
    for stage in stages:
        store.record(
            shard=shard,
            stage=stage,
            status="running",
            challenge_id=challenge_id,
        )
        store.record(
            shard=shard,
            stage=stage,
            status="passed",
            challenge_id=challenge_id,
        )


class RunnerDryRunIsolationTests(unittest.TestCase):
    def test_dry_run_does_not_write_events_and_requeues(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            shard_path = _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            runner = HermesRunner(paths, image_exists=lambda _: True)  # type: ignore[arg-type]
            result = runner.process_one("dry-01", dry_run=True)

            self.assertEqual(result["status"], "dry_run")
            self.assertTrue(shard_path.exists(), "shard not requeued to pending")

            dashboard = runner.state.dashboard()
            self.assertEqual(dashboard["snapshots"], [])
            self.assertEqual(dashboard["events"], [])

    def test_dry_run_renders_resume_plan_into_log(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            runner = HermesRunner(paths, image_exists=lambda _: True)  # type: ignore[arg-type]
            runner.process_one("dry-01", dry_run=True)

            log_path = paths.logs / "web-0001-0001.dry-01.log"
            self.assertTrue(log_path.exists())
            rendered = log_path.read_text(encoding="utf-8")
            self.assertIn("0. Resume Check", rendered)
            self.assertIn("web-0001", rendered)
            self.assertIn("./input/shard.json", rendered)
            self.assertIn("./output/challenges", rendered)
            self.assertNotIn(str(paths.root), rendered)
            self.assertFalse((paths.root / "work" / "executions").exists())


class RunnerRealRunTests(unittest.TestCase):
    def _make_runner_with_fake_invoke(
        self,
        paths: _Paths,
        *,
        validator_status: str = "passed",
        image_exists_value: bool = True,
        progress: ProgressStore | None = None,
    ) -> HermesRunner:
        runner = HermesRunner(
            paths,
            progress=progress,
            image_exists=lambda _: image_exists_value,
            host_builder=NoopHostBuilder(),
            profile_exists=lambda _: True,
            terminal_workspace_probe=lambda **_kwargs: None,
        )  # type: ignore[arg-type]

        def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("fake invoke\n", encoding="utf-8")
            return 0

        runner._invoke = fake_invoke  # type: ignore[assignment]

        def fake_validate(challenge_id: str) -> dict:
            return {
                "challenge_id": challenge_id,
                "status": validator_status,
                "elapsed": 0.01,
            }

        runner.validator.validate_challenge = fake_validate  # type: ignore[assignment]
        runner.validator.validate_path = (  # type: ignore[method-assign]
            lambda _path, *, expected_challenge_id: fake_validate(expected_challenge_id)
        )
        return runner

    def test_first_run_writes_design_pending_and_marks_complete(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)

            outcome = runner.process_one("worker-01", dry_run=False)
            self.assertEqual(outcome["status"], "done")

            events = runner.state.events_for_shard("web-0001-0001.json")
            stage_status = [(e["stage"], e["status"], e["challenge_id"]) for e in events]
            # First pending must be design.
            self.assertIn(("design", "pending", "web-0001"), stage_status)
            # No carry-forward on a first run.
            for _, status, _ in stage_status:
                self.assertNotEqual(status, "passed_carry")
            # Validate events were written by the runner.
            self.assertIn(("validate", "running", "web-0001"), stage_status)
            self.assertIn(("validate", "passed", "web-0001"), stage_status)
            # Final complete events.
            self.assertIn(("complete", "passed", "web-0001"), stage_status)
            self.assertIn(("complete", "passed", ""), stage_status)

    def test_docker_terminal_visibility_failure_stops_before_build_prompt(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)
            invoked = {"build": False}

            def fail_probe(**_kwargs):
                raise hermes_process.TerminalWorkspaceVisibilityError(
                    "Docker terminal backend did not write to the host execution workspace"
                )

            def build_invoke(*_args, **_kwargs):
                invoked["build"] = True
                return 0

            runner._verify_terminal_workspace = fail_probe  # type: ignore[method-assign]
            runner._invoke = build_invoke  # type: ignore[assignment]

            outcome = runner.process_one("worker-01", dry_run=False)

            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["hermes_phase"], "terminal_workspace")
            self.assertIn("Docker terminal backend", outcome["error"])
            self.assertFalse(invoked["build"])

    def test_first_run_validates_directory_created_by_hermes(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)

            def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **kwargs) -> int:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("fake invoke\n", encoding="utf-8")
                created = _make_web_challenge(paths, "web-0001")
                workspace = kwargs["workspace"]
                output = workspace.output / "challenges" / "web" / created.name
                output.parent.mkdir(parents=True, exist_ok=True)
                created.replace(output)
                return 0

            runner._invoke = fake_invoke  # type: ignore[assignment]

            outcome = runner.process_one("worker-01", dry_run=False)
            self.assertEqual(outcome["status"], "done")

            events = runner.state.events_for_shard("web-0001-0001.json")
            validate_events = [
                event for event in events if event["stage"] == "validate" and event["challenge_id"] == "web-0001"
            ]
            self.assertIn(
                ("validate", "passed"),
                [(event["stage"], event["status"]) for event in validate_events],
            )
            self.assertFalse(
                any(
                    event["status"] == "failed" and "missing_challenge" in event["message"] for event in validate_events
                )
            )

    def test_pwn_shard_policy_timeout_is_passed_to_hermes(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "pwn-0001", category="pwn")
            _make_shard(paths, "pwn-0001-0001.json", ["pwn-0001"])
            runner = self._make_runner_with_fake_invoke(paths)
            observed: dict[str, int | None] = {}

            def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                observed["timeout"] = timeout
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("fake invoke\n", encoding="utf-8")
                return 0

            runner._invoke = fake_invoke  # type: ignore[assignment]

            outcome = runner.process_one("worker-01", dry_run=False)

            self.assertIn(outcome["status"], {"done", "failed"})
            self.assertEqual(observed["timeout"], 3600)

    def test_failed_hermes_outcome_includes_phase_elapsed_and_timeout_metadata(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)

            def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("Anthropic 401 gic密钥已失效\n", encoding="utf-8")
                return 1

            runner._invoke = fake_invoke  # type: ignore[assignment]

            outcome = runner.process_one("worker-01", dry_run=False)

            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["failure_type"], "infrastructure")
            self.assertEqual(outcome["hermes_phase"], "hermes_auth")
            self.assertIsInstance(outcome["elapsed_seconds"], float)
            self.assertEqual(outcome["effective_timeout_seconds"], 2700)
            self.assertEqual(outcome["timeout_source"], "shard_policy")
            report = read_json(paths.reports / "web-0001-0001.worker-01.report.json", {})
            self.assertEqual(report["hermes_phase"], "hermes_auth")
            self.assertEqual(report["effective_timeout_seconds"], 2700)
            self.assertEqual(report["timeout_source"], "shard_policy")

    def test_keyboard_interrupt_marks_cancelled_before_reraising(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)

            def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("interrupted\n", encoding="utf-8")
                raise KeyboardInterrupt

            runner._invoke = fake_invoke  # type: ignore[assignment]

            with self.assertRaises(KeyboardInterrupt):
                runner.process_one("worker-01", dry_run=False)

            report = read_json(paths.reports / "web-0001-0001.worker-01.report.json", {})
            self.assertEqual(report["hermes_phase"], "hermes_cancelled")
            self.assertEqual(report["returncode"], -2)
            self.assertIsInstance(report["elapsed_seconds"], float)
            self.assertTrue((paths.shards / "failed" / "web-0001-0001.json").exists())

    def test_resume_to_build_writes_carry_forward_and_build_pending(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            store = InMemoryProgressStore()
            _seed_passed(store, "web-0001-0001.json", "web-0001", ["design", "implement"])

            runner = self._make_runner_with_fake_invoke(paths, progress=store)
            runner.process_one("worker-02", dry_run=False)

            # Examine events written AFTER the new claim event.
            latest_claim = runner.state.latest_claim_event("web-0001-0001.json")
            self.assertIsNotNone(latest_claim)
            assert latest_claim is not None
            events = runner.state.events_for_shard("web-0001-0001.json")
            window = [e for e in events if e["id"] >= latest_claim["id"]]
            stages_in_window = [(e["stage"], e["status"], e["challenge_id"]) for e in window]

            # carry-forward design and implement, then build pending.
            self.assertIn(("design", "passed", "web-0001"), stages_in_window)
            self.assertIn(("implement", "passed", "web-0001"), stages_in_window)
            self.assertIn(("build", "pending", "web-0001"), stages_in_window)

            carry_forwards = [
                event
                for event in window
                if event["status"] == "passed"
                and event["stage"] in {"design", "implement"}
                and event["challenge_id"] == "web-0001"
            ]
            for event in carry_forwards:
                self.assertTrue(
                    event["message"].startswith("carry-forward:"),
                    f"expected carry-forward prefix, got {event['message']!r}",
                )

    def test_retry_reads_resume_source_but_writes_current_shard_key(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(
                paths,
                "web-current.json",
                ["web-0001"],
                envelope={"resume_from_shard_basename": "web-source.json"},
            )

            store = InMemoryProgressStore()
            _seed_passed(store, "web-source.json", "web-0001", ["design", "implement"])

            runner = self._make_runner_with_fake_invoke(paths, progress=store)
            runner.process_one("worker-retry", dry_run=False)

            current_events = runner.state.events_for_shard("web-current.json")
            source_events = runner.state.events_for_shard("web-source.json")
            self.assertTrue(current_events)
            self.assertTrue(source_events)
            self.assertTrue(
                any(
                    event["message"].startswith("carry-forward:") and event["shard"] == "web-current.json"
                    for event in current_events
                )
            )
            self.assertFalse(any(event["worker"] == "worker-retry" for event in source_events))

    def test_clean_mode_starts_empty_and_does_not_carry_prior_progress(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            canonical = _make_web_challenge(paths, "web-0001", slug="old")
            _make_shard(
                paths,
                "web-clean.json",
                ["web-0001"],
                envelope={"execution_mode": "clean"},
            )
            store = InMemoryProgressStore()
            _seed_passed(store, "web-prior.json", "web-0001", ["design", "implement"])
            runner = self._make_runner_with_fake_invoke(paths, progress=store)
            runner.validation_repair_attempts = 0
            observed_empty = False

            def invoke(_prompt, log, dry_run=False, **kwargs):
                del dry_run
                nonlocal observed_empty
                workspace = kwargs["workspace"]
                observed_empty = not any(workspace.output.rglob("metadata.json"))
                replacement = workspace.output / "challenges" / "web" / "web-0001-new"
                replacement.parent.mkdir(parents=True, exist_ok=True)
                __import__("shutil").copytree(canonical, replacement)
                metadata = json.loads((replacement / "metadata.json").read_text())
                metadata["id"] = "web-0001"
                (replacement / "metadata.json").write_text(json.dumps(metadata))
                log.write_text("fake invoke\n")
                return 0

            runner._invoke = invoke  # type: ignore[assignment]
            outcome = runner.process_one("worker-clean", dry_run=False)

            self.assertEqual(outcome["status"], "done")
            self.assertTrue(observed_empty)
            events = runner.state.events_for_shard("web-clean.json")
            self.assertFalse(any(e["message"].startswith("carry-forward:") for e in events))
            self.assertTrue((paths.challenges / "web" / "web-0001-new").is_dir())
            workspace = next((paths.root / "work" / "executions").iterdir())
            self.assertTrue((workspace / "quarantine" / "web" / "web-0001-old").is_dir())
            self.assertFalse((workspace / "output").exists())
            self.assertFalse((workspace / "logs").exists())

    def test_publisher_rejection_returns_infrastructure_failure(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001", slug="old")
            _make_shard(
                paths,
                "web-current.json",
                ["web-0001"],
                envelope={
                    "execution_mode": "resume",
                    "resume_from_shard_basename": "web-source.json",
                },
            )
            runner = self._make_runner_with_fake_invoke(paths)
            runner.validation_repair_attempts = 0

            def invoke(_prompt, log, dry_run=False, **kwargs):
                del dry_run
                workspace = kwargs["workspace"]
                duplicate = workspace.output / "challenges" / "web" / "web-0001-other"
                duplicate.mkdir(parents=True)
                write_json(duplicate / "metadata.json", {"id": "web-0001", "category": "web"})
                log.write_text("fake invoke\n")
                return 0

            runner._invoke = invoke  # type: ignore[assignment]
            outcome = runner.process_one("worker-resume", dry_run=False)

            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["failure_type"], "validation")
            self.assertTrue((paths.shards / "failed" / "web-current.json").exists())
            self.assertTrue((paths.challenges / "web" / "web-0001-old").is_dir())
            workspace = next((paths.root / "work" / "executions").iterdir())
            self.assertFalse((workspace / "quarantine" / "web" / "web-0001-old").exists())

    def test_resume_source_field_is_optional_for_first_run(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-optional.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)

            outcome = runner.process_one("worker-optional", dry_run=False)

            self.assertEqual(outcome["status"], "done")
            self.assertTrue(runner.state.events_for_shard("web-optional.json"))

    def test_unsafe_resume_source_is_rejected_before_hermes_invocation(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(
                paths,
                "web-current.json",
                ["web-0001"],
                envelope={"resume_from_shard_basename": "../web-source.json"},
            )
            runner = self._make_runner_with_fake_invoke(paths)
            called = {"invoke": False}

            def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                called["invoke"] = True
                return 0

            runner._invoke = fake_invoke  # type: ignore[assignment]

            with self.assertRaises(ValueError):
                runner.process_one("worker-retry", dry_run=False)
            self.assertFalse(called["invoke"])

    def test_clean_mode_with_resume_source_fails_before_hermes_invocation(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_shard(
                paths,
                "web-contradictory.json",
                ["web-0001"],
                envelope={
                    "execution_mode": "clean",
                    "resume_from_shard_basename": "web-source.json",
                },
            )
            runner = self._make_runner_with_fake_invoke(paths)
            called = False

            def invoke(*_args, **_kwargs):
                nonlocal called
                called = True
                return 0

            runner._invoke = invoke  # type: ignore[assignment]
            outcome = runner.process_one("worker-clean", dry_run=False)

            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["failure_type"], "infrastructure")
            self.assertFalse(called)

    def test_validator_message_uses_validator_prefix(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)
            runner.process_one("worker-01", dry_run=False)

            validate_events = [
                event
                for event in runner.state.events_for_shard("web-0001-0001.json")
                if event["stage"] == "validate" and event["challenge_id"] == "web-0001"
            ]
            passed = [event for event in validate_events if event["status"] == "passed"]
            self.assertEqual(len(passed), 1)
            self.assertTrue(passed[0]["message"].startswith("validator:"))

    def test_all_skipped_short_circuit(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            store = InMemoryProgressStore()
            _seed_passed(
                store,
                "web-0001-0001.json",
                "web-0001",
                ["design", "implement", "build", "validate", "document"],
            )

            runner = self._make_runner_with_fake_invoke(paths, progress=store)
            invocation_count = {"validator": 0}

            def assert_not_called(challenge_id: str) -> dict:
                invocation_count["validator"] += 1
                return {"challenge_id": challenge_id, "status": "passed"}

            runner.validator.validate_challenge = assert_not_called  # type: ignore[assignment]

            outcome = runner.process_one("worker-03", dry_run=False)
            self.assertEqual(outcome["status"], "done")
            self.assertTrue(outcome.get("short_circuit"))
            self.assertEqual(invocation_count["validator"], 0)

            latest_claim = runner.state.latest_claim_event("web-0001-0001.json")
            assert latest_claim is not None
            window = [
                event
                for event in runner.state.events_for_shard("web-0001-0001.json")
                if event["id"] >= latest_claim["id"]
            ]
            stage_set = {(event["stage"], event["status"], event["challenge_id"]) for event in window}
            # 5 carry-forward stages + complete pair.
            for stage in ("design", "implement", "build", "validate", "document"):
                self.assertIn((stage, "passed", "web-0001"), stage_set)
            self.assertIn(("complete", "passed", "web-0001"), stage_set)
            self.assertIn(("complete", "passed", ""), stage_set)

            # No stage=done events.
            self.assertFalse(any(event["stage"] == "done" for event in window))

    def test_resume_to_validate_writes_validate_pending(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            store = InMemoryProgressStore()
            _seed_passed(
                store,
                "web-0001-0001.json",
                "web-0001",
                ["design", "implement", "build"],
            )

            runner = self._make_runner_with_fake_invoke(paths, progress=store)
            runner.process_one("worker-04", dry_run=False)

            latest_claim = runner.state.latest_claim_event("web-0001-0001.json")
            assert latest_claim is not None
            window = [
                event
                for event in runner.state.events_for_shard("web-0001-0001.json")
                if event["id"] >= latest_claim["id"]
            ]
            stages_with_status = [(e["stage"], e["status"], e["challenge_id"]) for e in window]
            self.assertIn(("validate", "pending", "web-0001"), stages_with_status)

    def test_failed_validate_marks_shard_failed(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths, validator_status="flag_mismatch")
            runner.validation_repair_attempts = 0

            outcome = runner.process_one("worker-01", dry_run=False)
            self.assertEqual(outcome["status"], "failed")
            # Shard moved out of running into failed.
            failed_path = paths.shards / "failed"
            files = sorted(p.name for p in failed_path.glob("*.json"))
            self.assertTrue(any("web-0001-0001" in name for name in files))
            workspace = next((paths.root / "work" / "executions").iterdir())
            self.assertFalse((workspace / "quarantine" / "web" / "web-0001-demo").is_dir())
            self.assertTrue((paths.challenges / "web" / "web-0001-demo").is_dir())

    def test_failed_validation_is_fed_back_to_hermes_and_retried(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(
                paths,
                validator_status="passed",
            )
            runner.validation_repair_attempts = 2
            validation_calls = 0

            def validate(_challenge_id: str) -> dict:
                nonlocal validation_calls
                validation_calls += 1
                if validation_calls == 1:
                    return {
                        "status": "nonzero_exit",
                        "elapsed": 1.2,
                        "returncode": 1,
                        "stdout_tail": "leak=0x0",
                        "stderr_tail": "EOFError",
                    }
                return {"status": "passed", "elapsed": 0.4}

            runner.validator.validate_challenge = validate  # type: ignore[assignment]
            runner.validator.validate_path = lambda *_args, **_kwargs: validate("web-0001")  # type: ignore[method-assign]
            prompts: list[str] = []

            def invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                prompts.append(prompt)
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("fake invoke\n", encoding="utf-8")
                # 模拟 Hermes 真实修改了 workspace.output（runner 在 repair attempt
                # 前后会对此目录采样签名，无变更即终止后续重试）。Marker 必须落在
                # 已认领题目目录内，publisher 的 staging-hash 比较才会发现差异，
                # 否则 publisher 会返回 noop 并跳过再次校验。
                workspace = _kwargs.get("workspace")
                if workspace is not None:
                    challenge_root = workspace.output / "challenges" / "web" / "web-0001-demo"
                    challenge_root.mkdir(parents=True, exist_ok=True)
                    marker = challenge_root / f"repair-marker-{len(prompts)}.txt"
                    marker.write_text("touched\n", encoding="utf-8")
                return 0

            runner._invoke = invoke  # type: ignore[assignment]

            outcome = runner.process_one("worker-01", dry_run=False)

            self.assertEqual(outcome["status"], "done")
            self.assertEqual(validation_calls, 2)
            self.assertEqual(len(prompts), 2)
            self.assertIn("nonzero_exit", prompts[1])
            self.assertIn("leak=0x0", prompts[1])
            self.assertIn("EOFError", prompts[1])

    def test_deterministic_validation_failure_gets_focused_ai_repair(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)
            runner.validation_repair_attempts = 2

            def validate(_challenge_id: str) -> dict:
                return {
                    "status": "contract_failed",
                    "elapsed": 0.1,
                    "contract_errors": ["metadata.build_status is not passed"],
                }

            runner.validator.validate_challenge = validate  # type: ignore[assignment]
            runner.validator.validate_path = lambda *_args, **_kwargs: validate("web-0001")  # type: ignore[method-assign]
            prompts: list[str] = []

            def invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                prompts.append(prompt)
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("fake invoke\n", encoding="utf-8")
                return 0

            runner._invoke = invoke  # type: ignore[assignment]

            outcome = runner.process_one("worker-01", dry_run=False)

            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(len(prompts), 2)
            self.assertIn("Focused repair plan:", prompts[1])
            self.assertIn("Root cause: `metadata.json` still reports an incomplete build.", prompts[1])
            self.assertIn("the host runner will run the controlled Docker build", prompts[1])
            self.assertIn("metadata.build_status is not passed", prompts[1])

    def test_host_build_failure_is_forwarded_to_validation_repair_prompt(self):
        class FailingHostBuilder:
            def build_workspace(self, _workspace, _validation_set):
                raise HostBuildError(
                    "web-0001: docker build failed with exit 1",
                    challenge_id="web-0001",
                    command=[
                        "docker",
                        "build",
                        "-t",
                        "web-0001-demo:latest",
                        "-f",
                        "deploy/Dockerfile",
                        ".",
                    ],
                    log_path="/tmp/build.log",
                    stdout_tail="COPY failed\n",
                    stderr_tail="missing deploy/src/app.py\n",
                    failure_kind="missing_source",
                    failure_hint="The build context or COPY path does not match the generated tree; verify the deploy/src files exist and the Dockerfile uses the right relative paths.",
                    failed_step="Step 7: RUN cp -R /lib* /home/ctf",
                )

        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = HermesRunner(
                paths,
                image_exists=lambda _: True,
                host_builder=FailingHostBuilder(),
                profile_exists=lambda _: True,
                validation_repair_attempts=1,
                terminal_workspace_probe=lambda **_kwargs: None,
            )  # type: ignore[arg-type]
            prompts: list[str] = []

            def invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                prompts.append(prompt)
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("fake invoke\n", encoding="utf-8")
                return 0

            runner._invoke = invoke  # type: ignore[assignment]

            outcome = runner.process_one("worker-01", dry_run=False)

            self.assertEqual(outcome["status"], "failed")
            self.assertGreaterEqual(len(prompts), 2)
            self.assertIn("missing deploy/src/app.py", prompts[1])
            self.assertIn("missing_source", prompts[1])
            self.assertIn("Step 7: RUN cp -R /lib* /home/ctf", prompts[1])
            self.assertIn("deploy/src files exist", prompts[1])

    def test_deterministic_repair_fixes_host_build_without_ai_budget(self):
        class BuildOnceAfterAutoRepair:
            def __init__(self):
                self.calls = 0

            def build_workspace(self, workspace, validation_set):
                self.calls += 1
                challenge = next(iter(validation_set.candidates.values()))
                dockerfile = (challenge / "deploy" / "Dockerfile").read_text(encoding="utf-8")
                if self.calls == 1:
                    raise HostBuildError(
                        "pwn-0001: docker build failed with exit 1",
                        challenge_id="pwn-0001",
                        command=[
                            "docker",
                            "build",
                            "-t",
                            "pwn-demo:latest",
                            "-f",
                            "deploy/Dockerfile",
                            ".",
                        ],
                        stdout_tail="COPY src/ /tmp/src/\n",
                        stderr_tail="failed to calculate checksum of ref src: not found\n",
                    )
                assert "COPY deploy/src/ /tmp/src/" in dockerfile
                assert "cp vuln /home/ctf/vuln" in dockerfile
                return NoopHostBuilder().build_workspace(workspace, validation_set)

        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            challenge = _make_web_challenge(
                paths,
                "pwn-0001",
                category="pwn",
                docker_image="pwn-demo:latest",
                metadata_extra={"port": 31337, "runtime_profile": "xinetd"},
            )
            (challenge / "deploy" / "src" / "app.py").unlink()
            (challenge / "deploy" / "src" / "vuln.c").write_text("int main(){return 0;}\n", encoding="utf-8")
            (challenge / "deploy" / "src" / "Makefile").write_text(
                "TARGET = vuln\nall:\n\tgcc vuln.c -o $(TARGET)\nclean:\n\trm -f $(TARGET)\n",
                encoding="utf-8",
            )
            (challenge / "deploy" / "_files").mkdir(parents=True, exist_ok=True)
            (challenge / "deploy" / "_files" / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (challenge / "deploy" / "_files" / "ctf.xinetd").write_text(
                "service ctf\n{\n server = /usr/sbin/chroot\n server_args = /home/ctf ./pwn\n}\n",
                encoding="utf-8",
            )
            (challenge / "deploy" / "Dockerfile").write_text(
                "FROM ubuntu:22.04\n"
                "RUN apt-get update && apt-get install -y gcc xinetd\n"
                "COPY src/ /tmp/src/\n"
                "RUN cp -R /lib* /home/ctf/ \\\n"
                "    && cp -R /usr/lib* /home/ctf/\n",
                encoding="utf-8",
            )
            _make_shard(paths, "pwn-0001-0001.json", ["pwn-0001"])
            host_builder = BuildOnceAfterAutoRepair()
            runner = HermesRunner(
                paths,
                image_exists=lambda _: True,
                host_builder=host_builder,
                profile_exists=lambda _: True,
                validation_repair_attempts=0,
                terminal_workspace_probe=lambda **_kwargs: None,
            )  # type: ignore[arg-type]
            prompts: list[str] = []
            runner._invoke = lambda prompt, log, dry_run, *, timeout=None, **_kwargs: prompts.append(prompt) or 0  # type: ignore[assignment]
            runner.validator.validate_challenge = lambda cid: {  # type: ignore[assignment]
                "challenge_id": cid,
                "status": "passed",
                "elapsed": 0.1,
            }
            runner.validator.validate_path = lambda _path, *, expected_challenge_id: {  # type: ignore[method-assign]
                "challenge_id": expected_challenge_id,
                "status": "passed",
                "elapsed": 0.1,
            }

            outcome = runner.process_one("worker-01", dry_run=False)

            self.assertEqual(outcome["status"], "done")
            self.assertEqual(host_builder.calls, 2)
            self.assertEqual(len(prompts), 1)

    def test_validation_repair_uses_capped_timeout(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = self._make_runner_with_fake_invoke(paths)
            runner.validation_repair_attempts = 1
            validation_calls = 0

            def validate(_challenge_id: str) -> dict:
                nonlocal validation_calls
                validation_calls += 1
                if validation_calls == 1:
                    return {
                        "status": "nonzero_exit",
                        "elapsed": 1.2,
                        "returncode": 1,
                        "stdout_tail": "leak=0x0",
                        "stderr_tail": "EOFError",
                    }
                return {"status": "passed", "elapsed": 0.4}

            runner.validator.validate_challenge = validate  # type: ignore[assignment]
            runner.validator.validate_path = lambda *_args, **_kwargs: validate("web-0001")  # type: ignore[method-assign]
            observed_timeouts: list[int | None] = []

            def invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                observed_timeouts.append(timeout)
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("fake invoke\n", encoding="utf-8")
                workspace = _kwargs.get("workspace")
                if workspace is not None and len(observed_timeouts) > 1:
                    challenge_root = workspace.output / "challenges" / "web" / "web-0001-demo"
                    challenge_root.mkdir(parents=True, exist_ok=True)
                    marker = challenge_root / "repair-marker.txt"
                    marker.write_text("touched\n", encoding="utf-8")
                return 0

            runner._invoke = invoke  # type: ignore[assignment]

            outcome = runner.process_one("worker-01", dry_run=False)

            self.assertEqual(outcome["status"], "done")
            self.assertGreaterEqual(len(observed_timeouts), 2)
            self.assertEqual(observed_timeouts[1], 600)

    def test_progress_write_failure_does_not_block_successful_shard(self):
        class RaisingWriteProgressStore(InMemoryProgressStore):
            def record(self, **kwargs):
                raise PersistenceConnectionError("db down")

            def record_batch(self, events):
                raise PersistenceConnectionError("db down")

        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            runner = HermesRunner(
                paths,
                progress=RaisingWriteProgressStore(),
                progress_write_exceptions=(PersistenceConnectionError,),
                image_exists=lambda _: True,
                host_builder=NoopHostBuilder(),
                profile_exists=lambda _: True,
                terminal_workspace_probe=lambda **_kwargs: None,
            )  # type: ignore[arg-type]
            runner._invoke = lambda prompt, log, dry_run, *, timeout=None, **_kwargs: 0  # type: ignore[assignment]
            runner.validator.validate_challenge = lambda cid: {  # type: ignore[assignment]
                "challenge_id": cid,
                "status": "passed",
                "elapsed": 0.0,
            }
            runner.validator.validate_path = lambda _path, *, expected_challenge_id: {  # type: ignore[method-assign]
                "challenge_id": expected_challenge_id,
                "status": "passed",
                "elapsed": 0.0,
            }

            outcome = runner.process_one("worker-05", dry_run=False)

            self.assertEqual(outcome["status"], "done")
            files = sorted(p.name for p in (paths.shards / "done").glob("*.json"))
            self.assertTrue(any("web-0001-0001" in name for name in files))

    def test_resume_read_failure_surfaces_before_hermes_invocation(self):
        class RaisingReadProgressStore(InMemoryProgressStore):
            def latest_claim_event(self, shard, *, before_id=None):
                raise PersistenceConnectionError("db down")

        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])
            runner = HermesRunner(
                paths,
                progress=RaisingReadProgressStore(),
                progress_write_exceptions=(PersistenceConnectionError,),
                image_exists=lambda _: True,
                host_builder=NoopHostBuilder(),
                profile_exists=lambda _: True,
                terminal_workspace_probe=lambda **_kwargs: None,
            )  # type: ignore[arg-type]
            called = {"invoke": False}

            def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                called["invoke"] = True
                return 0

            runner._invoke = fake_invoke  # type: ignore[assignment]

            with self.assertRaises(PersistenceConnectionError):
                runner.process_one("worker-06", dry_run=False)
            self.assertFalse(called["invoke"])


class ShardNameNormalizationTests(unittest.TestCase):
    def test_events_use_original_shard_name_not_worker_suffix(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            runner = HermesRunner(
                paths,
                image_exists=lambda _: True,
                host_builder=NoopHostBuilder(),
                profile_exists=lambda _: True,
                terminal_workspace_probe=lambda **_kwargs: None,
            )  # type: ignore[arg-type]

            def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("ok", encoding="utf-8")
                return 0

            runner._invoke = fake_invoke  # type: ignore[assignment]
            runner.validator.validate_challenge = lambda cid: {  # type: ignore[assignment]
                "challenge_id": cid,
                "status": "passed",
                "elapsed": 0.0,
            }
            runner.validator.validate_path = lambda _path, *, expected_challenge_id: {  # type: ignore[method-assign]
                "challenge_id": expected_challenge_id,
                "status": "passed",
                "elapsed": 0.0,
            }

            runner.process_one("worker-42", dry_run=False)

            # All events for the original name; none for any worker-suffixed key.
            events = runner.state.events_for_shard("web-0001-0001.json")
            self.assertTrue(events)
            for event in events:
                self.assertFalse(".worker-" in event["shard"])

            # Suffixed name has zero events.
            suffixed = runner.state.events_for_shard("web-0001-0001.worker-42.json")
            self.assertEqual(suffixed, [])


if __name__ == "__main__":
    unittest.main()
