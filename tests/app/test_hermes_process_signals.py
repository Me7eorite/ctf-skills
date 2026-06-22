from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from domain.build_failure_taxonomy import classify_hermes_exit
from hermes.process import HERMES_TIMEOUT_RETURNCODE, invoke


@pytest.mark.skipif(os.name == "nt", reason="Windows does not report POSIX negative signal return codes")
@pytest.mark.parametrize(
    "sig, expected",
    [
        (signal.SIGINT, -2),
        (signal.SIGTERM, -15),
    ],
)
def test_invoke_preserves_negative_signal_returncode(tmp_path: Path, sig: signal.Signals, expected: int):
    returncode = invoke(
        "noop",
        arguments=[
            sys.executable,
            "-c",
            f"import os, signal; os.kill(os.getpid(), {int(sig)})",
        ],
        log_path=tmp_path / "hermes.log",
        cwd=tmp_path,
        environment=os.environ.copy(),
        timeout=10,
    )

    assert returncode == expected
    assert classify_hermes_exit(returncode, "", 1.0) == "hermes_cancelled"


@pytest.mark.skipif(
    os.name != "nt",
    reason="portable fallback is only needed where signal returncodes are not negative",
)
def test_windows_portable_cancel_classification():
    assert classify_hermes_exit(-2, "", 1.0) == "hermes_cancelled"


def test_timeout_returncode_is_124_and_independent_of_signals(tmp_path: Path):
    returncode = invoke(
        "noop",
        arguments=[sys.executable, "-c", "import time; time.sleep(5)"],
        log_path=tmp_path / "hermes.log",
        cwd=tmp_path,
        environment=os.environ.copy(),
        timeout=1,
    )

    assert HERMES_TIMEOUT_RETURNCODE == 124
    assert returncode == HERMES_TIMEOUT_RETURNCODE
    assert classify_hermes_exit(returncode, "", 1.0) == "hermes_timeout"
