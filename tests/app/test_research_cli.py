"""CLI 测试：`challenge-factory research <subcommand>`。

不依赖真实数据库；通过 patch `services.*` 和 `persistence.session.transaction`
来覆盖 argparse + dispatch 行为。Postgres 端到端的覆盖在 Section 12 的
`tests/app/test_research_cli.py` 的 postgres-marked 子用例里（DB 用例命名
`test_*_db_*` 以便排除）。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import unittest
from datetime import datetime, timezone
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import cli

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _capture_run(argv: list[str]) -> tuple[int, str, str]:
    """Run cli.main() with `argv`; return (exit_code, stdout, stderr)."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = 0
    with patch.object(sys, "argv", ["challenge-factory", *argv]):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                cli.main()
            except SystemExit as exc:
                exit_code = int(exc.code or 0)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _make_request(
    *,
    request_id=None,
    category: str = "web",
    topic: str = "T",
    target_count: int = 5,
    distribution=None,
    seed_urls=(),
    max_attempts: int = 3,
    status: str = "draft",
):
    request_id = request_id or uuid4()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    from domain.research import GenerationRequest

    return GenerationRequest(
        id=request_id,
        category=category,
        topic=topic,
        target_count=target_count,
        difficulty_distribution=MappingProxyType(dict(distribution or {"easy": target_count})),
        runtime_constraints=MappingProxyType({}),
        seed_urls=tuple(seed_urls),
        max_attempts=max_attempts,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_run(
    *,
    run_id=None,
    request_id=None,
    status: str = "queued",
    attempt: int = 1,
):
    from domain.research import ResearchRun

    return ResearchRun(
        id=run_id or uuid4(),
        generation_request_id=request_id or uuid4(),
        parent_run_id=None,
        attempt=attempt,
        status=status,
        claimed_by=None,
        claim_token=None,
        claimed_at=None,
        heartbeat_at=None,
        lease_expires_at=None,
        started_at=None,
        finished_at=None,
        last_error=None,
        hermes_log_path=None,
        profile_name_used=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# `--difficulty` parsing
# ---------------------------------------------------------------------------


class ParseDifficultyTests(unittest.TestCase):
    def test_valid_distribution_parsed(self):
        # 中文注释：合法 distribution 应正确解析为 dict。
        self.assertEqual(cli._parse_difficulty("easy:2,medium:3"), {"easy": 2, "medium": 3})

    def test_whitespace_tolerated(self):
        self.assertEqual(cli._parse_difficulty(" easy : 2 , medium : 3 "), {"easy": 2, "medium": 3})

    def test_missing_colon_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_difficulty("easy2,medium:3")

    def test_non_integer_count_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_difficulty("easy:abc")

    def test_zero_count_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_difficulty("easy:0")

    def test_duplicate_label_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_difficulty("easy:1,easy:2")

    def test_empty_string_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._parse_difficulty("")


# ---------------------------------------------------------------------------
# `--category` choices: fallback when DB unreachable
# ---------------------------------------------------------------------------


class FetchCategoryChoicesTests(unittest.TestCase):
    def test_db_unreachable_falls_back_with_warning(self):
        # 中文注释：DB 不可达时回退到三元组并打 warning，不应让 argparse 构建失败。
        with patch(
            "persistence.session.transaction",
            side_effect=RuntimeError("connection refused"),
        ):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                choices = cli._fetch_category_choices()
        self.assertEqual(choices, ["web", "pwn", "re"])
        self.assertIn("falling back", stderr.getvalue())

    def test_empty_db_result_falls_back(self):
        # 中文注释：DB 可达但 challenge_categories 为空时仍然回退到三元组。
        fake_session_ctx = contextlib.nullcontext(None)
        with patch(
            "persistence.session.transaction", return_value=fake_session_ctx
        ), patch(
            "persistence.repositories.ResearchRepository"
        ) as repo_class:
            repo_class.return_value.list_categories.return_value = []
            choices = cli._fetch_category_choices()
        self.assertEqual(choices, ["web", "pwn", "re"])


# ---------------------------------------------------------------------------
# `research submit`
# ---------------------------------------------------------------------------


class ResearchSubmitTests(unittest.TestCase):
    def test_invalid_category_rejected_by_argparse(self):
        # 中文注释：argparse choices 应拒绝不在 DB 列表里的 category（DB 不可达时是 fallback 三元组）。
        with patch("persistence.session.transaction", side_effect=RuntimeError):
            code, _stdout, stderr = _capture_run(
                ["research", "submit",
                 "--category", "crypto",
                 "--topic", "x", "--count", "2",
                 "--difficulty", "easy:2"]
            )
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", stderr)

    def test_submit_prints_request_id_and_run_id(self):
        request = _make_request(category="web", target_count=2)
        run = _make_run(request_id=request.id, status="queued")
        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "services.ResearchJobService"
        ) as job_class:
            job_class.return_value.submit_request.return_value = (request, run)
            code, stdout, _stderr = _capture_run(
                ["research", "submit",
                 "--category", "web", "--topic", "SQLi",
                 "--count", "2", "--difficulty", "easy:2"]
            )
        self.assertEqual(code, 0)
        import json

        payload = json.loads(stdout)
        self.assertEqual(payload["request_id"], str(request.id))
        self.assertEqual(payload["run_id"], str(run.id))
        self.assertEqual(payload["category"], "web")
        self.assertEqual(payload["status"], "queued")

    def test_submit_distribution_mismatch_exits_2(self):
        from domain.research_validators import ResearchValidationError as RVE

        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "services.ResearchJobService"
        ) as job_class:
            job_class.return_value.submit_request.side_effect = RVE("distribution sum mismatch")
            code, _stdout, stderr = _capture_run(
                ["research", "submit",
                 "--category", "web", "--topic", "SQLi",
                 "--count", "2", "--difficulty", "easy:1"]
            )
        self.assertEqual(code, 2)
        self.assertIn("distribution sum mismatch", stderr)


# ---------------------------------------------------------------------------
# `research worker`
# ---------------------------------------------------------------------------


class ResearchWorkerTests(unittest.TestCase):
    def test_hermes_timeout_ge_lease_rejected(self):
        # 中文注释：argparse 层应在 default 都应用后再拒绝 timeout >= lease。
        with patch("persistence.session.transaction", side_effect=RuntimeError):
            code, _stdout, stderr = _capture_run(
                ["research", "worker",
                 "--agent-id", "w1",
                 "--lease-seconds", "60", "--hermes-timeout-seconds", "60"]
            )
        self.assertEqual(code, 2)
        self.assertIn("less than --lease-seconds", stderr)

    def test_worker_dispatches_to_research_worker(self):
        # 中文注释：CLI 只是入口；具体调度委托给 ResearchWorker.run。
        captured = {}

        class FakeWorker:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs

            def run(self, agent_id, *, loop, max_jobs, poll_interval_seconds,
                    lease_seconds, hermes_timeout_seconds):
                captured["agent_id"] = agent_id
                captured["loop"] = loop
                captured["max_jobs"] = max_jobs
                captured["lease_seconds"] = lease_seconds
                captured["hermes_timeout_seconds"] = hermes_timeout_seconds
                return {"processed": 0, "agent_id": agent_id}

        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "services.ResearchWorker", FakeWorker
        ), patch(
            "services.ResearchJobService"
        ), patch(
            "services.ResearchAgentExecutor"
        ):
            code, _stdout, _stderr = _capture_run(
                ["research", "worker",
                 "--agent-id", "w1",
                 "--max-jobs", "2",
                 "--lease-seconds", "60",
                 "--hermes-timeout-seconds", "30"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured["agent_id"], "w1")
        self.assertEqual(captured["max_jobs"], 2)
        self.assertEqual(captured["lease_seconds"], 60)
        self.assertEqual(captured["hermes_timeout_seconds"], 30)
        self.assertFalse(captured["loop"])

    def test_worker_ignores_HERMES_TIMEOUT_env_var(self):
        # 中文注释：HERMES_TIMEOUT 环境变量只服务于 shard 流程；research worker 不应读取。
        captured = {}

        class FakeWorker:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, agent_id, *, hermes_timeout_seconds, **kwargs):
                captured["hermes_timeout_seconds"] = hermes_timeout_seconds
                return {"processed": 0, "agent_id": agent_id}

        with patch.dict("os.environ", {"HERMES_TIMEOUT": "123"}), patch(
            "persistence.session.transaction", side_effect=RuntimeError
        ), patch("services.ResearchWorker", FakeWorker), patch(
            "services.ResearchJobService"
        ), patch("services.ResearchAgentExecutor"):
            _capture_run(["research", "worker", "--agent-id", "w1"])
        # 中文注释：CLI 默认值是 810，HERMES_TIMEOUT=123 必须被忽略。
        self.assertEqual(captured["hermes_timeout_seconds"], 810)


# ---------------------------------------------------------------------------
# `research wait`
# ---------------------------------------------------------------------------


class ResearchWaitTests(unittest.TestCase):
    def test_invalid_uuid_exit_3(self):
        with patch("persistence.session.transaction", side_effect=RuntimeError):
            code, _stdout, stderr = _capture_run(["research", "wait", "not-a-uuid"])
        self.assertEqual(code, 3)
        self.assertIn("not a valid uuid", stderr)

    def _wait_with_status(self, status: str, expected_code: int):
        run = _make_run(status=status)
        run_id = uuid4()
        run = _make_run(run_id=run_id, status=status)

        repo = SimpleNamespace(get_run=lambda _rid: run)

        @contextlib.contextmanager
        def _ctx():
            yield "dummy-session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["research", "wait", str(run_id)])
        self.assertEqual(code, expected_code)
        if expected_code in (0, 1):
            self.assertIn(status, stdout)

    def test_completed_exits_0(self):
        self._wait_with_status("completed", 0)

    def test_failed_exits_1(self):
        self._wait_with_status("failed", 1)

    def test_unknown_run_exits_3(self):
        repo = SimpleNamespace(get_run=lambda _rid: None)

        @contextlib.contextmanager
        def _ctx():
            yield "dummy-session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, _stdout, stderr = _capture_run(["research", "wait", str(uuid4())])
        self.assertEqual(code, 3)
        self.assertIn("not found", stderr)


# ---------------------------------------------------------------------------
# `research list`
# ---------------------------------------------------------------------------


class ResearchListTests(unittest.TestCase):
    def test_unknown_category_exits_2(self):
        # 中文注释：未知 category 应给出明确错误而不是静默返回空。
        from domain.research import ChallengeCategory

        repo = SimpleNamespace(
            list_categories=lambda: [
                ChallengeCategory(code="web", display_name="Web", description=None),
                ChallengeCategory(code="pwn", display_name="Pwn", description=None),
            ],
            list_generation_requests=lambda **_kw: [],
        )

        @contextlib.contextmanager
        def _ctx():
            yield "dummy-session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, _stdout, stderr = _capture_run(
                ["research", "list", "--category", "crypto"]
            )
        self.assertEqual(code, 2)
        self.assertIn("unknown category 'crypto'", stderr)


class DatabaseUrlMissingTests(unittest.TestCase):
    def test_list_without_database_url_prints_clean_error(self):
        # 中文注释：DATABASE_URL 未设置时必须给出明确错误而不是 Python traceback。
        from persistence.errors import PersistenceConfigurationError

        actual_msg = "DATABASE_URL is not set; persistence requires a PostgreSQL URL."
        with patch(
            "persistence.session.transaction",
            side_effect=PersistenceConfigurationError(actual_msg),
        ):
            code, _stdout, stderr = _capture_run(["research", "list"])
        self.assertEqual(code, 2)
        self.assertIn(actual_msg, stderr)
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
