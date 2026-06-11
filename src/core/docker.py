"""Isolated Docker CLI helpers.

Only this module is permitted to spawn ``docker`` subprocesses for image
inspection. Domain code MUST call ``image_exists`` rather than importing
``subprocess`` directly.
"""

from __future__ import annotations

import subprocess

_DEFAULT_TIMEOUT_SECONDS = 5.0


def image_exists(image: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> bool:
    """Return True iff a local Docker image with the given tag exists.

    Returns False (does not raise) when:
    - ``image`` is empty
    - the ``docker`` CLI is not on PATH
    - the inspect command times out
    - ``docker image inspect`` returns non-zero
    """
    if not image:
        return False
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0
