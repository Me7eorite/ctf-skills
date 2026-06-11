"""Tests for the host-side resume planner in ``domain.resume``."""

from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from core.state import StateStore
from domain.resume import (
    ShardResumePlan,
    carry_forward_message,
    compute_resume_plan,
    document_evidence,
    find_challenge_directory,
    validator_message,
)


@dataclass(frozen=True)
class _Paths:
    root: Path

    @property
    def state_database(self) -> Path:
        return self.root / "state.sqlite3"

    @property
    def challenges(self) -> Path:
        return self.root / "challenges"


def _make_store(tmp: Path) -> StateStore:
    return StateStore(_Paths(root=tmp))  # type: ignore[arg-type]


def _make_paths(tmp: Path) -> _Paths:
    paths = _Paths(root=tmp)
    paths.challenges.mkdir(parents=True, exist_ok=True)
    return paths


def _make_web_challenge_dir(
    paths: _Paths,
    challenge_id: str,
    *,
    slug: str = "demo",
    docker_image: str = "demo:latest",
    build_command: str = "docker build -t demo:latest .",
    solve_status: str = "passed",
    build_status: str = "passed",
) -> Path:
    directory = paths.challenges / "web" / f"{challenge_id}-{slug}"
    deploy = directory / "deploy"
    src = deploy / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "app.py").write_text("print('vulnerable')\n", encoding="utf-8")
    (deploy / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    (deploy / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (directory / "validate.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    solve_dir = directory / "solve"
    solve_dir.mkdir(parents=True, exist_ok=True)
    (solve_dir / "solve.py").write_text("print('solve')\n", encoding="utf-8")
    writeup_dir = directory / "writeup"
    writeup_dir.mkdir(parents=True, exist_ok=True)
    (writeup_dir / "wp.md").write_text(
        "# title\n\n## Background\n\n" + ("x" * 600) + "\n\n## Solution\n\nmore\n",
        encoding="utf-8",
    )
    (directory / "README.md").write_text(
        "# Title\n\n## Setup\n\n"
        + ("y" * 600)
        + "\n\n## Run\n\nrun details\n",
        encoding="utf-8",
    )
    metadata = {
        "id": challenge_id,
        "category": "web",
        "docker_image": docker_image,
        "build_command": build_command,
        "build_status": build_status,
        "solve_status": solve_status,
        "flag": "flag{example}",
    }
    (directory / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return directory


def _make_re_challenge_dir(
    paths: _Paths,
    challenge_id: str,
    *,
    slug: str = "re",
    artifact_name: str = "checker",
) -> Path:
    directory = paths.challenges / "re" / f"{challenge_id}-{slug}"
    (directory / "src").mkdir(parents=True, exist_ok=True)
    (directory / "src" / "main.c").write_text(
        "int main(){return 0;}\n", encoding="utf-8"
    )
    dist = directory / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / artifact_name).write_bytes(b"\x7fELFnotreal")
    sha = hashlib.sha256(b"\x7fELFnotreal").hexdigest()
    metadata = {
        "id": challenge_id,
        "category": "re",
        "build_command": "gcc src/main.c -o dist/checker",
        "build_status": "passed",
        "solve_status": "passed",
        "artifact": f"dist/{artifact_name}",
        "artifact_sha256": sha,
        "flag": "flag{example}",
    }
    (directory / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    (directory / "validate.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (directory / "solve").mkdir(parents=True, exist_ok=True)
    (directory / "solve" / "solve.py").write_text("pass\n", encoding="utf-8")
    (directory / "writeup").mkdir(parents=True, exist_ok=True)
    (directory / "writeup" / "wp.md").write_text(
        "# t\n\n## A\n\n" + ("x" * 600) + "\n\n## B\nmore\n",
        encoding="utf-8",
    )
    (directory / "README.md").write_text(
        "# t\n\n## A\n\n" + ("y" * 600) + "\n\n## B\nmore\n",
        encoding="utf-8",
    )
    return directory


def _seed_passed_events(
    store: StateStore,
    shard: str,
    challenge_id: str,
    stages: list[str],
) -> list[dict]:
    """Seed a previous claim window: shard queued, then passed events for stages."""
    events = []
    events.append(
        store.record(shard=shard, stage="queued", status="running")
    )
    for stage in stages:
        running = store.record(
            shard=shard,
            stage=stage,
            status="running",
            challenge_id=challenge_id,
        )
        events.append(running)
        passed = store.record(
            shard=shard,
            stage=stage,
            status="passed",
            challenge_id=challenge_id,
        )
        events.append(passed)
    return events


class FindChallengeDirectoryTests(unittest.TestCase):
    def test_exact_one_match(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            directory = _make_web_challenge_dir(paths, "web-0001")
            lookup = find_challenge_directory(paths, "web-0001")  # type: ignore[arg-type]
            self.assertEqual(lookup.status, "ok")
            self.assertEqual(lookup.directory, directory)

    def test_missing(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            lookup = find_challenge_directory(paths, "web-0001")  # type: ignore[arg-type]
            self.assertEqual(lookup.status, "missing_challenge")
            self.assertIsNone(lookup.directory)

    def test_ambiguous(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _make_web_challenge_dir(paths, "web-0001", slug="alpha")
            _make_web_challenge_dir(paths, "web-0001", slug="beta")
            lookup = find_challenge_directory(paths, "web-0001")  # type: ignore[arg-type]
            self.assertEqual(lookup.status, "ambiguous_challenge")
            self.assertIsNone(lookup.directory)


class ComputeResumePlanTests(unittest.TestCase):
    def test_no_history_resumes_from_design(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _make_web_challenge_dir(paths, "web-0001")
            store = _make_store(Path(tmp))

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["web-0001"],
                image_exists=lambda image: True,
            )
            self.assertIsInstance(plan, ShardResumePlan)
            challenge = plan.challenges[0]
            self.assertEqual(challenge.skipped_stages, ())
            self.assertEqual(challenge.first_pending_stage, "design")

    def test_full_skip_when_all_evidence_present(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _make_web_challenge_dir(paths, "web-0001")
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store,
                "s.json",
                "web-0001",
                ["design", "implement", "build", "validate", "document"],
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["web-0001"],
                image_exists=lambda image: True,
            )
            challenge = plan.challenges[0]
            self.assertEqual(
                challenge.skipped_stages,
                ("design", "implement", "build", "validate", "document"),
            )
            self.assertTrue(challenge.all_skipped)
            self.assertIsNone(challenge.first_pending_stage)

    def test_missing_docker_image_stops_at_build(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _make_web_challenge_dir(paths, "web-0001")
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store,
                "s.json",
                "web-0001",
                ["design", "implement", "build"],
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["web-0001"],
                image_exists=lambda image: False,
            )
            challenge = plan.challenges[0]
            self.assertEqual(
                challenge.skipped_stages, ("design", "implement")
            )
            self.assertEqual(challenge.first_pending_stage, "build")

    def test_continuous_prefix_breaks_after_first_missing(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            directory = _make_web_challenge_dir(paths, "web-0001")
            (directory / "deploy" / "Dockerfile").unlink()
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store,
                "s.json",
                "web-0001",
                ["design", "implement", "build", "validate", "document"],
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["web-0001"],
                image_exists=lambda image: True,
            )
            challenge = plan.challenges[0]
            self.assertEqual(challenge.skipped_stages, ("design",))
            self.assertEqual(challenge.first_pending_stage, "implement")

    def test_latest_event_supersedes_earlier_passed(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _make_web_challenge_dir(paths, "web-0001")
            store = _make_store(Path(tmp))
            store.record(shard="s.json", stage="queued", status="running")
            store.record(
                shard="s.json",
                stage="design",
                status="passed",
                challenge_id="web-0001",
            )
            store.record(
                shard="s.json",
                stage="design",
                status="failed",
                challenge_id="web-0001",
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["web-0001"],
                image_exists=lambda image: True,
            )
            challenge = plan.challenges[0]
            self.assertEqual(challenge.skipped_stages, ())
            self.assertEqual(challenge.first_pending_stage, "design")

    def test_only_last_claim_window_is_considered(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _make_web_challenge_dir(paths, "web-0001")
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store,
                "s.json",
                "web-0001",
                ["design", "implement"],
            )
            # New claim resets the window. Only events after the new claim count.
            store.record(shard="s.json", stage="queued", status="running")

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["web-0001"],
                image_exists=lambda image: True,
            )
            challenge = plan.challenges[0]
            self.assertEqual(challenge.skipped_stages, ())
            self.assertEqual(challenge.first_pending_stage, "design")

    def test_missing_challenge_directory_resumes_from_design(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            store = _make_store(Path(tmp))

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["web-0001"],
                image_exists=lambda image: True,
            )
            challenge = plan.challenges[0]
            self.assertEqual(challenge.lookup_status, "missing_challenge")
            self.assertEqual(challenge.skipped_stages, ())
            self.assertEqual(challenge.first_pending_stage, "design")


class ReverseArtifactEvidenceTests(unittest.TestCase):
    def test_safe_path_with_matching_sha256_passes(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _make_re_challenge_dir(paths, "re-0001")
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store, "s.json", "re-0001", ["design", "implement", "build"]
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["re-0001"],
                image_exists=lambda image: True,
            )
            self.assertEqual(
                plan.challenges[0].skipped_stages,
                ("design", "implement", "build"),
            )

    def test_absolute_artifact_path_is_rejected(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            challenge_dir = _make_re_challenge_dir(paths, "re-0001")
            metadata = json.loads((challenge_dir / "metadata.json").read_text())
            metadata["artifact"] = "/etc/passwd"
            (challenge_dir / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store, "s.json", "re-0001", ["design", "implement", "build"]
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["re-0001"],
                image_exists=lambda image: True,
            )
            self.assertEqual(
                plan.challenges[0].skipped_stages, ("design", "implement")
            )

    def test_dotdot_artifact_path_is_rejected(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            challenge_dir = _make_re_challenge_dir(paths, "re-0001")
            metadata = json.loads((challenge_dir / "metadata.json").read_text())
            metadata["artifact"] = "../dist/checker"
            (challenge_dir / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store, "s.json", "re-0001", ["design", "implement", "build"]
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["re-0001"],
                image_exists=lambda image: True,
            )
            self.assertEqual(
                plan.challenges[0].skipped_stages, ("design", "implement")
            )

    def test_artifact_outside_dist_is_rejected(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            challenge_dir = _make_re_challenge_dir(paths, "re-0001")
            elsewhere = challenge_dir / "side" / "checker"
            elsewhere.parent.mkdir(parents=True, exist_ok=True)
            elsewhere.write_bytes(b"\x7fELFnotreal")
            metadata = json.loads((challenge_dir / "metadata.json").read_text())
            metadata["artifact"] = "side/checker"
            (challenge_dir / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store, "s.json", "re-0001", ["design", "implement", "build"]
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["re-0001"],
                image_exists=lambda image: True,
            )
            self.assertEqual(
                plan.challenges[0].skipped_stages, ("design", "implement")
            )

    def test_sha256_mismatch_rejected(self):
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            challenge_dir = _make_re_challenge_dir(paths, "re-0001")
            metadata = json.loads((challenge_dir / "metadata.json").read_text())
            metadata["artifact_sha256"] = "0" * 64
            (challenge_dir / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            store = _make_store(Path(tmp))
            _seed_passed_events(
                store, "s.json", "re-0001", ["design", "implement", "build"]
            )

            plan = compute_resume_plan(
                state=store,
                paths=paths,  # type: ignore[arg-type]
                shard="s.json",
                challenge_ids=["re-0001"],
                image_exists=lambda image: True,
            )
            self.assertEqual(
                plan.challenges[0].skipped_stages, ("design", "implement")
            )


class DocumentEvidenceTests(unittest.TestCase):
    def test_missing_file_fails(self):
        with TemporaryDirectory() as tmp:
            challenge_dir = Path(tmp) / "c"
            challenge_dir.mkdir()
            self.assertFalse(document_evidence(challenge_dir))

    def test_too_small_fails(self):
        with TemporaryDirectory() as tmp:
            challenge_dir = Path(tmp) / "c"
            (challenge_dir / "writeup").mkdir(parents=True)
            (challenge_dir / "writeup" / "wp.md").write_text(
                "## a\n## b\n", encoding="utf-8"
            )
            (challenge_dir / "README.md").write_text(
                "## a\n## b\n", encoding="utf-8"
            )
            self.assertFalse(document_evidence(challenge_dir))

    def test_too_few_headings_fails(self):
        with TemporaryDirectory() as tmp:
            challenge_dir = Path(tmp) / "c"
            (challenge_dir / "writeup").mkdir(parents=True)
            big = "x" * 600
            (challenge_dir / "writeup" / "wp.md").write_text(
                f"## only\n{big}\n", encoding="utf-8"
            )
            (challenge_dir / "README.md").write_text(
                f"## only\n{big}\n", encoding="utf-8"
            )
            self.assertFalse(document_evidence(challenge_dir))

    def test_meets_thresholds_passes(self):
        with TemporaryDirectory() as tmp:
            challenge_dir = Path(tmp) / "c"
            (challenge_dir / "writeup").mkdir(parents=True)
            big = "x" * 600
            (challenge_dir / "writeup" / "wp.md").write_text(
                f"## a\n## b\n{big}\n", encoding="utf-8"
            )
            (challenge_dir / "README.md").write_text(
                f"## a\n## b\n{big}\n", encoding="utf-8"
            )
            self.assertTrue(document_evidence(challenge_dir))


class MessageFormatTests(unittest.TestCase):
    def test_carry_forward_message_has_prefix_and_source(self):
        msg = carry_forward_message("design", 42)
        self.assertTrue(msg.startswith("carry-forward:"))
        self.assertIn("#42", msg)
        self.assertIn("design", msg)

    def test_validator_message_has_prefix_and_status(self):
        msg = validator_message(
            status="passed", elapsed=1.5, flag_matched=True
        )
        self.assertTrue(msg.startswith("validator:"))
        self.assertIn("status=passed", msg)
        self.assertIn("elapsed=1.50s", msg)

    def test_messages_machine_distinguishable(self):
        forward = carry_forward_message("validate", 7)
        validator = validator_message(status="passed")
        self.assertTrue(forward.startswith("carry-forward:"))
        self.assertTrue(validator.startswith("validator:"))
        self.assertNotEqual(forward.split(":", 1)[0], validator.split(":", 1)[0])


if __name__ == "__main__":
    unittest.main()
