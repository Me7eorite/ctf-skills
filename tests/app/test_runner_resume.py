"""End-to-end runner tests using temp project layouts and a fake validator."""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from core.state import StateStore
from hermes.runner import HermesRunner


@dataclass(frozen=True)
class _Paths:
    root: Path

    @property
    def state_database(self) -> Path:
        return self.root / "work" / "state.sqlite3"

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

    def initialize(self) -> None:
        for state in ("pending", "running", "done", "failed"):
            (self.shards / state).mkdir(parents=True, exist_ok=True)
        for category in ("web", "pwn", "re"):
            (self.challenges / category).mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)


def _copy_real_prompt(target: _Paths) -> None:
    real = Path(__file__).resolve().parents[2] / "prompts" / "shard_prompt.md"
    target.prompt_template.parent.mkdir(parents=True, exist_ok=True)
    target.prompt_template.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    target.generation_profile.write_text("{}", encoding="utf-8")
    target.design_skill.parent.mkdir(parents=True, exist_ok=True)
    target.design_skill.write_text("# s\n", encoding="utf-8")
    target.design_references.mkdir(parents=True, exist_ok=True)


def _make_shard(paths: _Paths, shard_name: str, challenge_ids: list[str]) -> Path:
    payload = {"challenges": [{"id": cid} for cid in challenge_ids]}
    pending = paths.shards / "pending" / shard_name
    pending.write_text(json.dumps(payload), encoding="utf-8")
    return pending


def _make_web_challenge(
    paths: _Paths,
    challenge_id: str,
    *,
    slug: str = "demo",
    docker_image: str = "demo:latest",
    metadata_extra: dict | None = None,
) -> Path:
    directory = paths.challenges / "web" / f"{challenge_id}-{slug}"
    deploy = directory / "deploy"
    (deploy / "src").mkdir(parents=True, exist_ok=True)
    (deploy / "src" / "app.py").write_text("print('vuln')\n", encoding="utf-8")
    (deploy / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    (deploy / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (directory / "validate.sh").write_text("#!/bin/bash\necho flag{x}\n", encoding="utf-8")
    (directory / "solve").mkdir(parents=True, exist_ok=True)
    (directory / "solve" / "solve.py").write_text("pass\n", encoding="utf-8")
    (directory / "writeup").mkdir(parents=True, exist_ok=True)
    (directory / "writeup" / "wp.md").write_text(
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
        "category": "web",
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
    store: StateStore,
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


class RunnerRealRunTests(unittest.TestCase):
    def _make_runner_with_fake_invoke(
        self,
        paths: _Paths,
        *,
        validator_status: str = "passed",
        image_exists_value: bool = True,
    ) -> HermesRunner:
        runner = HermesRunner(paths, image_exists=lambda _: image_exists_value)  # type: ignore[arg-type]

        def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None) -> int:
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

    def test_resume_to_build_writes_carry_forward_and_build_pending(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            store = StateStore(paths)  # type: ignore[arg-type]
            _seed_passed(store, "web-0001-0001.json", "web-0001", ["design", "implement"])

            runner = self._make_runner_with_fake_invoke(paths)
            runner.process_one("worker-02", dry_run=False)

            # Examine events written AFTER the new claim event.
            latest_claim = runner.state.latest_claim_event("web-0001-0001.json")
            self.assertIsNotNone(latest_claim)
            assert latest_claim is not None
            events = runner.state.events_for_shard(
                "web-0001-0001.json"
            )
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

            store = StateStore(paths)  # type: ignore[arg-type]
            _seed_passed(
                store,
                "web-0001-0001.json",
                "web-0001",
                ["design", "implement", "build", "validate", "document"],
            )

            runner = self._make_runner_with_fake_invoke(paths)
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
            stage_set = {
                (event["stage"], event["status"], event["challenge_id"]) for event in window
            }
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

            store = StateStore(paths)  # type: ignore[arg-type]
            _seed_passed(
                store,
                "web-0001-0001.json",
                "web-0001",
                ["design", "implement", "build"],
            )

            runner = self._make_runner_with_fake_invoke(paths)
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

            outcome = runner.process_one("worker-01", dry_run=False)
            self.assertEqual(outcome["status"], "failed")
            # Shard moved out of running into failed.
            failed_path = paths.shards / "failed"
            files = sorted(p.name for p in failed_path.glob("*.json"))
            self.assertTrue(any("web-0001-0001" in name for name in files))


class ShardNameNormalizationTests(unittest.TestCase):
    def test_events_use_original_shard_name_not_worker_suffix(self):
        with TemporaryDirectory() as tmp:
            paths = _Paths(root=Path(tmp))
            paths.initialize()
            _copy_real_prompt(paths)
            _make_web_challenge(paths, "web-0001")
            _make_shard(paths, "web-0001-0001.json", ["web-0001"])

            runner = HermesRunner(paths, image_exists=lambda _: True)  # type: ignore[arg-type]

            def fake_invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None) -> int:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text("ok", encoding="utf-8")
                return 0

            runner._invoke = fake_invoke  # type: ignore[assignment]
            runner.validator.validate_challenge = lambda cid: {  # type: ignore[assignment]
                "challenge_id": cid,
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
            suffixed = runner.state.events_for_shard(
                "web-0001-0001.worker-42.json"
            )
            self.assertEqual(suffixed, [])


if __name__ == "__main__":
    unittest.main()
