import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes import HermesRunner
from paths import ProjectPaths


class HermesRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        repository = Path(self.temp.name)
        self.paths = ProjectPaths(
            root=repository / "challenge-factory",
            repository=repository,
        )
        self.paths.initialize()
        self.paths.prompt_template.parent.mkdir(parents=True, exist_ok=True)
        self.paths.prompt_template.write_text(
            "{design_skill}\n{progress_command}\n{shard_name}\n{worker}\n",
            encoding="utf-8",
        )

    def test_prompt_contains_skill_and_progress_command(self):
        shard = self.paths.shards / "running" / "web-0001-0001.worker.json"
        report = self.paths.reports / "web.report.json"

        prompt = HermesRunner(self.paths).render_prompt(shard, report, "worker-1")

        self.assertIn("skills", prompt)
        self.assertIn("cli.py", prompt)
        self.assertIn("progress", prompt)
        self.assertIn(shard.name, prompt)
        self.assertIn("worker-1", prompt)

    def test_uses_uvx_fallback_when_hermes_is_not_on_path(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("hermes.shutil.which", side_effect=[None, "C:/tools/uvx.exe"]),
            patch("hermes.Path.home", return_value=Path("C:/Users/test")),
            patch("hermes.Path.exists", return_value=True),
        ):
            arguments = HermesRunner._hermes_arguments()

        self.assertEqual(arguments[0], "C:/tools/uvx.exe")
        self.assertIn("hermes-agent", arguments)
        self.assertEqual(arguments[-5:], ["hermes", "chat", "-Q", "--yolo", "-q"])

    def test_uses_uvx_fallback_without_windows_python(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("hermes.shutil.which", side_effect=[None, "/opt/homebrew/bin/uvx"]),
            patch("hermes.Path.exists", return_value=False),
        ):
            arguments = HermesRunner._hermes_arguments()

        self.assertEqual(arguments[0], "/opt/homebrew/bin/uvx")
        self.assertNotIn("--python", arguments)
        self.assertEqual(arguments[-5:], ["hermes", "chat", "-Q", "--yolo", "-q"])

    def test_maps_legacy_custom_provider_to_environment(self):
        self.paths.hermes_home.mkdir(parents=True, exist_ok=True)
        (self.paths.hermes_home / "config.yaml").write_text(
            "model:\n"
            "  provider: custom\n"
            "  default: glm-5\n"
            "  base_url: http://model.example/v1\n"
            "  api_key: secret-value\n",
            encoding="utf-8",
        )
        environment = {}

        configured = HermesRunner(self.paths)._apply_legacy_custom_provider(
            environment
        )

        self.assertTrue(configured)
        self.assertEqual(environment["CUSTOM_BASE_URL"], "http://model.example/v1")
        self.assertEqual(environment["CUSTOM_API_KEY"], "secret-value")

    def test_removes_conflicting_custom_credential_pool(self):
        self.paths.hermes_home.mkdir(parents=True, exist_ok=True)
        auth_path = self.paths.hermes_home / "auth.json"
        auth_path.write_text(
            '{"credential_pool":{"custom:old":{"api_key":"old"},'
            '"openrouter":{"api_key":"keep"}}}\n',
            encoding="utf-8",
        )

        changed = HermesRunner(self.paths)._remove_conflicting_custom_pool()
        payload = __import__("json").loads(auth_path.read_text(encoding="utf-8"))

        self.assertTrue(changed)
        self.assertNotIn("custom:old", payload["credential_pool"])
        self.assertIn("openrouter", payload["credential_pool"])

    def test_invoke_returns_timeout_status(self):
        runner = HermesRunner(self.paths)
        log = self.paths.logs / "timeout.log"
        with (
            patch.dict("os.environ", {"HERMES_TIMEOUT": "1", "HERMES_CMD": "hermes"}),
            patch.object(runner, "_apply_legacy_custom_provider", return_value=False),
            patch(
                "hermes.subprocess.run",
                side_effect=__import__("subprocess").TimeoutExpired("hermes", 1),
            ),
        ):
            returncode = runner._invoke("prompt", log, dry_run=False)

        self.assertEqual(returncode, 124)
        self.assertIn("timed out after 1s", log.read_text(encoding="utf-8"))

    def _write_shard(self, name: str, challenges: list[dict]) -> Path:
        from jsonio import write_json

        path = self.paths.shards / "pending" / name
        write_json(path, {"challenges": challenges})
        return path

    def _write_metadata(self, challenge_id: str, category: str, build_status: str) -> Path:
        from jsonio import write_json

        path = (
            self.paths.challenges
            / category
            / f"{challenge_id}-demo"
            / "metadata.json"
        )
        write_json(
            path,
            {
                "id": challenge_id,
                "title": "Demo",
                "category": category,
                "difficulty": "easy",
                "build_status": build_status,
                "flag": "flag{demo}",
            },
        )
        return path

    def test_process_one_recovers_when_timeout_after_artifacts_built(self):
        self._write_shard(
            "web-0001-0001.json",
            [{"id": "web-0001", "category": "web"}],
        )
        self._write_metadata("web-0001", "web", "passed")

        runner = HermesRunner(self.paths)
        with (
            patch.object(runner, "_invoke", return_value=124),
            patch.object(runner, "render_prompt", return_value="prompt"),
        ):
            outcome = runner.process_one("worker-1", validate=False, dry_run=False)

        self.assertEqual(outcome["status"], "done")
        self.assertTrue(any(self.paths.shards.joinpath("done").iterdir()))

    def test_process_one_fails_when_timeout_without_artifacts(self):
        self._write_shard(
            "web-0002-0002.json",
            [{"id": "web-0002", "category": "web"}],
        )

        runner = HermesRunner(self.paths)
        with (
            patch.object(runner, "_invoke", return_value=124),
            patch.object(runner, "render_prompt", return_value="prompt"),
        ):
            outcome = runner.process_one("worker-1", validate=False, dry_run=False)

        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["returncode"], 124)

    def test_process_one_fails_when_timeout_with_partial_artifacts(self):
        self._write_shard(
            "web-0003-0003.json",
            [
                {"id": "web-0003", "category": "web"},
                {"id": "web-0004", "category": "web"},
            ],
        )
        # Only one of two has a passing build.
        self._write_metadata("web-0003", "web", "passed")
        self._write_metadata("web-0004", "web", "failed")

        runner = HermesRunner(self.paths)
        with (
            patch.object(runner, "_invoke", return_value=124),
            patch.object(runner, "render_prompt", return_value="prompt"),
        ):
            outcome = runner.process_one("worker-1", validate=False, dry_run=False)

        self.assertEqual(outcome["status"], "failed")
