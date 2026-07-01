"""Tests for the reusable Hermes subprocess primitives in `hermes/process.py`.

`invoke_capture` is the new code path introduced by add-research-planning-core
Section 6 — it captures stdout into memory, mirrors it into the log file, and
supports cooperative cancellation via `threading.Event`. These tests exercise
real subprocesses (using the current Python interpreter as a stand-in for
Hermes) so the threading and termination paths are end-to-end covered.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.jsonio import read_json
from hermes.process import (
    HERMES_TIMEOUT_RETURNCODE,
    TERMINATION_WAIT_TIMEOUT,
    HermesProcessResult,
    TerminalWorkspaceVisibilityError,
    _wait_after_terminate,
    configure_terminal_workspace,
    effective_terminal_backend,
    hermes_profile_health,
    invoke,
    invoke_capture,
    project_hermes_home_is_configured,
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
    def test_docker_backend_mounts_executions_root_and_uses_container_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp) / "work" / "executions" / "attempt" / "current"
            cwd.mkdir(parents=True)
            environment = {"TERMINAL_CWD": "/stale"}

            configure_terminal_workspace(
                environment,
                cwd=cwd,
                terminal_backend="docker",
            )

            executions = Path(temp) / "work" / "executions"
            expected_volume = f"{executions.resolve()}:/workspace/executions"

        self.assertEqual(environment["_HERMES_GATEWAY"], "1")
        self.assertEqual(environment["TERMINAL_CWD"], "/workspace/executions/attempt/current")
        self.assertEqual(environment["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"], "0")
        self.assertEqual(json.loads(environment["TERMINAL_DOCKER_VOLUMES"]), [expected_volume])
        self.assertEqual(environment["TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES"], "false")

    def test_local_backend_leaves_environment_untouched(self):
        environment = {"TERMINAL_CWD": "/operator/default"}

        configure_terminal_workspace(
            environment,
            cwd=Path("/tmp/workspace"),
            terminal_backend="local",
        )

        self.assertEqual(environment, {"TERMINAL_CWD": "/operator/default"})


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

            self.assertTrue((cwd / "state" / "terminal-workspace-probe.json").is_file())

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
