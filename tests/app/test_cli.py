"""CLI surface tests for the run timeout precedence and the durations command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = ROOT / "src" / "cli.py"


def _run_cli(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env is not None:
        full_env.update(env)
    full_env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, str(CLI_SCRIPT)] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=full_env,
    )


def _prepare_workspace(tmp: Path) -> None:
    """Copy the bits the CLI needs to run init/run dry-run."""
    for src in (ROOT / "prompts", ROOT / "skills", ROOT / "generation-profiles.json"):
        target = tmp / src.name
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            for child in src.rglob("*"):
                if child.is_dir():
                    continue
                rel = child.relative_to(src)
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(child.read_bytes())
        else:
            target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _seed_pending_shard(tmp: Path) -> None:
    shards = tmp / "work" / "shards" / "pending"
    shards.mkdir(parents=True, exist_ok=True)
    (shards / "web-0001-0001.json").write_text(
        json.dumps({"challenges": [{"id": "web-0001"}]}),
        encoding="utf-8",
    )


class CLIHelpAndParserTests(unittest.TestCase):
    def test_run_help_has_timeout_and_no_validate(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_cli(["run", "--help"], cwd=tmp_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--timeout", result.stdout)
        self.assertIn("--category", result.stdout)
        self.assertIn("--build-attempt", result.stdout)
        self.assertIn("--build-attempts-only", result.stdout)
        self.assertNotIn("--validate", result.stdout)

    def test_validate_subcommand_still_exists(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_cli(["validate", "--help"], cwd=tmp_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: challenge-factory validate", result.stdout)

    def test_dry_run_and_loop_mutually_exclusive(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _prepare_workspace(tmp_path)
            result = _run_cli(
                ["run", "--worker", "dry-01", "--dry-run", "--loop"],
                cwd=tmp_path,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutually exclusive", result.stderr)

    def test_build_attempt_and_loop_are_mutually_exclusive(self):
        with TemporaryDirectory() as tmp:
            result = _run_cli(
                [
                    "run",
                    "--worker",
                    "worker-1",
                    "--build-attempt",
                    "11111111-1111-1111-1111-111111111111",
                    "--loop",
                ],
                cwd=Path(tmp),
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutually exclusive", result.stderr)

    def test_build_attempts_only_requires_category(self):
        with TemporaryDirectory() as tmp:
            result = _run_cli(
                ["run", "--worker", "worker-1", "--build-attempts-only"],
                cwd=Path(tmp),
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --category", result.stderr)

    def test_invalid_category_and_uuid_exit_before_queue_mutation(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_pending_shard(tmp_path)
            pending = tmp_path / "work" / "shards" / "pending" / "web-0001-0001.json"
            category = _run_cli(
                ["run", "--worker", "worker-1", "--category", "crypto"],
                cwd=tmp_path,
            )
            invalid_uuid = _run_cli(
                ["run", "--worker", "worker-1", "--build-attempt", "invalid"],
                cwd=tmp_path,
            )
            self.assertEqual(category.returncode, 2)
            self.assertEqual(invalid_uuid.returncode, 2)
            self.assertTrue(pending.exists())


class CLITimeoutPrecedenceTests(unittest.TestCase):
    def _expect_first_line(self, tmp_path: Path, args: list[str], env: dict[str, str], expected: str) -> None:
        _prepare_workspace(tmp_path)
        _seed_pending_shard(tmp_path)
        # Use --dry-run so no Hermes invocation is required.
        result = _run_cli(
            ["run", "--worker", "dry-01", "--dry-run"] + args,
            cwd=tmp_path,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        first_line = result.stdout.splitlines()[0]
        self.assertEqual(first_line, expected)

    def test_default_when_no_flag_or_env(self):
        with TemporaryDirectory() as tmp:
            self._expect_first_line(
                Path(tmp),
                args=[],
                env={"HERMES_TIMEOUT": ""},  # cleared
                expected="effective_timeout=1500 source=default",
            )

    def test_env_used_when_no_cli_flag(self):
        with TemporaryDirectory() as tmp:
            self._expect_first_line(
                Path(tmp),
                args=[],
                env={"HERMES_TIMEOUT": "1700"},
                expected="effective_timeout=1700 source=env",
            )

    def test_cli_overrides_env(self):
        with TemporaryDirectory() as tmp:
            self._expect_first_line(
                Path(tmp),
                args=["--timeout", "1800"],
                env={"HERMES_TIMEOUT": "1700"},
                expected="effective_timeout=1800 source=cli",
            )

    def test_invalid_cli_timeout_fails(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _prepare_workspace(tmp_path)
            for raw in ("0", "-1"):
                result = _run_cli(
                    ["run", "--worker", "dry-01", "--dry-run", "--timeout", raw],
                    cwd=tmp_path,
                )
                self.assertEqual(result.returncode, 2, f"raw={raw}")

    def test_invalid_env_timeout_fails(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _prepare_workspace(tmp_path)
            _seed_pending_shard(tmp_path)
            for raw in ("abc", "0", "-1"):
                result = _run_cli(
                    ["run", "--worker", "dry-01", "--dry-run"],
                    cwd=tmp_path,
                    env={"HERMES_TIMEOUT": raw},
                )
                self.assertEqual(result.returncode, 2, f"raw={raw} stderr={result.stderr}")


class DurationsCLIInputTests(unittest.TestCase):
    def test_valid_basename_returns_json(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "work").mkdir(parents=True, exist_ok=True)
            result = _run_cli(
                [
                    "durations",
                    "--challenge",
                    "web-0001",
                    "--shard",
                    "web-0001-0001.json",
                ],
                cwd=tmp_path,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = json.loads(result.stdout)
            for stage in ("design", "implement", "build", "validate", "document"):
                self.assertIn(stage, parsed)

    def test_rejects_path_with_directory(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "work").mkdir(parents=True, exist_ok=True)
            result = _run_cli(
                [
                    "durations",
                    "--challenge",
                    "web-0001",
                    "--shard",
                    "running/web-0001-0001.json",
                ],
                cwd=tmp_path,
            )
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_rejects_worker_suffix(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "work").mkdir(parents=True, exist_ok=True)
            result = _run_cli(
                [
                    "durations",
                    "--challenge",
                    "web-0001",
                    "--shard",
                    "web-0001-0001.worker-02.json",
                ],
                cwd=tmp_path,
            )
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_rejects_missing_json_suffix(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "work").mkdir(parents=True, exist_ok=True)
            result = _run_cli(
                [
                    "durations",
                    "--challenge",
                    "web-0001",
                    "--shard",
                    "web-0001-0001",
                ],
                cwd=tmp_path,
            )
            self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
