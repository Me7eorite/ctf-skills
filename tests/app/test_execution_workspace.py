from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from core.jsonio import write_json
from core.paths import ProjectPaths
from hermes.runner import HermesRunner
from hermes.workspace import (
    WorkspacePreflightError,
    derive_workspace_id,
    import_workspace_report,
    preflight_workspace,
    prepare_workspace,
)


class ExecutionWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = ProjectPaths(root=root, repository=root)
        self.paths.initialize()
        self.paths.generation_profile.write_text("{}\n", encoding="utf-8")
        self.paths.design_skill.parent.mkdir(parents=True, exist_ok=True)
        self.paths.design_skill.write_text("# Design\n", encoding="utf-8")
        self.paths.design_references.mkdir(parents=True, exist_ok=True)
        for filename in (
            "web-design.md",
            "pwn-design.md",
            "reverse-design.md",
            "quality-gate.md",
            "spec-template.md",
            "delivery-format.md",
        ):
            (self.paths.design_references / filename).write_text(
                f"# {filename}\n", encoding="utf-8"
            )

    def _running_shard(self, payload: dict, name: str = "claimed.worker.json") -> Path:
        shard = self.paths.shards / "running" / name
        write_json(shard, payload)
        return shard

    def test_initialize_creates_executions_root(self) -> None:
        self.assertEqual(self.paths.executions, self.paths.work / "executions")
        self.assertTrue(self.paths.executions.is_dir())

    def test_derive_workspace_id_uses_attempt_or_manual_uuid(self) -> None:
        attempt_id = uuid4()

        self.assertEqual(
            derive_workspace_id({"build_attempt_id": str(attempt_id)}),
            str(attempt_id),
        )
        manual = derive_workspace_id({})
        self.assertTrue(manual.startswith("manual-"))
        uuid4_type = __import__("uuid").UUID(manual.removeprefix("manual-"))
        self.assertEqual(str(uuid4_type), manual.removeprefix("manual-"))

    def test_invalid_attempt_id_cannot_escape_executions(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a UUID"):
            derive_workspace_id({"build_attempt_id": "../../outside"})

    def test_prepare_creates_clean_layout_snapshot_and_manifest(self) -> None:
        attempt_id = uuid4()
        design_task_id = uuid4()
        payload = {
            "build_attempt_id": str(attempt_id),
            "design_task_id": str(design_task_id),
            "challenges": [{"id": "web-0001", "category": "web"}],
        }
        shard = self._running_shard(payload)
        stale_root = self.paths.executions / str(attempt_id)
        stale_root.mkdir()
        (stale_root / "stale.txt").write_text("stale", encoding="utf-8")
        now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)

        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name=f"{attempt_id}.json",
            worker="worker-1",
            now=now,
        )

        self.assertFalse((workspace.root / "stale.txt").exists())
        for name in ("input", "references", "output", "logs", "bin"):
            self.assertTrue((workspace.root / name).is_dir())
        snapshot = workspace.input / "shard.json"
        self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8")), payload)
        manifest = json.loads(
            (workspace.input / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["workspace_id"], str(attempt_id))
        self.assertEqual(manifest["original_shard_basename"], f"{attempt_id}.json")
        self.assertEqual(manifest["running_shard_basename"], shard.name)
        self.assertEqual(manifest["worker"], "worker-1")
        self.assertEqual(manifest["category"], "web")
        self.assertEqual(manifest["design_task_id"], str(design_task_id))
        expected_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        self.assertEqual(
            manifest["input_hashes"]["input/shard.json"],
            f"sha256:{expected_hash}",
        )

    def test_manual_gc_removes_old_and_empty_but_keeps_fresh_and_attributed(self) -> None:
        now = datetime.now(timezone.utc)
        old = self.paths.executions / "manual-old"
        fresh = self.paths.executions / "manual-fresh"
        empty = self.paths.executions / "manual-empty"
        attributed = self.paths.executions / str(uuid4())
        for directory in (old, fresh, empty, attributed):
            directory.mkdir()
        for directory in (old, fresh, attributed):
            (directory / "keep.txt").write_text("x", encoding="utf-8")
        (fresh / "input").mkdir()
        write_json(fresh / "input" / "manifest.json", {"workspace_id": "manual-fresh"})
        old_timestamp = (now - timedelta(days=8)).timestamp()
        os.utime(old, (old_timestamp, old_timestamp))
        shard = self._running_shard(
            {"challenges": [{"id": "pwn-0001", "category": "pwn"}]}
        )

        prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="legacy.json",
            worker="worker-1",
            now=now,
        )

        self.assertFalse(old.exists())
        self.assertFalse(empty.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(attributed.exists())

    def test_manual_gc_error_is_non_blocking(self) -> None:
        stale = self.paths.executions / "manual-stale"
        stale.mkdir()
        shard = self._running_shard(
            {"challenges": [{"id": "re-0001", "category": "re"}]}
        )
        real_rmtree = __import__("shutil").rmtree

        def fail_stale(path: Path) -> None:
            if Path(path) == stale:
                raise PermissionError("busy")
            real_rmtree(path)

        with patch("hermes.workspace.shutil.rmtree", side_effect=fail_stale):
            workspace = prepare_workspace(
                self.paths,
                shard=shard,
                original_shard_name="legacy.json",
                worker="worker-1",
            )

        self.assertTrue(workspace.root.is_dir())
        self.assertTrue(stale.exists())

    def test_report_import_copies_workspace_report_to_legacy_path(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="legacy.json",
            worker="worker-1",
        )
        write_json(workspace.report, {"runner_status": "from-workspace"})
        legacy_report = self.paths.reports / "legacy.report.json"

        imported = import_workspace_report(workspace, legacy_report)

        self.assertTrue(imported)
        self.assertEqual(
            json.loads(legacy_report.read_text(encoding="utf-8"))["runner_status"],
            "from-workspace",
        )

    def test_materializes_only_selected_category_context_as_regular_files(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )

        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )

        self.assertTrue((workspace.input / "generation-profiles.json").is_file())
        reference_root = workspace.references / "design-challenges"
        self.assertTrue((reference_root / "SKILL.md").is_file())
        self.assertTrue((reference_root / "references" / "web-design.md").is_file())
        self.assertFalse((reference_root / "references" / "pwn-design.md").exists())
        self.assertFalse((reference_root / "references" / "reverse-design.md").exists())
        self.assertFalse(any(path.is_symlink() for path in workspace.root.rglob("*")))
        manifest = json.loads(workspace.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["allowed_static_reference_roots"], [])

    def test_preflight_missing_profile_includes_recovery_command(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )

        with self.assertRaisesRegex(
            WorkspacePreflightError,
            "hermes profile create cf-web",
        ):
            preflight_workspace(
                workspace,
                profile_name="cf-web",
                profile_exists=lambda _: False,
            )

    def test_preflight_rejects_malformed_snapshot(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        (workspace.input / "shard.json").write_text("{", encoding="utf-8")

        with self.assertRaisesRegex(WorkspacePreflightError, "readable JSON"):
            preflight_workspace(
                workspace,
                profile_name="cf-web",
                profile_exists=lambda _: True,
            )

    def test_preflight_rejects_category_profile_mismatch(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )

        with self.assertRaisesRegex(WorkspacePreflightError, "profile/category mismatch"):
            preflight_workspace(
                workspace,
                profile_name="cf-pwn",
                profile_exists=lambda _: True,
            )

    def test_preflight_rejects_unrelated_challenge_artifact(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        (workspace.output / "pwn-9999-stale").mkdir()

        with self.assertRaisesRegex(WorkspacePreflightError, "unrelated"):
            preflight_workspace(
                workspace,
                profile_name="cf-web",
                profile_exists=lambda _: True,
            )

    def test_preflight_rejects_every_reference_symlink_for_copy_only_policy(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        outside = self.paths.root / "outside-reference.md"
        outside.write_text("outside", encoding="utf-8")
        (workspace.references / "injected.md").symlink_to(outside)

        with self.assertRaisesRegex(WorkspacePreflightError, "unsafe reference symlink"):
            preflight_workspace(
                workspace,
                profile_name="cf-web",
                profile_exists=lambda _: True,
            )

    def test_runner_preflight_failure_fails_only_claimed_shard(self) -> None:
        self.paths.prompt_template.parent.mkdir(parents=True, exist_ok=True)
        self.paths.prompt_template.write_text("prompt\n", encoding="utf-8")
        write_json(
            self.paths.shards / "pending" / "a-web.json",
            {"challenges": [{"id": "web-0001", "category": "web"}]},
        )
        unrelated = self.paths.shards / "pending" / "b-pwn.json"
        write_json(
            unrelated,
            {"challenges": [{"id": "pwn-0001", "category": "pwn"}]},
        )
        runner = HermesRunner(
            self.paths,
            image_exists=lambda _: False,
            profile_exists=lambda _: False,
        )

        with patch.object(runner, "_invoke") as invoke:
            outcome = runner.process_one("worker-1", dry_run=False)

        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["failure_type"], "infrastructure")
        self.assertIn("hermes profile create cf-web", outcome["error"])
        invoke.assert_not_called()
        self.assertTrue((self.paths.shards / "failed" / "a-web.json").is_file())
        self.assertTrue(unrelated.is_file())

    def test_real_runner_uses_workspace_log_and_relative_report_prompt(self) -> None:
        self.paths.prompt_template.parent.mkdir(parents=True, exist_ok=True)
        self.paths.prompt_template.write_text("{report_path}\n", encoding="utf-8")
        shard = self.paths.shards / "pending" / "legacy.json"
        write_json(
            shard,
            {"challenges": [{"id": "web-0001", "category": "web"}]},
        )
        runner = HermesRunner(
            self.paths,
            image_exists=lambda _: False,
            profile_exists=lambda _: True,
        )
        observed: dict[str, object] = {}

        def invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None) -> int:
            observed.update(prompt=prompt, log=log, dry_run=dry_run)
            return 2

        with patch.object(runner, "_invoke", side_effect=invoke):
            outcome = runner.process_one("worker-1", dry_run=False)

        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(observed["prompt"], "./logs/report.json\n")
        log = observed["log"]
        self.assertIsInstance(log, Path)
        self.assertEqual(log.name, "hermes.log")
        self.assertEqual(log.parent.parent.parent, self.paths.executions)


if __name__ == "__main__":
    unittest.main()
