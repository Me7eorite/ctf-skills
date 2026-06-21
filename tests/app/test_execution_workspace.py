from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from core.build_timeout import shard_timeout_policy
from core.jsonio import write_json
from core.paths import ProjectPaths
from domain.reports import merge_reports
from hermes import process as hermes_process
from hermes.runner import HermesRunner
from hermes.workspace import (
    WorkspacePreflightError,
    WorkspacePromotionError,
    derive_workspace_id,
    import_workspace_report,
    materialize_resume_outputs,
    preflight_workspace,
    prepare_workspace,
    promote_claimed_outputs,
)
from hermes.workspace_progress import WorkspaceProgressTailer, materialize_progress_shim


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

    def _artifact(
        self,
        root: Path,
        challenge_id: str = "web-0001",
        category: str = "web",
        slug: str = "demo",
        marker: str = "new",
    ) -> Path:
        directory = root / category / f"{challenge_id}-{slug}"
        directory.mkdir(parents=True, exist_ok=True)
        write_json(
            directory / "metadata.json",
            {"id": challenge_id, "category": category, "marker": marker},
        )
        (directory / "artifact.txt").write_text(marker, encoding="utf-8")
        return directory

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

        def invoke(prompt: str, log: Path, dry_run: bool, *, timeout=None, **_kwargs) -> int:
            observed.update(prompt=prompt, log=log, dry_run=dry_run, timeout=timeout)
            return 2

        with patch.object(runner, "_invoke", side_effect=invoke):
            outcome = runner.process_one("worker-1", dry_run=False)

        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(observed["prompt"], "./logs/report.json\n")
        self.assertEqual(observed["timeout"], 2700)
        log = observed["log"]
        self.assertIsInstance(log, Path)
        self.assertEqual(log.name, "hermes.log")
        self.assertEqual(log.parent.parent.parent, self.paths.executions)
        manifest = json.loads((log.parent.parent / "input" / "manifest.json").read_text())
        self.assertEqual(manifest["timeout_source"], "shard_policy")

    def test_timeout_policy_by_category_and_expert_difficulty(self) -> None:
        def payload(category: str, difficulty: str | None = None) -> dict:
            challenge = {"id": f"{category}-0001", "category": category}
            if difficulty is not None:
                challenge["difficulty"] = difficulty
            return {"challenges": [challenge]}

        self.assertEqual(shard_timeout_policy(payload("re")), 1800)
        self.assertEqual(shard_timeout_policy(payload("web")), 2700)
        self.assertEqual(shard_timeout_policy(payload("pwn")), 3600)
        self.assertEqual(shard_timeout_policy(payload("pwn", "hard")), 3600)
        self.assertEqual(shard_timeout_policy(payload("pwn", "expert")), 5400)
        mixed = payload("pwn", "hard")
        mixed["challenges"].append(
            {"id": "pwn-0002", "category": "pwn", "difficulty": "expert"}
        )
        self.assertEqual(shard_timeout_policy(mixed), 5400)

    def test_workspace_prompt_uses_only_relative_runtime_paths(self) -> None:
        self.paths.prompt_template.parent.mkdir(parents=True, exist_ok=True)
        self.paths.prompt_template.write_text(
            "{shard_path}\n{challenge_dir}\n{report_path}\n{generation_profile}\n"
            "{design_skill}\n{design_references}\n{progress_command}\n",
            encoding="utf-8",
        )
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        stale = self.paths.challenges / "pwn" / "pwn-9999-stale"
        stale.mkdir(parents=True)

        prompt = HermesRunner(self.paths).render_prompt(
            shard,
            self.paths.reports / "x.report.json",
            "worker-1",
            workspace_relative=True,
        )

        self.assertIn("./input/shard.json", prompt)
        self.assertIn("./output/challenges", prompt)
        self.assertIn("./logs/report.json", prompt)
        self.assertIn("./bin/progress", prompt)
        self.assertNotIn(str(self.paths.root), prompt)
        self.assertNotIn("pwn-9999", prompt)

    def test_timeout_policy_rejects_mixed_categories(self) -> None:
        with self.assertRaisesRegex(ValueError, "one category"):
            shard_timeout_policy(
                {
                    "challenges": [
                        {"id": "web-0001", "category": "web"},
                        {"id": "pwn-0001", "category": "pwn"},
                    ]
                }
            )

    def test_progress_shim_encodes_special_characters(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        shim = materialize_progress_shim(workspace)
        message = 'fix "quoted" path \\ newline\n中文 🚀'

        subprocess.run(
            [
                str(shim),
                "--challenge",
                "web-0001",
                "--stage",
                "build",
                "--status",
                "running",
                "--message",
                message,
            ],
            cwd=workspace.root,
            check=True,
        )

        event = json.loads(
            (workspace.logs / "progress-events.jsonl").read_text(encoding="utf-8")
        )
        self.assertEqual(event["message"], message)

    def test_progress_shim_fails_when_python3_is_absent(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        shim = materialize_progress_shim(workspace)
        result = subprocess.run(
            [
                str(shim),
                "--challenge",
                "web-0001",
                "--stage",
                "build",
                "--status",
                "running",
                "--message",
                "x",
            ],
            cwd=workspace.root,
            env={"PATH": ""},
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_live_tailer_imports_before_stop(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        rows: list[dict] = []
        tailer = WorkspaceProgressTailer(
            workspace,
            lambda **kwargs: rows.append(kwargs) or kwargs,
            poll_interval=0.02,
        )
        tailer.start()
        write_json(workspace.logs / "unused.json", {})
        (workspace.logs / "progress-events.jsonl").write_text(
            '{"challenge":"web-0001","stage":"build","status":"running","message":"live"}\n',
            encoding="utf-8",
        )
        deadline = time.monotonic() + 1
        while not rows and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(rows[0]["message"], "live")
        tailer.stop_and_flush()

    def test_promotion_quarantines_claimed_and_preserves_unrelated(self) -> None:
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        shard = self._running_shard(payload)
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        old = self._artifact(self.paths.challenges, marker="old")
        unrelated = self._artifact(
            self.paths.challenges,
            challenge_id="web-9999",
            slug="keep",
            marker="keep",
        )
        self._artifact(workspace.output / "challenges", marker="new")

        promoted = promote_claimed_outputs(self.paths, workspace, payload)

        self.assertEqual(len(promoted), 1)
        self.assertEqual((promoted[0] / "artifact.txt").read_text(), "new")
        quarantine = workspace.root / "quarantine" / "web" / old.name
        self.assertEqual((quarantine / "artifact.txt").read_text(), "old")
        self.assertTrue(unrelated.is_dir())

    def test_promotion_rejects_unclaimed_and_output_symlink(self) -> None:
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        shard = self._running_shard(payload)
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        self._artifact(workspace.output / "challenges")
        self._artifact(
            workspace.output / "challenges", challenge_id="web-9999", slug="bad"
        )
        with self.assertRaisesRegex(WorkspacePromotionError, "unclaimed"):
            promote_claimed_outputs(self.paths, workspace, payload)

        bad = workspace.output / "challenges" / "web" / "web-9999-bad"
        __import__("shutil").rmtree(bad)
        (workspace.output / "linked").symlink_to(self.paths.root)
        with self.assertRaisesRegex(WorkspacePromotionError, "symlink"):
            promote_claimed_outputs(self.paths, workspace, payload)

    def test_promotion_rejects_duplicate_metadata_and_nonconforming_layout(self) -> None:
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}

        def fresh_workspace():
            shard = self._running_shard(payload)
            return prepare_workspace(
                self.paths,
                shard=shard,
                original_shard_name="web.json",
                worker="worker-1",
            )

        workspace = fresh_workspace()
        self._artifact(workspace.output / "challenges", slug="one")
        self._artifact(workspace.output / "challenges", slug="two")
        with self.assertRaisesRegex(WorkspacePromotionError, "multiple output"):
            promote_claimed_outputs(self.paths, workspace, payload)

        workspace = fresh_workspace()
        candidate = self._artifact(workspace.output / "challenges")
        write_json(candidate / "metadata.json", {"id": "web-0002", "category": "web"})
        with self.assertRaisesRegex(WorkspacePromotionError, "metadata mismatch"):
            promote_claimed_outputs(self.paths, workspace, payload)

        workspace = fresh_workspace()
        self._artifact(workspace.output, slug="wrong-place")
        with self.assertRaisesRegex(WorkspacePromotionError, "non-conforming"):
            promote_claimed_outputs(self.paths, workspace, payload)

    def test_resume_materialization_copies_claimed_only(self) -> None:
        payload = {"challenges": [{"id": "web-0001", "category": "web"}]}
        shard = self._running_shard(payload)
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        claimed = self._artifact(self.paths.challenges, marker="old")
        self._artifact(self.paths.challenges, challenge_id="web-9999", slug="keep")

        materialize_resume_outputs(self.paths, workspace, payload)

        self.assertTrue(
            (workspace.output / "challenges" / "web" / claimed.name).is_dir()
        )
        self.assertFalse(
            (workspace.output / "challenges" / "web" / "web-9999-keep").exists()
        )

    def test_report_import_remains_visible_to_merge_reports(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        write_json(workspace.report, {"status": "passed", "marker": "workspace"})
        legacy = self.paths.reports / "web.report.json"
        import_workspace_report(workspace, legacy)

        summary = json.loads(merge_reports(self.paths.reports).read_text())

        self.assertEqual(summary["reports"][0]["marker"], "workspace")

    def test_build_invoke_uses_profile_and_workspace_cwd(self) -> None:
        shard = self._running_shard(
            {"challenges": [{"id": "web-0001", "category": "web"}]}
        )
        workspace = prepare_workspace(
            self.paths,
            shard=shard,
            original_shard_name="web.json",
            worker="worker-1",
        )
        captured: dict = {}

        def fake_run(arguments, **kwargs):
            captured["arguments"] = arguments
            captured.update(kwargs)
            return type("Result", (), {"returncode": 0})()

        runner = HermesRunner(self.paths)
        with (
            patch.object(hermes_process, "hermes_arguments", return_value=["hermes", "chat", "-q"]),
            patch.object(hermes_process.subprocess, "run", side_effect=fake_run),
        ):
            result = runner._invoke(
                "prompt",
                workspace.hermes_log,
                False,
                timeout=10,
                workspace=workspace,
                profile_name="cf-web",
            )

        self.assertEqual(result, 0)
        self.assertEqual(captured["arguments"][:4], ["hermes", "-p", "cf-web", "chat"])
        self.assertEqual(captured["cwd"], workspace.root)
        self.assertNotEqual(captured["cwd"], self.paths.root)


if __name__ == "__main__":
    unittest.main()
