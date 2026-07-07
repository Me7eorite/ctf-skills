"""Tests for the reusable Hermes subprocess primitives in `hermes/process.py`.

`invoke_capture` is the new code path introduced by add-research-planning-core
Section 6 — it captures stdout into memory, mirrors it into the log file, and
supports cooperative cancellation via `threading.Event`. These tests exercise
real subprocesses (using the current Python interpreter as a stand-in for
Hermes) so the threading and termination paths are end-to-end covered.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.jsonio import read_json
from hermes.process import (
    HERMES_TIMEOUT_RETURNCODE,
    NUL_BLOCKED_MESSAGE,
    NUL_SANITIZED_MESSAGE,
    TERMINATION_WAIT_TIMEOUT,
    HermesProcessResult,
    TerminalWorkspaceVisibilityError,
    _wait_after_terminate,
    configure_terminal_workspace,
    effective_terminal_backend,
    hermes_profile_health,
    invoke,
    invoke_capture,
    materialize_isolated_hermes_home,
    project_hermes_home_is_configured,
    resolve_template_hermes_home,
    sanitize_prompt_text,
    verify_terminal_workspace_visibility,
)


def _python(*statements: str) -> list[str]:
    """Build argv that runs the joined statements in the current interpreter."""
    return [sys.executable, "-c", "\n".join(statements)]


class InvokeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workdir = Path(self.temp.name)
        self.log = self.workdir / "research.log"

    def test_captures_stdout_into_memory_and_log(self):
        arguments = _python(
            "import sys",
            "sys.stdout.write('{\"ok\": true}\\n')",
            "sys.stderr.write('debug line\\n')",
            "sys.exit(0)",
        )
        result = invoke_capture(
            "noop-prompt",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=10,
        )

        self.assertIsInstance(result, HermesProcessResult)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.cancelled)
        self.assertIn('{"ok": true}', result.stdout)

        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn("--- stdout ---", log_text)
        self.assertIn('{"ok": true}', log_text)
        self.assertIn("--- end stdout ---", log_text)
        self.assertIn("--- stderr ---", log_text)
        self.assertIn("debug line", log_text)

    def test_invoke_capture_sanitizes_prompt_before_argv(self):
        self.assertEqual(sanitize_prompt_text("prefix\x00suffix"), r"prefix\x00suffix")
        arguments = _python(
            "import sys",
            "sys.stdout.write(sys.argv[-1])",
        )
        result = invoke_capture(
            "prefix\x00suffix",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=10,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, r"prefix\x00suffix")
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn(NUL_SANITIZED_MESSAGE, log_text)
        self.assertNotIn("\x00", log_text)

    def test_invoke_capture_embedded_nul_valueerror_is_normal_failure(self):
        with patch(
            "hermes.process.subprocess.Popen",
            side_effect=ValueError("embedded null byte"),
        ):
            result = invoke_capture(
                "safe",
                arguments=["hermes", "chat", "-q"],
                log_path=self.log,
                cwd=self.workdir,
                environment={},
                timeout=10,
            )

        self.assertEqual(result.returncode, 1)
        self.assertFalse(result.cancelled)
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn(NUL_BLOCKED_MESSAGE, log_text)

    def test_invoke_sanitizes_prompt_before_argv(self):
        arguments = _python(
            "import sys",
            "sys.stdout.write(sys.argv[-1])",
        )
        returncode = invoke(
            "alpha\x00omega",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=10,
        )

        self.assertEqual(returncode, 0)
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn(r"alpha\x00omega", log_text)
        self.assertIn(NUL_SANITIZED_MESSAGE, log_text)
        self.assertNotIn("\x00", log_text)

    def test_cancel_event_terminates_subprocess(self):
        arguments = _python(
            "import sys, time",
            "sys.stdout.write('started\\n')",
            "sys.stdout.flush()",
            "time.sleep(30)",
        )
        cancel_event = threading.Event()

        def trigger_cancel():
            time.sleep(0.5)
            cancel_event.set()

        threading.Thread(target=trigger_cancel, daemon=True).start()

        start = time.monotonic()
        result = invoke_capture(
            "noop",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=10,
            cancel_event=cancel_event,
        )
        elapsed = time.monotonic() - start

        self.assertTrue(result.cancelled)
        self.assertLess(elapsed, 5, "cancel did not terminate subprocess quickly")
        self.assertIn("started", result.stdout)
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn("cancelled at", log_text)

    def test_timeout_returns_124(self):
        arguments = _python(
            "import time",
            "time.sleep(30)",
        )
        result = invoke_capture(
            "noop",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=1,
        )

        self.assertEqual(result.returncode, HERMES_TIMEOUT_RETURNCODE)
        self.assertFalse(result.cancelled)
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn("timed out after 1s", log_text)

    def test_missing_executable_returns_127(self):
        result = invoke_capture(
            "noop",
            arguments=["/nonexistent/binary-that-does-not-exist"],
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=5,
        )

        self.assertEqual(result.returncode, 127)
        self.assertFalse(result.cancelled)
        self.assertEqual(result.stdout, "")
        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn("Hermes command not found", log_text)

    def test_keyboard_interrupt_terminates_subprocess_and_reraises(self):
        # 中文注释：模拟主线程中断，确保 invoke_capture 清理子进程并把异常继续抛给 worker。
        arguments = _python(
            "import time",
            "time.sleep(30)",
        )
        original_sleep = time.sleep
        sleep_calls = 0

        def interrupt_once(seconds):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 1:
                raise KeyboardInterrupt
            original_sleep(seconds)

        with patch("hermes.process.time.sleep", side_effect=interrupt_once):
            with self.assertRaises(KeyboardInterrupt):
                invoke_capture(
                    "noop",
                    arguments=arguments,
                    log_path=self.log,
                    cwd=self.workdir,
                    environment={},
                    timeout=10,
                )

        log_text = self.log.read_text(encoding="utf-8")
        self.assertIn("interrupted before completion", log_text)

    def test_wait_after_terminate_uses_timeout(self):
        class NeverExits:
            timeout = None

            def wait(self, timeout=None):
                self.timeout = timeout
                raise TimeoutError

        process = NeverExits()

        with patch("hermes.process.subprocess.TimeoutExpired", TimeoutError):
            _wait_after_terminate(process)

        self.assertEqual(process.timeout, TERMINATION_WAIT_TIMEOUT)


class ProjectHermesHomeTests(unittest.TestCase):
    def test_logs_only_home_does_not_shadow_global_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            hermes_home = Path(temp) / ".hermes"
            (hermes_home / "logs").mkdir(parents=True)

            self.assertFalse(project_hermes_home_is_configured(hermes_home))

    def test_configured_home_is_used(self):
        with tempfile.TemporaryDirectory() as temp:
            hermes_home = Path(temp) / ".hermes"
            (hermes_home / "profiles" / "cf-re").mkdir(parents=True)

            self.assertTrue(project_hermes_home_is_configured(hermes_home))

    def test_resolve_template_home_prefers_explicit_env(self):
        with tempfile.TemporaryDirectory() as temp:
            project_home = Path(temp) / "project" / ".hermes"
            explicit_home = Path(temp) / "explicit" / ".hermes"

            resolved = resolve_template_hermes_home(
                project_home,
                {"HERMES_HOME": str(explicit_home)},
            )

        self.assertEqual(resolved, explicit_home)

    def test_isolated_home_copies_config_without_profile_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_home = root / "source" / ".hermes"
            source_profile = source_home / "profiles" / "cf-web"
            (source_profile / "skills" / "ctf").mkdir(parents=True)
            (source_profile / "sessions").mkdir()
            (source_profile / "logs").mkdir()
            (source_profile / "memories").mkdir()
            (source_home / "bin").mkdir(parents=True)
            (source_home / "sessions").mkdir(parents=True)
            (source_home / "logs").mkdir()
            (source_home / "config.yaml").write_text("model:\n  default: test\n", encoding="utf-8")
            (source_home / "auth.json").write_text('{"credential_pool":{}}\n', encoding="utf-8")
            (source_home / "state.db").write_text("shared-state", encoding="utf-8")
            (source_home / "bin" / "tirith").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_profile / ".env").write_text("OPENAI_API_KEY=test\n", encoding="utf-8")
            (source_profile / "skills" / "ctf" / "SKILL.md").write_text("# CTF\n", encoding="utf-8")
            (source_profile / "sessions" / "old.json").write_text("old", encoding="utf-8")
            (source_profile / "logs" / "agent.log").write_text("old log", encoding="utf-8")
            (source_profile / "memories" / "MEMORY.md").write_text("old memory", encoding="utf-8")

            isolated = materialize_isolated_hermes_home(
                root / "workspace" / "state" / "hermes-home",
                source_home=source_home,
                profile_name="cf-web",
            )

            self.assertEqual(
                (isolated / "config.yaml").read_text(encoding="utf-8"),
                "model:\n  default: test\n",
            )
            self.assertTrue((isolated / "auth.json").is_file())
            self.assertTrue((isolated / "bin" / "tirith").is_file())
            self.assertTrue((isolated / "profiles" / "cf-web" / ".env").is_file())
            self.assertTrue(
                (isolated / "profiles" / "cf-web" / "skills" / "ctf" / "SKILL.md").is_file()
            )
            self.assertFalse((isolated / "state.db").exists())
            self.assertFalse(
                (isolated / "profiles" / "cf-web" / "sessions" / "old.json").exists()
            )
            self.assertFalse(
                (isolated / "profiles" / "cf-web" / "logs" / "agent.log").exists()
            )
            self.assertFalse(
                (isolated / "profiles" / "cf-web" / "memories" / "MEMORY.md").exists()
            )

    def test_effective_terminal_backend_prefers_terminal_env(self):
        with tempfile.TemporaryDirectory() as temp:
            hermes_home = Path(temp) / ".hermes"
            hermes_home.mkdir()
            (hermes_home / "config.yaml").write_text(
                "terminal:\n  backend: local\n",
                encoding="utf-8",
            )

            backend = effective_terminal_backend(
                hermes_home,
                {"TERMINAL_ENV": "docker"},
            )

        self.assertEqual(backend, "docker")

    def test_effective_terminal_backend_reads_project_dotenv_before_config(self):
        with tempfile.TemporaryDirectory() as temp:
            hermes_home = Path(temp) / ".hermes"
            hermes_home.mkdir()
            (hermes_home / ".env").write_text("TERMINAL_ENV=docker\n", encoding="utf-8")
            (hermes_home / "config.yaml").write_text(
                "terminal:\n  backend: local\n",
                encoding="utf-8",
            )

            backend = effective_terminal_backend(hermes_home, {})

        self.assertEqual(backend, "docker")

    def test_effective_terminal_backend_reads_profile_before_project(self):
        with tempfile.TemporaryDirectory() as temp:
            hermes_home = Path(temp) / ".hermes"
            profile_home = hermes_home / "profiles" / "cf-pwn"
            profile_home.mkdir(parents=True)
            (hermes_home / "config.yaml").write_text(
                "terminal:\n  backend: local\n",
                encoding="utf-8",
            )
            (profile_home / "config.yaml").write_text(
                "terminal:\n  backend: docker\n",
                encoding="utf-8",
            )

            backend = effective_terminal_backend(
                hermes_home,
                {},
                profile_name="cf-pwn",
            )

        self.assertEqual(backend, "docker")

    def test_effective_terminal_backend_uses_explicit_hermes_home_env(self):
        with tempfile.TemporaryDirectory() as temp:
            project_home = Path(temp) / "project" / ".hermes"
            configured_home = Path(temp) / "configured" / ".hermes"
            profile_home = configured_home / "profiles" / "cf-pwn"
            profile_home.mkdir(parents=True)
            (profile_home / "config.yaml").write_text(
                "terminal:\n  backend: docker\n",
                encoding="utf-8",
            )

            backend = effective_terminal_backend(
                project_home,
                {"HERMES_HOME": str(configured_home)},
                profile_name="cf-pwn",
            )

        self.assertEqual(backend, "docker")

    def test_effective_terminal_backend_falls_back_to_default_home_when_project_home_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            project_home = Path(temp) / "project" / ".hermes"
            default_home = Path(temp) / "default" / ".hermes"
            profile_home = default_home / "profiles" / "cf-pwn"
            profile_home.mkdir(parents=True)
            (profile_home / "config.yaml").write_text(
                "terminal:\n  backend: docker\n",
                encoding="utf-8",
            )

            with patch("hermes.process.Path.home", return_value=Path(temp) / "default"):
                backend = effective_terminal_backend(
                    project_home,
                    {},
                    profile_name="cf-pwn",
                    allow_cli_fallback=False,
                )

        self.assertEqual(backend, "docker")

    def test_effective_terminal_backend_reads_config(self):
        with tempfile.TemporaryDirectory() as temp:
            hermes_home = Path(temp) / ".hermes"
            hermes_home.mkdir()
            (hermes_home / "config.yaml").write_text(
                "model:\n  provider: custom\nterminal:\n  cwd: .\n  backend: docker\n",
                encoding="utf-8",
            )

            backend = effective_terminal_backend(hermes_home, {})

        self.assertEqual(backend, "docker")

    def test_effective_terminal_backend_falls_back_to_hermes_cli_profile_config(self):
        captured_command = {}

        def fake_run(arguments, **keyword_args):
            captured_command["arguments"] = arguments
            captured_command["keyword_args"] = keyword_args

            class Result:
                returncode = 0
                stdout = "◆ Terminal\n  Backend:      docker\n  Working dir:  .\n"

            return Result()

        with tempfile.TemporaryDirectory() as temp:
            hermes_home = Path(temp) / ".hermes"
            with patch("hermes.process.hermes_arguments", return_value=["hermes", "chat", "-Q"]):
                with patch("hermes.process.subprocess.run", side_effect=fake_run):
                    backend = effective_terminal_backend(
                        hermes_home,
                        {"HERMES_HOME": str(hermes_home)},
                        profile_name="cf-pwn",
                    )

        self.assertEqual(backend, "docker")
        self.assertEqual(
            captured_command["arguments"],
            ["hermes", "-p", "cf-pwn", "config", "show"],
        )
        self.assertEqual(captured_command["keyword_args"]["timeout"], 10)


class ConfigureTerminalWorkspaceTests(unittest.TestCase):
    def test_docker_backend_mounts_current_execution_workspace_only(self):
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp) / "work" / "executions" / "attempt" / "current"
            cwd.mkdir(parents=True)
            environment = {"TERMINAL_CWD": "/stale"}

            configure_terminal_workspace(
                environment,
                cwd=cwd,
                terminal_backend="docker",
            )

            expected_volume = f"{cwd.resolve()}:/workspace/current"

        self.assertEqual(environment["_HERMES_GATEWAY"], "1")
        self.assertEqual(environment["TERMINAL_CWD"], "/workspace/current")
        self.assertEqual(environment["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"], "1")
        self.assertEqual(json.loads(environment["TERMINAL_DOCKER_VOLUMES"]), [expected_volume])
        self.assertEqual(environment["TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES"], "false")
        self.assertEqual(environment["TERMINAL_CONTAINER_PERSISTENT"], "true")
        self.assertEqual(environment["CTF_SKILLS_EXECUTION_ID"], "attempt")
        self.assertEqual(environment["CTF_SKILLS_HERMES_TASK_ID"], "ctf-build-attempt")
        self.assertEqual(environment["CTF_SKILLS_HOST_WORKSPACE"], str(cwd.resolve()))
        self.assertEqual(environment["CTF_SKILLS_CONTAINER_WORKSPACE"], "/workspace/current")
        self.assertIn("hermes_sitecustomize", environment["PYTHONPATH"])
        docker_env = json.loads(environment["TERMINAL_DOCKER_ENV"])
        self.assertEqual(docker_env["CTF_SKILLS_EXECUTION_ID"], "attempt")
        self.assertEqual(docker_env["CTF_SKILLS_HERMES_TASK_ID"], "ctf-build-attempt")
        self.assertEqual(docker_env["CTF_SKILLS_HOST_WORKSPACE"], str(cwd.resolve()))
        self.assertEqual(docker_env["CTF_SKILLS_CONTAINER_WORKSPACE"], "/workspace/current")
        extra_args = json.loads(environment["TERMINAL_DOCKER_EXTRA_ARGS"])
        self.assertIn("ctf-skills-owner=ctf-skills", extra_args)
        self.assertIn("ctf-skills-execution=attempt", extra_args)

    def test_docker_backend_mounts_design_execution_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp) / "work" / "design" / "executions" / "design-attempt"
            cwd.mkdir(parents=True)
            environment = {}

            configure_terminal_workspace(
                environment,
                cwd=cwd,
                terminal_backend="docker",
            )

            expected_volume = f"{cwd.resolve()}:/workspace"

        self.assertEqual(environment["TERMINAL_CWD"], "/workspace")
        self.assertEqual(json.loads(environment["TERMINAL_DOCKER_VOLUMES"]), [expected_volume])
        self.assertEqual(environment["CTF_SKILLS_EXECUTION_ID"], "design-attempt")
        self.assertEqual(
            environment["CTF_SKILLS_HERMES_TASK_ID"],
            "ctf-build-design-attempt",
        )
        self.assertEqual(environment["CTF_SKILLS_HOST_WORKSPACE"], str(cwd.resolve()))
        self.assertEqual(environment["CTF_SKILLS_CONTAINER_WORKSPACE"], "/workspace")

    def test_docker_backend_keeps_non_execution_cwd_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp) / "workspace"
            cwd.mkdir()
            environment = {}

            configure_terminal_workspace(
                environment,
                cwd=cwd,
                terminal_backend="docker",
            )

            expected_volume = f"{cwd.resolve()}:/workspace"

        self.assertEqual(environment["TERMINAL_CWD"], "/workspace")
        self.assertEqual(json.loads(environment["TERMINAL_DOCKER_VOLUMES"]), [expected_volume])

    def test_local_backend_leaves_environment_untouched(self):
        environment = {"TERMINAL_CWD": "/operator/default"}

        configure_terminal_workspace(
            environment,
            cwd=Path("/tmp/workspace"),
            terminal_backend="local",
        )

        self.assertEqual(environment, {"TERMINAL_CWD": "/operator/default"})

    def test_bootstrap_forces_file_and_terminal_tools_off_default_task(self):
        root = Path(__file__).resolve().parents[2]
        module_path = root / "src" / "hermes_sitecustomize" / "ctf_skills_hermes_bootstrap.py"
        spec = importlib.util.spec_from_file_location("ctf_skills_hermes_bootstrap_test", module_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        tools_module = types.ModuleType("tools")
        terminal_tool_module = types.ModuleType("tools.terminal_tool")
        terminal_tool_module._task_env_overrides = {}
        terminal_tool_module._resolve_container_task_id = lambda _task_id: "default"
        terminal_tool_module._get_env_config = lambda: {
            "cwd": "/workspace/executions/other/current",
            "docker_volumes": ["/root/ctf-skills/work/executions:/workspace/executions"],
            "docker_extra_args": [],
            "docker_env": {},
            "docker_persist_across_processes": True,
        }

        def register_task_env_overrides(task_id, overrides):
            terminal_tool_module._task_env_overrides[task_id] = overrides

        terminal_tool_module.register_task_env_overrides = register_task_env_overrides
        tools_module.terminal_tool = terminal_tool_module

        with (
            patch.dict(
                os.environ,
                {
                    "CTF_SKILLS_HERMES_TASK_ID": "ctf-build-attempt",
                    "CTF_SKILLS_EXECUTION_ID": "attempt",
                    "CTF_SKILLS_HERMES_DOCKER_LABEL": "attempt-label",
                    "CTF_SKILLS_HOST_WORKSPACE": "/host/current",
                    "CTF_SKILLS_CONTAINER_WORKSPACE": "/workspace/current",
                    "TERMINAL_ENV": "docker",
                    "TERMINAL_CWD": "/workspace/current",
                },
                clear=False,
            ),
            patch.dict(
                sys.modules,
                {
                    "tools": tools_module,
                    "tools.terminal_tool": terminal_tool_module,
                },
            ),
        ):
            module.install()

        self.assertEqual(
            terminal_tool_module._task_env_overrides["ctf-build-attempt"]["cwd"],
            "/workspace/current",
        )
        self.assertEqual(terminal_tool_module._resolve_container_task_id(None), "ctf-build-attempt")
        self.assertEqual(
            terminal_tool_module._resolve_container_task_id("hermes-session-id"),
            "ctf-build-attempt",
        )
        config = terminal_tool_module._get_env_config()
        self.assertEqual(config["cwd"], "/workspace/current")
        self.assertEqual(config["docker_volumes"], ["/host/current:/workspace/current"])
        self.assertFalse(config["docker_persist_across_processes"])
        self.assertEqual(config["docker_env"]["CTF_SKILLS_EXECUTION_ID"], "attempt")
        self.assertEqual(config["docker_env"]["CTF_SKILLS_HERMES_TASK_ID"], "ctf-build-attempt")
        self.assertIn("ctf-skills-owner=ctf-skills", config["docker_extra_args"])
        self.assertIn("ctf-skills-execution=attempt", config["docker_extra_args"])
        self.assertIn("ctf-skills-hermes-run=attempt-label", config["docker_extra_args"])


class TerminalWorkspaceVisibilityTests(unittest.TestCase):
    def test_docker_probe_requires_marker_visible_on_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "state").mkdir()
            log = cwd / "logs" / "hermes.log"
            arguments = _python(
                "from pathlib import Path",
                "Path('state/terminal-workspace-probe.json').write_text('{\"ok\": true}', encoding='utf-8')",
            )

            verify_terminal_workspace_visibility(
                arguments=arguments,
                log_path=log,
                cwd=cwd,
                environment={},
                terminal_backend="docker",
                timeout=10,
            )

            self.assertFalse((cwd / "state" / "terminal-workspace-probe.json").exists())

    def test_docker_probe_fails_when_marker_is_not_host_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "state").mkdir()
            log = cwd / "logs" / "hermes.log"
            arguments = _python("print('wrote marker inside private container cwd')")

            environment = {
                "TERMINAL_CWD": str(cwd),
                "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE": "1",
                "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES": "false",
            }
            with self.assertRaisesRegex(
                TerminalWorkspaceVisibilityError,
                "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES='false'",
            ):
                verify_terminal_workspace_visibility(
                    arguments=arguments,
                    log_path=log,
                    cwd=cwd,
                    environment=environment,
                    terminal_backend="docker",
                    timeout=10,
                )

    def test_docker_probe_caps_timeout_to_probe_budget(self):
        captured = {}

        def fake_probe(**kwargs):
            captured["timeout"] = kwargs["timeout"]
            marker = Path(kwargs["cwd"]) / "state" / "terminal-workspace-probe.json"
            marker.write_text('{"ok": true}', encoding="utf-8")
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "state").mkdir()
            log = cwd / "logs" / "hermes.log"

            with patch("hermes.process._invoke_terminal_workspace_probe", side_effect=fake_probe):
                verify_terminal_workspace_visibility(
                    arguments=_python("pass"),
                    log_path=log,
                    cwd=cwd,
                    environment={},
                    terminal_backend="docker",
                    timeout=3600,
                )

        self.assertEqual(captured["timeout"], 240)

    def test_non_docker_backend_still_requires_host_visible_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "state").mkdir()
            arguments = _python(
                "from pathlib import Path",
                "Path('state/terminal-workspace-probe.json').write_text('{\"ok\": true}', encoding='utf-8')",
            )
            verify_terminal_workspace_visibility(
                arguments=arguments,
                log_path=cwd / "logs" / "hermes.log",
                cwd=cwd,
                environment={},
                terminal_backend="local",
                timeout=10,
            )

    def test_docker_probe_timeout_cleans_stale_containers_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "state").mkdir()
            log = cwd / "logs" / "hermes.log"

            def fake_run(command, **_kwargs):
                if command[:3] == ["/usr/bin/docker", "ps", "-aq"]:
                    return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
                if command[:3] == ["/usr/bin/docker", "rm", "-f"]:
                    return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
                raise AssertionError(f"unexpected command: {command!r}")

            with patch("hermes.process.invoke", return_value=HERMES_TIMEOUT_RETURNCODE):
                with patch("hermes.process.shutil.which", return_value="/usr/bin/docker"):
                    with patch("hermes.process.subprocess.run", side_effect=fake_run):
                        with self.assertRaises(TerminalWorkspaceVisibilityError):
                            verify_terminal_workspace_visibility(
                                arguments=["hermes", "-p", "cf-pwn", "chat", "-Q"],
                                log_path=log,
                                cwd=cwd,
                                environment={
                                    "TERMINAL_CWD": "/workspace",
                                    "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES": "false",
                                },
                                terminal_backend="docker",
                                timeout=10,
                            )

            probe_log = log.with_name(log.name + ".terminal_probe.log")
            text = probe_log.read_text(encoding="utf-8")
            self.assertIn("removed 1 stale Hermes Docker terminal container", text)
            self.assertNotIn("recovered", text)


class InvokeLogMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workdir = Path(self.temp.name)
        self.log = self.workdir / "hermes.log"

    def test_invoke_writes_auth_error_marker_without_stdout_capture(self):
        arguments = _python(
            "import sys",
            "sys.stdout.write('{\"type\":\"error\",\"error\":{\"type\":\"authentication_error\",\"status_code\":401}}\\n')",
            "sys.exit(1)",
        )

        returncode = invoke(
            "noop",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=10,
        )

        self.assertEqual(returncode, 1)
        marker = read_json(self.log.with_name("hermes.log.error_marker.json"), {})
        self.assertEqual(marker["error_type"], "authentication_error")
        self.assertEqual(marker["status_code"], 401)

    def test_invoke_writes_rate_limit_marker_from_provider_tail(self):
        arguments = _python(
            "import sys",
            "sys.stdout.write('Anthropic overloaded_error: retry later\\n')",
            "sys.exit(1)",
        )

        returncode = invoke(
            "noop",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=10,
        )

        self.assertEqual(returncode, 1)
        marker = read_json(self.log.with_name("hermes.log.error_marker.json"), {})
        self.assertEqual(marker["error_type"], "rate_limit_error")

    def test_rate_limit_retry_skips_when_attempt_deadline_expired(self):
        arguments = _python(
            "import sys",
            "sys.stdout.write('Anthropic overloaded_error: retry later\\n')",
            "sys.exit(1)",
        )

        returncode = invoke(
            "noop",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={"HERMES_RATE_LIMIT_RETRIES": "1"},
            timeout=10,
            attempt_deadline=time.monotonic() + 0.2,
        )

        self.assertEqual(returncode, HERMES_TIMEOUT_RETURNCODE)
        self.assertIn(
            "global deadline exceeded; retry skipped",
            self.log.read_text(encoding="utf-8"),
        )

    def test_invoke_timeout_logs_process_group_cleanup(self):
        arguments = _python(
            "import time",
            "time.sleep(30)",
        )

        returncode = invoke(
            "noop",
            arguments=arguments,
            log_path=self.log,
            cwd=self.workdir,
            environment={},
            timeout=1,
        )

        self.assertEqual(returncode, HERMES_TIMEOUT_RETURNCODE)
        self.assertIn(
            "killed_process_group:",
            self.log.read_text(encoding="utf-8"),
        )


class HermesProfileHealthTests(unittest.TestCase):
    def test_missing_profile_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"USERPROFILE": tmp, "HOME": tmp}):
                ok, code, _message = hermes_profile_health("cf-web")

        self.assertFalse(ok)
        self.assertEqual(code, "hermes_profile_missing")

    def test_ok_without_env_file_when_cli_can_show_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / ".hermes" / "profiles" / "cf-web"
            profile.mkdir(parents=True)
            with patch.dict(os.environ, {"USERPROFILE": tmp, "HOME": tmp}), patch(
                "hermes.process.profile_exists",
                return_value=True,
            ):
                ok, code, _message = hermes_profile_health("cf-web")

        self.assertTrue(ok)
        self.assertEqual(code, "")

    def test_empty_env_file_does_not_override_cli_profile_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / ".hermes" / "profiles" / "cf-web"
            profile.mkdir(parents=True)
            (profile / ".env").write_text("ANTHROPIC_API_KEY=''\nANTHROPIC_TOKEN=   \n", encoding="utf-8")
            with patch.dict(os.environ, {"USERPROFILE": tmp, "HOME": tmp}), patch(
                "hermes.process.profile_exists",
                return_value=True,
            ):
                ok, code, _message = hermes_profile_health("cf-web")

        self.assertTrue(ok)
        self.assertEqual(code, "")

    def test_cli_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / ".hermes" / "profiles" / "cf-web"
            profile.mkdir(parents=True)
            with patch.dict(os.environ, {"USERPROFILE": tmp, "HOME": tmp}), patch(
                "hermes.process.profile_exists",
                return_value=False,
            ):
                ok, code, _message = hermes_profile_health("cf-web")

        self.assertFalse(ok)
        self.assertEqual(code, "hermes_profile_cli_unavailable")

    def test_ok_with_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / ".hermes" / "profiles" / "cf-web"
            profile.mkdir(parents=True)
            (profile / ".env").write_text("ANTHROPIC_TOKEN=\"secret\"\n", encoding="utf-8")
            with patch.dict(os.environ, {"USERPROFILE": tmp, "HOME": tmp}), patch(
                "hermes.process.profile_exists",
                return_value=True,
            ):
                ok, code, _message = hermes_profile_health("cf-web")

        self.assertTrue(ok)
        self.assertEqual(code, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
