"""Tests for ``core.docker.image_exists``.

The helper MUST return False (never raise) for empty input, missing Docker,
command timeouts, and non-zero ``docker image inspect`` results. It MUST use
an argv list with ``shell=False``.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from core import docker as core_docker


class DockerImageExistsTests(unittest.TestCase):
    def test_empty_image_returns_false(self):
        self.assertFalse(core_docker.image_exists(""))

    def test_image_present_returns_true(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch("core.docker.subprocess.run", return_value=completed) as runner:
            self.assertTrue(core_docker.image_exists("node:20-alpine"))
            self.assertEqual(runner.call_count, 1)
            args, kwargs = runner.call_args
            self.assertEqual(
                args[0], ["docker", "image", "inspect", "node:20-alpine"]
            )
            self.assertEqual(kwargs.get("shell", False), False)
            self.assertIsNotNone(kwargs.get("timeout"))

    def test_inspect_nonzero_returns_false(self):
        completed = subprocess.CompletedProcess(args=[], returncode=1)
        with patch("core.docker.subprocess.run", return_value=completed):
            self.assertFalse(core_docker.image_exists("missing:tag"))

    def test_docker_missing_returns_false(self):
        with patch("core.docker.subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(core_docker.image_exists("anything:latest"))

    def test_command_timeout_returns_false(self):
        with patch(
            "core.docker.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=1),
        ):
            self.assertFalse(core_docker.image_exists("slow:image"))


if __name__ == "__main__":
    unittest.main()
