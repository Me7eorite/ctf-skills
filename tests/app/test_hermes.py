import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from core.paths import ProjectPaths
from hermes import HermesRunner
from hermes.prompt import render_validation_repair_prompt

ROOT = Path(__file__).resolve().parents[2]


class HermesRunnerTests(unittest.TestCase):
    def test_validation_prompt_requires_clean_stdout_and_stale_cleanup(self):
        prompt = (ROOT / "prompts" / "shard_prompt.md").read_text(encoding="utf-8")
        self.assertIn("redirect its output to stderr (`>&2`)", prompt)
        self.assertIn(
            'docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true',
            prompt,
        )

    def test_shard_prompt_requires_pwn_xinetd_chroot_launcher(self):
        prompt = (ROOT / "prompts" / "shard_prompt.md").read_text(encoding="utf-8")

        self.assertIn("./references/scaffolds/pwn/xinetd-chroot/", prompt)
        self.assertIn("xinetd + chroot + TCP socket", prompt)
        self.assertIn("server = /usr/sbin/chroot", prompt)
        self.assertIn("server_args = --userspec=ctf:ctf", prompt)
        self.assertIn("/etc/xinetd.d/ctf", prompt)

    def test_shard_prompt_keeps_pwn_chroot_setup_inside_dockerfile(self):
        prompt = (ROOT / "prompts" / "shard_prompt.md").read_text(encoding="utf-8")

        self.assertIn("ONLY inside `deploy/Dockerfile` `RUN` steps", prompt)
        self.assertIn("MUST NOT be executed on the host", prompt)
        self.assertIn("output/challenges/<category>/<id>-.../metadata.json", prompt)

    def test_repair_prompt_replays_pwn_xinetd_chroot_contract(self):
        prompt = render_validation_repair_prompt(
            attempt=1,
            max_attempts=3,
            validation_results=[],
        )

        self.assertIn("Pwn container launcher", prompt)
        self.assertIn("./references/scaffolds/pwn/xinetd-chroot/", prompt)
        self.assertIn("xinetd + chroot + TCP socket", prompt)
        self.assertIn("/usr/sbin/chroot", prompt)
        self.assertIn("--userspec=ctf:ctf", prompt)
        self.assertIn("MUST appear only as `RUN` steps in", prompt)

    def test_pwn_xinetd_chroot_scaffold_has_container_only_setup(self):
        scaffold = ROOT / "scaffolds" / "pwn" / "xinetd-chroot"
        dockerfile = (scaffold / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        compose = (scaffold / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        start_sh = (scaffold / "deploy" / "_files" / "start.sh").read_text(encoding="utf-8")
        xinetd = (scaffold / "deploy" / "_files" / "ctf.xinetd").read_text(encoding="utf-8")

        self.assertNotIn("RUN cp -R /lib* /home/ctf", dockerfile)
        self.assertIn("cp -a /lib/x86_64-linux-gnu/*.so*", dockerfile)
        self.assertIn("cp /bin/ls /home/ctf/bin", dockerfile)
        self.assertIn("Every absolute path below", dockerfile)
        self.assertIn("- FLAG={{FLAG}}", compose)
        self.assertNotIn("volumes:", compose)
        self.assertNotIn("cp -R /lib* /home/ctf", start_sh)
        self.assertNotIn("mknod /home/ctf", start_sh)
        self.assertIn("server      = /usr/sbin/chroot", xinetd)
        self.assertIn("server_args = --userspec=ctf:ctf", xinetd)
        self.assertIn("groupadd -g 1000 ctf", dockerfile)
        self.assertIn("useradd -u 1000 -g 1000 -m ctf", dockerfile)
        self.assertNotIn("ARG CTF_UID", dockerfile)
        self.assertNotIn("ARG CTF_GID", dockerfile)
        self.assertIn("container_name: {{CONTAINER_NAME}}", compose)
        self.assertNotIn("build:", compose)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        repository = Path(self.temp.name)
        self.paths = ProjectPaths(
            root=repository,
            repository=repository,
        )
        self.paths.initialize()
        self.paths.prompt_template.parent.mkdir(parents=True, exist_ok=True)
        self.paths.prompt_template.write_text(
            "{design_skill}\n{progress_command}\n{shard_name}\n{worker}\n{repair_section}\n",
            encoding="utf-8",
        )
        self.paths.generation_profile.write_text("{}\n", encoding="utf-8")
        self.paths.design_skill.parent.mkdir(parents=True, exist_ok=True)
        self.paths.design_skill.write_text("# Design\n", encoding="utf-8")
        self.paths.design_references.mkdir(parents=True, exist_ok=True)
        for filename in (
            "design-core.md",
            "category-tactics.md",
            "difficulty-rubric.md",
            "shared_generation_strategy.md",
        ):
            (self.paths.design_references / filename).write_text(
                f"# {filename}\n", encoding="utf-8"
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

    def test_runner_prompt_accepts_retry_context(self):
        shard = self.paths.shards / "running" / "web-0001-0001.worker.json"
        report = self.paths.reports / "web.report.json"

        prompt = HermesRunner(self.paths).render_prompt(
            shard,
            report,
            "worker-1",
            retry_context={"previous_error": "host build failed"},
        )

        self.assertIn("host build failed", prompt)

    def test_uses_uvx_fallback_when_hermes_is_not_on_path(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("hermes.process.shutil.which", side_effect=[None, "C:/tools/uvx.exe"]),
            patch("hermes.process.Path.home", return_value=Path("C:/Users/test")),
            patch("hermes.process.Path.exists", return_value=True),
        ):
            arguments = HermesRunner._hermes_arguments()

        self.assertEqual(arguments[0], "C:/tools/uvx.exe")
        self.assertIn("hermes-agent", arguments)
        self.assertEqual(arguments[-5:], ["hermes", "chat", "-Q", "--yolo", "-q"])

    def test_uses_uvx_fallback_without_windows_python(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("hermes.process.shutil.which", side_effect=[None, "/opt/homebrew/bin/uvx"]),
            patch("hermes.process.Path.home", return_value=Path("/Users/test")),
            patch("hermes.process.Path.exists", return_value=False),
        ):
            arguments = HermesRunner._hermes_arguments()

        self.assertEqual(arguments[0], "/opt/homebrew/bin/uvx")
        self.assertNotIn("--python", arguments)
        self.assertEqual(arguments[-5:], ["hermes", "chat", "-Q", "--yolo", "-q"])

    def test_uses_pyenv_shim_when_hermes_is_not_on_path(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("hermes.process.shutil.which", return_value=None),
            patch("hermes.process.Path.home", return_value=Path("/root")),
            patch("hermes.process.Path.is_file", return_value=True),
            patch("hermes.process.os.access", return_value=True),
        ):
            arguments = HermesRunner._hermes_arguments()

        self.assertEqual(Path(arguments[0]).as_posix(), "/root/.pyenv/shims/hermes")
        self.assertEqual(arguments[1:], ["chat", "-Q", "--yolo", "-q"])

    def test_uses_configured_bin_dir_before_default_shims(self):
        def exists(path):
            return Path(path).as_posix() == "/opt/hermes/bin/hermes"

        with (
            patch.dict("os.environ", {"HERMES_BIN_DIR": "/opt/hermes/bin"}, clear=True),
            patch("hermes.process.shutil.which", return_value=None),
            patch("hermes.process.Path.home", return_value=Path("/root")),
            patch("hermes.process.Path.is_file", autospec=True, side_effect=exists),
            patch("hermes.process.os.access", return_value=True),
        ):
            arguments = HermesRunner._hermes_arguments()

        self.assertEqual(Path(arguments[0]).as_posix(), "/opt/hermes/bin/hermes")
        self.assertEqual(arguments[1:], ["chat", "-Q", "--yolo", "-q"])

    def test_uses_extra_paths_when_hermes_is_not_on_path(self):
        def exists(path):
            return Path(path).as_posix() == "/srv/tools/hermes"

        with (
            patch.dict("os.environ", {"HERMES_EXTRA_PATHS": "/srv/tools"}, clear=True),
            patch("hermes.process.shutil.which", return_value=None),
            patch("hermes.process.Path.home", return_value=Path("/root")),
            patch("hermes.process.Path.is_file", autospec=True, side_effect=exists),
            patch("hermes.process.os.access", return_value=True),
        ):
            arguments = HermesRunner._hermes_arguments()

        self.assertEqual(Path(arguments[0]).as_posix(), "/srv/tools/hermes")
        self.assertEqual(arguments[1:], ["chat", "-Q", "--yolo", "-q"])

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

    def test_invoke_mounts_workspace_for_docker_backend(self):
        runner = HermesRunner(self.paths)
        log = self.paths.logs / "docker.log"
        active = self.paths.root / "work" / "executions" / "attempt" / "current"
        workspace = type("Workspace", (), {"active": active})()
        captured = {}

        def fake_invoke(_prompt, **kwargs):
            captured.update(kwargs)
            return 0

        with (
            patch.object(runner, "_apply_legacy_custom_provider", return_value=False),
            patch("hermes.process.hermes_arguments", return_value=["hermes", "chat", "-Q", "-q"]),
            patch("hermes.process.effective_terminal_backend", return_value="docker"),
            patch("hermes.process.invoke", side_effect=fake_invoke),
        ):
            returncode = runner._invoke(
                "prompt",
                log,
                dry_run=False,
                timeout=1,
                workspace=workspace,
                profile_name="cf-pwn",
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(captured["cwd"], active)
        self.assertEqual(
            captured["environment"]["TERMINAL_CWD"],
            "/workspace/executions/attempt/current",
        )
        self.assertEqual(
            captured["environment"]["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"],
            "0",
        )
        volume = json.loads(captured["environment"]["TERMINAL_DOCKER_VOLUMES"])[0]
        self.assertTrue(volume.endswith("/workspace/executions"))
        self.assertIn("work", volume)
        self.assertIn("executions", volume)
        self.assertEqual(
            captured["environment"]["TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES"],
            "false",
        )

    def test_invoke_returns_timeout_status(self):
        runner = HermesRunner(self.paths)
        log = self.paths.logs / "timeout.log"
        with (
            patch.dict("os.environ", {"HERMES_CMD": "hermes"}),
            patch.object(runner, "_apply_legacy_custom_provider", return_value=False),
            patch(
                "hermes.process.subprocess.run",
                side_effect=__import__("subprocess").TimeoutExpired("hermes", 1),
            ),
        ):
            returncode = runner._invoke("prompt", log, dry_run=False, timeout=1)

        self.assertEqual(returncode, 124)
        self.assertIn("timed out after 1s", log.read_text(encoding="utf-8"))

    def _write_shard(self, name: str, challenges: list[dict]) -> Path:
        from core.jsonio import write_json

        path = self.paths.shards / "pending" / name
        write_json(path, {"challenges": challenges})
        return path

    def _write_metadata(self, challenge_id: str, category: str, build_status: str) -> Path:
        from core.jsonio import write_json

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

    def test_process_one_fails_when_timeout_without_artifacts(self):
        """Timeout with no challenge directories cannot recover under the new contract."""
        self._write_shard(
            "web-0002-0002.json",
            [{"id": "web-0002", "category": "web"}],
        )

        runner = HermesRunner(
            self.paths,
            image_exists=lambda _: True,
            profile_exists=lambda _: True,
        )
        with (
            patch.object(runner, "_invoke", return_value=124),
            patch.object(runner, "render_prompt", return_value="prompt"),
        ):
            outcome = runner.process_one("worker-1", dry_run=False)

        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["returncode"], 1)

    def test_process_one_fails_when_timeout_with_partial_artifacts(self):
        """Timeout with incomplete per-stage evidence still fails under the new contract."""
        self._write_shard(
            "web-0003-0003.json",
            [
                {"id": "web-0003", "category": "web"},
                {"id": "web-0004", "category": "web"},
            ],
        )
        # Only the metadata file exists — no deploy/, writeup, etc.
        self._write_metadata("web-0003", "web", "passed")
        self._write_metadata("web-0004", "web", "failed")

        runner = HermesRunner(
            self.paths,
            image_exists=lambda _: True,
            profile_exists=lambda _: True,
        )
        with (
            patch.object(runner, "_invoke", return_value=124),
            patch.object(runner, "render_prompt", return_value="prompt"),
        ):
            outcome = runner.process_one("worker-1", dry_run=False)

        self.assertEqual(outcome["status"], "failed")

    def test_process_one_passes_claim_filters(self):
        runner = HermesRunner(self.paths)
        attempt_id = uuid4()
        with patch.object(runner.queue, "claim", return_value=None) as claim:
            outcome = runner.process_one(
                "worker-1",
                dry_run=False,
                category="web",
                build_attempt_id=attempt_id,
                require_build_attempt=True,
            )

        self.assertEqual(outcome, {"status": "empty"})
        claim.assert_called_once_with(
            "worker-1",
            category="web",
            build_attempt_id=attempt_id,
            require_build_attempt=True,
        )
