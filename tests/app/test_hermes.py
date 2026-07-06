import inspect
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from core.paths import ProjectPaths
from hermes import HermesRunner
from hermes import process as hermes_process
from hermes.prompt import render_validation_repair_prompt

ROOT = Path(__file__).resolve().parents[2]


class HermesRunnerTests(unittest.TestCase):
    def test_run_accepts_attempt_deadline_parameters(self):
        signature = inspect.signature(HermesRunner.run)

        self.assertIn("attempt_timeout_seconds", signature.parameters)
        self.assertIn("attempt_deadline", signature.parameters)

    def test_validation_prompt_requires_clean_stdout_and_stale_cleanup(self):
        prompt = (ROOT / "prompts" / "shard_prompt.md").read_text(encoding="utf-8")
        self.assertIn("redirect its output to stderr (`>&2`)", prompt)
        self.assertIn(
            'docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true',
            prompt,
        )

    def test_invoke_clamps_timeout_to_remaining_attempt_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = ProjectPaths(root=Path(tmp), repository=Path(tmp))
            runner = HermesRunner(paths)
            log = Path(tmp) / "hermes.log"
            deadline = time.monotonic() + 5

            with (
                patch.object(
                    runner,
                    "_invoke_context",
                    return_value=(["hermes"], {}, Path(tmp), None),
                ),
                patch.object(runner, "_profile_agent_log_path", return_value=None),
                patch("hermes.runner.hermes_process.invoke", return_value=0) as invoke,
            ):
                returncode = runner._invoke(
                    "prompt",
                    log,
                    dry_run=False,
                    timeout=60,
                    attempt_deadline=deadline,
                )

        self.assertEqual(returncode, 0)
        captured_timeout = invoke.call_args.kwargs["timeout"]
        self.assertLessEqual(captured_timeout, 5)
        self.assertGreater(captured_timeout, 0)

    def test_shard_prompt_uses_materialized_design_references(self):
        prompt = (ROOT / "prompts" / "shard_prompt.md").read_text(encoding="utf-8")

        for current in (
            "design-core.md",
            "category-tactics.md",
            "difficulty-rubric.md",
            "shared_generation_strategy.md",
        ):
            self.assertIn(current, prompt)
        for legacy in (
            "web-design.md",
            "pwn-design.md",
            "reverse-design.md",
            "quality-gate.md",
            "spec-template.md",
            "delivery-format.md",
        ):
            self.assertNotIn(f"- `{legacy}`", prompt)

    def test_shard_prompt_requires_pwn_xinetd_chroot_launcher(self):
        prompt = (ROOT / "prompts" / "shard_prompt.md").read_text(encoding="utf-8")

        self.assertIn("{pwn_scaffold_reference}", prompt)
        self.assertIn("xinetd + chroot + TCP socket", prompt)
        self.assertIn("server = /usr/sbin/chroot", prompt)
        self.assertIn("server_args = --userspec=1000:1000", prompt)
        self.assertIn("/etc/xinetd.d/ctf", prompt)
        self.assertIn("pwn-{workspace_id[:6]}-{challenge_slug}:latest", prompt)
        self.assertIn("Read `workspace_id` from `./input/manifest.json`", prompt)
        self.assertIn("derive `challenge_slug` from the\n  canonical challenge directory basename", prompt)
        self.assertIn("Do not use short/generic names such as\n  `pwn-canary:latest`", prompt)
        self.assertIn("The slug alone is NOT the image name", prompt)
        self.assertIn("pwn-09c554-canary:latest", prompt)
        self.assertIn("ctf-factory.*", prompt)
        self.assertIn("workspace-scoped dangling managed images", prompt)
        self.assertIn("apt mirror fallback loop and mirror\n  order", prompt)
        self.assertIn("Do not replace it with one hardcoded mirror", prompt)
        self.assertIn("Do not run any terminal command that contains `cd ./output/challenges/...`", prompt)
        self.assertIn("deploy/src` and then running", prompt)

    def test_shard_prompt_guides_pwntools_exp_debugging(self):
        prompt = (ROOT / "prompts" / "shard_prompt.md").read_text(encoding="utf-8")

        self.assertIn("context(os='linux', arch='amd64'", prompt)
        self.assertIn("ELF('./attachments/<binary>', checksec=False)", prompt)
        self.assertIn("BINARY_SHA256", prompt)
        self.assertIn("socket.create_connection", prompt)
        self.assertIn("never waits for\n  the same prompt twice", prompt)
        self.assertIn("LOCAL=1 python3 writenup/exp.py", prompt)
        self.assertIn("process([binary_path])", prompt)
        self.assertIn("PWNLIB_LOG_LEVEL=debug", prompt)
        self.assertIn("remote(os.environ['CHAL_HOST']", prompt)
        self.assertIn("command -v gdb checksec readelf objdump", prompt)
        self.assertIn("gdb -q <binary>", prompt)
        self.assertIn("pwndbg/gef", prompt)
        self.assertIn("Never run bare `./<binary>`", prompt)
        self.assertIn("subprocess.run([...], input=..., timeout=5)", prompt)
        self.assertIn("Do not write absolute", prompt)
        self.assertIn("/output/...", prompt)
        self.assertIn("/attachments/...", prompt)
        self.assertIn("/writenup/exp.py", prompt)
        self.assertIn("Do not `chmod` files under `attachments/`", prompt)
        self.assertIn("application-level probe", prompt)
        self.assertIn("Choice:", prompt)
        self.assertIn("open the flag by its chroot-internal path such as `/flag`", prompt)
        self.assertIn("xinetd/chroot scaffold is a deployment contract only", prompt)
        self.assertIn("does not\n  imply ret2libc", prompt)
        self.assertIn("writenup/pwn_debug_report.json", prompt)
        self.assertIn("remote_result", prompt)

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
        self.assertIn("Pwn scaffold reference:", prompt)
        self.assertIn("Check `pwd` first", prompt)
        self.assertNotIn("../references/scaffolds/pwn/xinetd-chroot/", prompt)
        self.assertNotIn("./references/scaffolds/pwn/xinetd-chroot/", prompt)
        self.assertIn("xinetd + chroot + TCP socket", prompt)
        self.assertIn("/usr/sbin/chroot", prompt)
        self.assertIn("--userspec=1000:1000", prompt)
        self.assertIn("MUST appear only as `RUN` steps in", prompt)
        self.assertIn("Never run bare `./<binary>`", prompt)
        self.assertIn("subprocess.run([...], input=..., timeout=5)", prompt)
        self.assertIn("/writenup/exp.py", prompt)
        self.assertIn("bare `nc -z` port check is too", prompt)
        self.assertIn("program must open `/flag`", prompt)
        self.assertIn("fixed exploit family", prompt)
        self.assertIn("pwn_debug_report.json", prompt)
        self.assertIn("helper function name", prompt)
        self.assertIn("Directory discipline", prompt)
        self.assertIn("Terminal tool usage", prompt)
        self.assertIn("Do not use `eval`", prompt)
        self.assertIn("CHAL_ROOT=\"$(find \"$WORKSPACE_ROOT/output/challenges/<category>\"", prompt)
        self.assertIn("must not call `./bin/progress` at all", prompt)
        self.assertIn("A bare\n  `./bin/progress` call is always wrong", prompt)
        self.assertIn("already contains `metadata.json`, `validate.sh`", prompt)
        self.assertIn("never concatenate `./output/challenges/...`", prompt)
        self.assertIn("The same path rule applies to file tools", prompt)
        self.assertIn("read `deploy/Dockerfile`, not", prompt)
        self.assertIn("A real absolute path printed by `pwd -P`", prompt)
        self.assertIn("Before every `read_file`, `write_file`, or patch", prompt)
        self.assertIn("/output/...", prompt)
        self.assertIn("/attachments/...", prompt)
        self.assertIn("pwn-{workspace_id[:6]}-{challenge_slug}:latest", prompt)
        self.assertIn("pwn-canary:latest", prompt)
        self.assertIn("workspace-scoped pattern", prompt)
        self.assertIn("ctf-factory.*", prompt)
        self.assertIn("apt mirror fallback loop and mirror\n  order", prompt)
        self.assertIn("Do not run any terminal command that contains `cd ./output/challenges/...`", prompt)

    def test_pwn_scaffold_prefers_stable_apt_fallback_order(self):
        dockerfile = ROOT / "scaffolds" / "pwn" / "xinetd-chroot" / "deploy" / "Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")

        aliyun = text.index("http://mirrors.aliyun.com/ubuntu/")
        ustc = text.index("http://mirrors.ustc.edu.cn/ubuntu/")
        zju = text.index("http://mirrors.zju.edu.cn/ubuntu/")
        official = text.index("http://archive.ubuntu.com/ubuntu/")
        self.assertLess(aliyun, ustc)
        self.assertLess(ustc, zju)
        self.assertLess(zju, official)
        self.assertNotIn("http://mirror.tuna.tsinghua.edu.cn/ \\", text)
        self.assertIn("http://mirror.tuna.tsinghua.edu.cn/ubuntu/?", text)

    def test_shard_prompt_enforces_workspace_path_discipline(self):
        prompt = (ROOT / "prompts" / "shard_prompt.md").read_text(encoding="utf-8")

        self.assertIn("Workspace Path Discipline", prompt)
        self.assertIn("Terminal Tool Usage", prompt)
        self.assertIn("Do not\nuse `eval`", prompt)
        self.assertIn("CHAL_ROOT=\"$(find \"$WORKSPACE_ROOT/output/challenges/<category>\"", prompt)
        self.assertIn("\"$WORKSPACE_ROOT/bin/progress\" --challenge <challenge-id>", prompt)
        self.assertIn("Never call only `{progress_command}` or `./bin/progress` by itself", prompt)
        self.assertIn("The same path rule applies to file tools", prompt)
        self.assertIn("read `deploy/Dockerfile`, not", prompt)
        self.assertIn("Do not use absolute synthetic paths", prompt)
        self.assertIn("/output/...", prompt)
        self.assertIn("/attachments/...", prompt)
        self.assertIn("A real absolute path printed by `pwd -P`", prompt)
        self.assertIn("Before every `read_file`, `write_file`, or patch", prompt)
        self.assertIn("If the path starts with\n  `./output/challenges/`", prompt)
        self.assertIn("Before reading optional files such as `deploy/src/Makefile`", prompt)
        self.assertIn("Do not `chmod` files under `attachments/`", prompt)
        self.assertIn("Do not run any terminal command that contains `cd ./output/challenges/...`", prompt)

    def test_repair_prompt_uses_pwn_failure_details(self):
        prompt = render_validation_repair_prompt(
            attempt=1,
            max_attempts=2,
            validation_results=[
                {
                    "challenge_id": "pwn-0001",
                    "solve_status": "failed",
                    "validation_status": "nonzero_exit",
                    "validation_failure_details": [
                        {
                            "code": "pwn_bad_libc_base",
                            "hint": "libc base is not page aligned",
                        }
                    ],
                }
            ],
        )

        self.assertIn("Pwn validation failed", prompt)
        self.assertIn("pwn_debug_report.json", prompt)
        self.assertIn("matching libc/ld", prompt)
        self.assertIn("libc base is not page aligned", prompt)

    def test_pwn_xinetd_chroot_scaffold_has_container_only_setup(self):
        scaffold = ROOT / "scaffolds" / "pwn" / "xinetd-chroot"
        dockerfile = (scaffold / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        compose = (scaffold / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        start_sh = (scaffold / "deploy" / "_files" / "start.sh").read_text(encoding="utf-8")
        xinetd = (scaffold / "deploy" / "_files" / "ctf.xinetd").read_text(encoding="utf-8")

        self.assertNotIn("RUN cp -R /lib* /home/ctf", dockerfile)
        self.assertIn("cp -a /lib/x86_64-linux-gnu/*.so*", dockerfile)
        self.assertIn("lib32z1", dockerfile)
        self.assertIn("cp /bin/ls /home/ctf/bin", dockerfile)
        self.assertIn("cp /usr/bin/timeout /home/ctf/bin", dockerfile)
        self.assertIn("Every absolute path below", dockerfile)
        self.assertIn("ctf-docker-template pwn Ubuntu layout", dockerfile)
        self.assertIn("- FLAG={{FLAG}}", compose)
        self.assertNotIn("volumes:", compose)
        self.assertNotIn("cp -R /lib* /home/ctf", start_sh)
        self.assertNotIn("mknod /home/ctf", start_sh)
        self.assertIn("DASFLAG", start_sh)
        self.assertIn("GZCTF_FLAG", start_sh)
        self.assertIn("printf '%s\\n' \"$INSERT_FLAG\" > /home/ctf/flag", start_sh)
        self.assertIn("chmod 711 /home/ctf/{{BINARY_NAME}}", start_sh)
        self.assertIn("server      = /usr/sbin/chroot", xinetd)
        self.assertIn("server_args = --userspec=1000:1000", xinetd)
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
        for profile_name in ("cf-web", "cf-pwn"):
            profile_dir = self.paths.hermes_home / "profiles" / profile_name
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / ".env").write_text("", encoding="utf-8")
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

    def test_bypasses_hermes_wrapper_that_unsets_pythonpath(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "venv" / "bin" / "hermes"
            target.parent.mkdir(parents=True)
            target.write_text("#!/usr/bin/env python\n", encoding="utf-8")
            target.chmod(0o755)
            wrapper = root / "hermes"
            wrapper.write_text(
                "#!/usr/bin/env bash\n"
                "unset PYTHONPATH\n"
                "unset PYTHONHOME\n"
                f"exec {str(target)!r} \"$@\"\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)

            with (
                patch.dict("os.environ", {}, clear=True),
                patch("hermes.process.shutil.which", return_value=str(wrapper)),
            ):
                arguments = HermesRunner._hermes_arguments()

        self.assertEqual(arguments[0], str(target))
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
            "/workspace/current",
        )
        self.assertEqual(
            captured["environment"]["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"],
            "1",
        )
        volume = json.loads(captured["environment"]["TERMINAL_DOCKER_VOLUMES"])[0]
        self.assertTrue(volume.endswith("/workspace/current"))
        self.assertIn("work", volume)
        self.assertIn("executions", volume)
        self.assertIn("/attempt/current:", volume)
        self.assertNotIn("/workspace/executions", volume)
        self.assertEqual(
            captured["environment"]["TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES"],
            "false",
        )
        self.assertEqual(
            captured["environment"]["CTF_SKILLS_EXECUTION_ID"],
            "attempt",
        )
        self.assertEqual(
            captured["environment"]["CTF_SKILLS_HERMES_TASK_ID"],
            "ctf-build-attempt",
        )
        self.assertEqual(
            captured["environment"]["HERMES_HOME"],
            str(active / ".hermes-session" / "hermes-home"),
        )
        self.assertEqual(
            captured["environment"]["CTF_SKILLS_HERMES_SESSION_HOME"],
            str(active / ".hermes-session" / "hermes-home"),
        )
        docker_env = json.loads(captured["environment"]["TERMINAL_DOCKER_ENV"])
        self.assertEqual(docker_env["CTF_SKILLS_EXECUTION_ID"], "attempt")
        self.assertEqual(docker_env["CTF_SKILLS_HERMES_TASK_ID"], "ctf-build-attempt")
        extra_args = json.loads(captured["environment"]["TERMINAL_DOCKER_EXTRA_ARGS"])
        self.assertIn("--label", extra_args)
        self.assertIn("ctf-skills-owner=ctf-skills", extra_args)
        self.assertIn("ctf-skills-execution=attempt", extra_args)
        self.assertTrue(
            any(arg.startswith("ctf-skills-hermes-run=attempt-") for arg in extra_args)
        )
        self.assertIn("CTF_SKILLS_HERMES_DOCKER_LABEL", captured["environment"])
        self.assertIn("profile_log_path", captured)

    def test_invoke_rejects_other_attempt_execution_path_in_prompt(self):
        runner = HermesRunner(self.paths)
        current = "11111111-1111-1111-1111-111111111111"
        other = "22222222-2222-2222-2222-222222222222"
        active = self.paths.root / "work" / "executions" / current / "current"
        active.mkdir(parents=True)
        log = self.paths.logs / "leak.log"
        workspace = type("Workspace", (), {"active": active})()

        with patch("hermes.process.invoke", side_effect=AssertionError("Hermes must not run")):
            returncode = runner._invoke(
                f"debug path /workspace/executions/{other}/current/output",
                log,
                dry_run=False,
                timeout=1,
                workspace=workspace,
                profile_name="cf-pwn",
            )

        self.assertEqual(returncode, 1)
        self.assertIn("orchestration-context-leak", log.read_text(encoding="utf-8"))
        self.assertIn(other, log.read_text(encoding="utf-8"))

    def test_three_attempt_invocations_have_isolated_workspace_context(self):
        runner = HermesRunner(self.paths)
        attempt_ids = [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
        ]
        captures = []

        def fake_invoke(prompt, **kwargs):
            captures.append((prompt, kwargs))
            return 0

        with (
            patch.object(runner, "_apply_legacy_custom_provider", return_value=False),
            patch("hermes.process.hermes_arguments", return_value=["hermes", "chat", "-Q", "-q"]),
            patch("hermes.process.effective_terminal_backend", return_value="docker"),
            patch("hermes.process.invoke", side_effect=fake_invoke),
        ):
            for attempt_id in attempt_ids:
                active = self.paths.root / "work" / "executions" / attempt_id / "current"
                active.mkdir(parents=True)
                workspace = type("Workspace", (), {"active": active})()
                runner._invoke(
                    f"current path /workspace/executions/{attempt_id}/current/output",
                    self.paths.logs / f"{attempt_id}.log",
                    dry_run=False,
                    timeout=1,
                    workspace=workspace,
                    profile_name="cf-pwn",
                )

        labels = set()
        homes = set()
        for index, (prompt, kwargs) in enumerate(captures):
            current = attempt_ids[index]
            others = set(attempt_ids) - {current}
            self.assertIn(current, prompt)
            self.assertFalse(any(other in prompt for other in others))
            self.assertEqual(
                kwargs["environment"]["TERMINAL_CWD"],
                "/workspace/current",
            )
            self.assertNotIn("/workspace/executions", kwargs["environment"]["TERMINAL_CWD"])
            volume = json.loads(kwargs["environment"]["TERMINAL_DOCKER_VOLUMES"])[0]
            self.assertIn(f"/work/executions/{current}/current:", volume)
            self.assertNotIn("/workspace/executions", volume)
            self.assertEqual(kwargs["environment"]["CTF_SKILLS_EXECUTION_ID"], current)
            self.assertEqual(
                kwargs["environment"]["CTF_SKILLS_HERMES_TASK_ID"],
                f"ctf-build-{current}",
            )
            home = kwargs["environment"]["HERMES_HOME"]
            self.assertIn(f"/work/executions/{current}/current/.hermes-session/hermes-home", home)
            self.assertEqual(
                kwargs["environment"]["CTF_SKILLS_HERMES_SESSION_HOME"],
                home,
            )
            homes.add(home)
            label = kwargs["environment"]["CTF_SKILLS_HERMES_DOCKER_LABEL"]
            self.assertIn(current, label)
            labels.add(label)
        self.assertEqual(len(labels), 3)
        self.assertEqual(len(homes), 3)

    def test_invoke_flushes_header_before_blocking_run(self):
        log = self.paths.logs / "live.log"
        profile_log = self.paths.root / ".hermes" / "profiles" / "cf-pwn" / "logs" / "agent.log"

        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.returncode = 0
                self.pid = 12345
                self.args = args
                self.kwargs = kwargs

            def wait(self, timeout=None):
                text = log.read_text(encoding="utf-8")
                assert "timeout: 9s" in text
                assert f"profile_log: {profile_log}" in text
                assert self.kwargs["stdin"] is subprocess.DEVNULL
                return 0

        with patch("hermes.process.subprocess.Popen", FakeProcess):
            returncode = hermes_process.invoke(
                "prompt",
                arguments=["hermes", "chat", "-Q", "-q"],
                log_path=log,
                cwd=self.paths.root,
                environment={},
                timeout=9,
                profile_log_path=profile_log,
            )

        self.assertEqual(returncode, 0)

    def test_invoke_does_not_mirror_unlabeled_profile_log_by_default(self):
        log = self.paths.logs / "profile-filter.log"
        profile_log = self.paths.root / ".hermes" / "profiles" / "cf-pwn" / "logs" / "agent.log"
        profile_log.parent.mkdir(parents=True, exist_ok=True)
        profile_log.write_text("existing sibling log\n", encoding="utf-8")

        class FakeProcess:
            pid = 12345

            def __init__(self, *_args, **_kwargs):
                pass

            def wait(self, timeout=None):
                profile_log.write_text(
                    "existing sibling log\nsibling execution File not found\n",
                    encoding="utf-8",
                )
                return 0

        with patch("hermes.process.subprocess.Popen", FakeProcess):
            returncode = hermes_process.invoke(
                "prompt",
                arguments=["hermes", "chat", "-Q", "-q"],
                log_path=log,
                cwd=self.paths.root,
                environment={},
                timeout=9,
                profile_log_path=profile_log,
            )

        self.assertEqual(returncode, 0)
        text = log.read_text(encoding="utf-8")
        self.assertIn(f"profile_log: {profile_log}", text)
        self.assertNotIn("sibling execution File not found", text)
        self.assertNotIn("[profile]", text)

    def test_invoke_cleans_labeled_hermes_container(self):
        log = self.paths.logs / "cleanup.log"
        environment = {"CTF_SKILLS_HERMES_DOCKER_LABEL": "attempt-123"}

        class FakeProcess:
            pid = 12345

            def __init__(self, *_args, **_kwargs):
                pass

            def wait(self, timeout=None):
                return 0

        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[:3] == ["/usr/bin/docker", "ps", "-aq"]:
                return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
            if command[:3] == ["/usr/bin/docker", "rm", "-f"]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {command!r}")

        with (
            patch("hermes.process.subprocess.Popen", FakeProcess),
            patch("hermes.process.shutil.which", return_value="/usr/bin/docker"),
            patch("hermes.process.subprocess.run", side_effect=fake_run),
        ):
            returncode = hermes_process.invoke(
                "prompt",
                arguments=["hermes", "chat", "-Q", "-q"],
                log_path=log,
                cwd=self.paths.root,
                environment=environment,
                timeout=9,
            )

        self.assertEqual(returncode, 0)
        self.assertIn(
            ["--filter", "label=ctf-skills-hermes-run=attempt-123"],
            [commands[0][index:index + 2] for index in range(len(commands[0]) - 1)],
        )
        self.assertEqual(commands[1][:4], ["/usr/bin/docker", "rm", "-f", "abc123"])
        self.assertIn("[hermes-docker-cleanup] removed 1", log.read_text(encoding="utf-8"))

    def test_invoke_returns_timeout_status(self):
        runner = HermesRunner(self.paths)
        log = self.paths.logs / "timeout.log"

        class SlowProcess:
            pid = 12345

            def __init__(self, *_args, **_kwargs):
                self.returncode = None

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("hermes", timeout)

        with (
            patch.dict("os.environ", {"HERMES_CMD": "hermes"}),
            patch.object(runner, "_apply_legacy_custom_provider", return_value=False),
            patch("hermes.process.subprocess.Popen", SlowProcess),
            patch("hermes.process._terminate"),
            patch("hermes.process._wait_after_terminate"),
            patch("hermes.process.shutil.which", return_value=None),
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
