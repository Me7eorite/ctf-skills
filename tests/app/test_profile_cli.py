"""CLI 测试：`challenge-factory profile <subcommand>`。

与 test_research_cli.py 一致：patch persistence + services，断言 argparse 和
dispatch 行为；不依赖真实数据库或真实 Hermes 二进制。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_run(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    exit_code = 0
    with patch.object(sys, "argv", ["challenge-factory", *argv]):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                cli.main()
            except SystemExit as exc:
                exit_code = int(exc.code or 0)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _make_binding(role="research", profile_name="default", status="enabled"):
    from domain.research import HermesProfileBinding

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return HermesProfileBinding(
        role=role,
        profile_name=profile_name,
        description="默认绑定",
        status=status,
        last_used_at=None,
        last_used_run_id=None,
        created_at=now,
        updated_at=now,
    )


def _silence_argparse_db():
    """Patch persistence + sqlalchemy paths used during parser build."""
    return (
        patch("persistence.session.transaction", side_effect=RuntimeError("no DB")),
    )


# ---------------------------------------------------------------------------
# `profile list`
# ---------------------------------------------------------------------------


class ProfileListTests(unittest.TestCase):
    def test_empty_bindings(self):
        repo = SimpleNamespace(list_bindings=lambda: [])

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["profile", "list"])
        self.assertEqual(code, 0)
        self.assertIn("(no bindings)", stdout)

    def test_populated_bindings(self):
        bindings = [_make_binding("research", "default", "enabled")]
        repo = SimpleNamespace(list_bindings=lambda: bindings)

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["profile", "list"])
        self.assertEqual(code, 0)
        self.assertIn("research", stdout)
        self.assertIn("default", stdout)
        self.assertIn("enabled", stdout)


# ---------------------------------------------------------------------------
# `profile show`
# ---------------------------------------------------------------------------


class ProfileShowTests(unittest.TestCase):
    def test_known_role(self):
        binding = _make_binding("research", "default", "enabled")
        repo = SimpleNamespace(get_binding=lambda _r: binding)

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["profile", "show", "research"])
        self.assertEqual(code, 0)
        self.assertIn("profile_name    : default", stdout)
        self.assertIn("status          : enabled", stdout)

    def test_unknown_role_exits_2(self):
        # 中文注释：DB 里没有该 role 的绑定行（不同于 argparse choices 检查）。
        repo = SimpleNamespace(get_binding=lambda _r: None)

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, _stdout, stderr = _capture_run(["profile", "show", "research"])
        self.assertEqual(code, 2)
        self.assertIn("no binding for role 'research'", stderr)


# ---------------------------------------------------------------------------
# `profile bind`
# ---------------------------------------------------------------------------


class ProfileBindTests(unittest.TestCase):
    def test_bind_rejects_unknown_role_via_argparse(self):
        # 中文注释：DB 不可达时 argparse choices 回退到 ['research']，
        # 'planning' 不在内必须被 argparse 拒绝。
        with patch("persistence.session.transaction", side_effect=RuntimeError):
            code, _stdout, stderr = _capture_run(
                ["profile", "bind", "planning", "my-profile"]
            )
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", stderr)

    def test_bind_refused_when_profile_missing(self):
        # 中文注释：profile_exists False 时不应触达数据库。
        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "hermes.process.profile_exists", return_value=False
        ):
            code, _stdout, stderr = _capture_run(
                ["profile", "bind", "research", "ghost-profile"]
            )
        self.assertEqual(code, 2)
        self.assertIn("ghost-profile", stderr)
        self.assertIn("does not exist", stderr)
        self.assertIn("hermes profile create", stderr)

    def test_bind_happy_path(self):
        binding = _make_binding("research", "my-profile", "enabled")
        repo = SimpleNamespace(
            upsert_binding=lambda role, name, description=None: binding
        )

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ), patch("hermes.process.profile_exists", return_value=True):
            code, stdout, _stderr = _capture_run(
                ["profile", "bind", "research", "my-profile", "--description", "x"]
            )
        self.assertEqual(code, 0)
        self.assertIn("bound research → my-profile", stdout)


# ---------------------------------------------------------------------------
# `profile enable` / `profile disable`
# ---------------------------------------------------------------------------


class ProfileStatusTests(unittest.TestCase):
    def test_enable_round_trip(self):
        repo = SimpleNamespace(
            set_binding_status=lambda _r, status: _make_binding(status=status)
        )

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["profile", "enable", "research"])
        self.assertEqual(code, 0)
        self.assertIn("status set to enabled", stdout)

    def test_disable_round_trip(self):
        repo = SimpleNamespace(
            set_binding_status=lambda _r, status: _make_binding(status=status)
        )

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["profile", "disable", "research"])
        self.assertEqual(code, 0)
        self.assertIn("status set to disabled", stdout)


# ---------------------------------------------------------------------------
# `profile hermes-available`
# ---------------------------------------------------------------------------


class ProfileHermesAvailableTests(unittest.TestCase):
    def _patch_subprocess(self, stdout: str, returncode: int = 0):
        completed = SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=""
        )
        return patch("subprocess.run", return_value=completed)

    def test_lists_profiles_from_hermes(self):
        payload = json.dumps([
            {"name": "default", "description": "默认"},
            {"name": "ctf-research-bot", "description": "research bot"},
        ])
        with patch("persistence.session.transaction", side_effect=RuntimeError):
            with self._patch_subprocess(payload, 0):
                code, stdout, _stderr = _capture_run(["profile", "hermes-available"])
        self.assertEqual(code, 0)
        self.assertIn("default", stdout)
        self.assertIn("ctf-research-bot", stdout)

    def test_empty_list_is_handled(self):
        with patch("persistence.session.transaction", side_effect=RuntimeError):
            with self._patch_subprocess("[]", 0):
                code, stdout, _stderr = _capture_run(["profile", "hermes-available"])
        self.assertEqual(code, 0)
        self.assertIn("no Hermes profiles installed", stdout)

    def test_hermes_failure_exits_2(self):
        completed = SimpleNamespace(returncode=2, stdout="", stderr="hermes blew up")
        with patch("persistence.session.transaction", side_effect=RuntimeError):
            with patch("subprocess.run", return_value=completed):
                code, _stdout, stderr = _capture_run(["profile", "hermes-available"])
        self.assertEqual(code, 2)
        self.assertIn("hermes blew up", stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
