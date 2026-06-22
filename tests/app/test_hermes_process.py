"""Tests for the reusable Hermes subprocess primitives in `hermes/process.py`.

`invoke_capture` is the new code path introduced by add-research-planning-core
Section 6 — it captures stdout into memory, mirrors it into the log file, and
supports cooperative cancellation via `threading.Event`. These tests exercise
real subprocesses (using the current Python interpreter as a stand-in for
Hermes) so the threading and termination paths are end-to-end covered.
"""

from __future__ import annotations

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
    _wait_after_terminate,
    invoke,
    invoke_capture,
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
