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

    def test_submit_prints_request_and_latest_run(self):
        # New contract (R6 / D8): CLI submit mirrors the HTTP response shape —
        # nested `{request, latest_run}` objects, no top-level `request_id`/`run_id`/`status`.
        request = _make_request(category="web", target_count=2, status="researching")
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
        self.assertEqual(payload["request"]["id"], str(request.id))
        self.assertEqual(payload["request"]["category"], "web")
        self.assertEqual(payload["request"]["status"], "researching")
        self.assertEqual(payload["request"]["display_status"], "queued")
        self.assertEqual(payload["latest_run"]["id"], str(run.id))
        self.assertEqual(payload["latest_run"]["status"], "queued")

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
                    lease_seconds, hermes_timeout_seconds, generation_request_id=None):
                captured["agent_id"] = agent_id
                captured["loop"] = loop
                captured["max_jobs"] = max_jobs
                captured["lease_seconds"] = lease_seconds
                captured["hermes_timeout_seconds"] = hermes_timeout_seconds
                captured["generation_request_id"] = generation_request_id
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
        self.assertIsNone(captured["generation_request_id"])
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

    def test_list_prints_beijing_time(self):
        request = _make_request(status="researching")
        repo = SimpleNamespace(
            list_categories=lambda: [],
            list_generation_requests=lambda **_kw: [request],
        )

        @contextlib.contextmanager
        def _ctx():
            yield "dummy-session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["research", "list"])
        self.assertEqual(code, 0)
        self.assertIn("created=2026-01-01T08:00:00+08:00", stdout)
        self.assertNotIn("+00:00", stdout)


class ResearchShowTests(unittest.TestCase):
    def test_show_prints_beijing_time(self):
        request_id = uuid4()
        run = _make_run(request_id=request_id, status="completed")
        run = type(run)(
            **{
                **{field: getattr(run, field) for field in run.__dataclass_fields__},
                "started_at": datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
                "finished_at": datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
            }
        )
        request = _make_request(request_id=request_id, status="researched")
        repo = SimpleNamespace(
            get_generation_request=lambda _rid: request,
            list_categories=lambda: [],
            list_runs=lambda **_kw: [run],
            list_sources=lambda _rid: [],
            list_findings=lambda _rid: [],
        )

        @contextlib.contextmanager
        def _ctx():
            yield "dummy-session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["research", "show", str(request_id)])
        self.assertEqual(code, 0)
        self.assertIn("created_at   : 2026-01-01T08:00:00+08:00", stdout)
        self.assertIn("started=2026-01-01T09:00:00+08:00", stdout)
        self.assertIn("finished=2026-01-01T10:00:00+08:00", stdout)
        self.assertNotIn("+00:00", stdout)


class ResearchSubmitMoreTests(unittest.TestCase):
    def test_max_attempts_forwarded(self):
        # 中文注释：--max-attempts 5 应原样传给 ResearchJobService.submit_request。
        captured = {}
        request = _make_request(category="web", target_count=2)
        run = _make_run(request_id=request.id, status="queued")

        class FakeJobService:
            def __init__(self, *_a, **_kw):
                pass

            def submit_request(self, **kwargs):
                captured.update(kwargs)
                return (request, run)

        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "services.ResearchJobService", FakeJobService
        ):
            code, _stdout, _stderr = _capture_run(
                ["research", "submit",
                 "--category", "web", "--topic", "x",
                 "--count", "2", "--difficulty", "easy:2",
                 "--max-attempts", "5"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured["max_attempts"], 5)

    def test_search_keywords_forwarded_as_runtime_constraint(self):
        captured = {}
        request = _make_request(category="web", target_count=2)
        run = _make_run(request_id=request.id, status="queued")

        class FakeJobService:
            def __init__(self, *_a, **_kw):
                pass

            def submit_request(self, **kwargs):
                captured.update(kwargs)
                return (request, run)

        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "services.ResearchJobService", FakeJobService
        ):
            code, _stdout, _stderr = _capture_run(
                [
                    "research",
                    "submit",
                    "--category",
                    "web",
                    "--topic",
                    "JWT auth",
                    "--count",
                    "2",
                    "--difficulty",
                    "easy:2",
                    "--search-keyword",
                    "kid traversal",
                    "--search-keyword",
                    "JWKS cache poisoning",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            captured["runtime_constraints"]["search_keywords"],
            ["kid traversal", "JWKS cache poisoning"],
        )

    def test_submit_has_no_timeout_flag(self):
        # 中文注释：submit 不应有 --timeout；argparse 必须拒绝。
        with patch("persistence.session.transaction", side_effect=RuntimeError):
            code, _stdout, stderr = _capture_run(
                ["research", "submit",
                 "--category", "web", "--topic", "x",
                 "--count", "1", "--difficulty", "easy:1",
                 "--timeout", "60"]
            )
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments", stderr)
        self.assertIn("--timeout", stderr)

    def test_submit_does_not_invoke_hermes(self):
        # 中文注释：submit 必须毫秒级返回；hermes_invoke 一旦被触达就 assert fail。
        request = _make_request(category="web", target_count=1)
        run = _make_run(request_id=request.id, status="queued")

        class FakeJobService:
            def __init__(self, *_a, **_kw):
                pass

            def submit_request(self, **_kw):
                return (request, run)

        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "services.ResearchJobService", FakeJobService
        ), patch(
            "hermes.research.invoke_research_agent",
            side_effect=AssertionError("Hermes must not be invoked by submit"),
        ):
            code, _stdout, _stderr = _capture_run(
                ["research", "submit",
                 "--category", "web", "--topic", "x",
                 "--count", "1", "--difficulty", "easy:1"]
            )
        self.assertEqual(code, 0)


class ResearchWorkerExecutionTests(unittest.TestCase):
    def test_worker_processes_exactly_max_jobs(self):
        # 中文注释：用真实 ResearchWorker，但桩出 service/executor；--max-jobs 2 时应只跑 2 次。
        executed: list = []

        class FakeJob:
            def __init__(self, *_a, **_kw):
                self.counter = 0

            def claim_next_run(self, _agent_id, _lease, **_kw):
                self.counter += 1
                return SimpleNamespace(id=uuid4(), claim_token=uuid4(), attempt=self.counter)

        class FakeExec:
            def __init__(self, *_a, **_kw):
                pass

            def execute(self, run, *_args, **_kw):
                executed.append(run.id)

        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "services.ResearchJobService", FakeJob
        ), patch("services.ResearchAgentExecutor", FakeExec):
            code, stdout, _stderr = _capture_run(
                ["research", "worker", "--agent-id", "w1",
                 "--max-jobs", "2",
                 "--lease-seconds", "60", "--hermes-timeout-seconds", "30"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(executed), 2)
        import json

        payload = json.loads(stdout)
        self.assertEqual(payload["processed"], 2)

    def test_worker_keyboard_interrupt_returns_interrupted_true(self):
        # 中文注释：claim 阶段被 KeyboardInterrupt 打断，应返回 interrupted=True 并 exit 0。
        class FakeJob:
            def __init__(self, *_a, **_kw):
                pass

            def claim_next_run(self, *_args, **_kw):
                raise KeyboardInterrupt

        class FakeExec:
            def __init__(self, *_a, **_kw):
                pass

            def execute(self, *_a, **_kw):
                pass

        with patch("persistence.session.transaction", side_effect=RuntimeError), patch(
            "services.ResearchJobService", FakeJob
        ), patch("services.ResearchAgentExecutor", FakeExec):
            code, stdout, _stderr = _capture_run(
                ["research", "worker", "--agent-id", "w1",
                 "--lease-seconds", "60", "--hermes-timeout-seconds", "30"]
            )
        self.assertEqual(code, 0)
        import json

        payload = json.loads(stdout)
        self.assertTrue(payload.get("interrupted"))
        self.assertEqual(payload["processed"], 0)


class ResearchWaitTimeoutTests(unittest.TestCase):
    def test_wait_timeout_exits_2(self):
        # 中文注释：run 一直处于非终态时，超过 --timeout 应当退出码 2。
        run_id = uuid4()
        run = _make_run(run_id=run_id, status="queued")
        repo = SimpleNamespace(get_run=lambda _: run)

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        # 中文注释：patch time.sleep + time.monotonic 让超时立刻命中。
        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ), patch("cli.time.sleep", return_value=None), patch(
            "cli.time.monotonic", side_effect=[0.0, 99.0, 99.0]
        ):
            code, stdout, _stderr = _capture_run(
                ["research", "wait", str(run_id), "--timeout", "5"]
            )
        self.assertEqual(code, 2)
        self.assertIn("timeout", stdout)


class ResearchShowMultiRunTests(unittest.TestCase):
    def test_show_renders_multiple_runs(self):
        # 中文注释：同一 generation_request 的 2 次 attempt 都应出现在输出。
        from domain.research import ChallengeCategory

        request = _make_request(category="web")
        runs = [
            _make_run(request_id=request.id, attempt=1, status="failed"),
            _make_run(request_id=request.id, attempt=2, status="completed"),
        ]
        cats = [
            ChallengeCategory("web", "Web 安全", None),
            ChallengeCategory("pwn", "Pwn", None),
            ChallengeCategory("re", "Reverse", None),
        ]
        repo = SimpleNamespace(
            get_generation_request=lambda _: request,
            list_categories=lambda: cats,
            list_runs=lambda **_kw: runs,
            list_sources=lambda _: [],
            list_findings=lambda _: [],
        )

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["research", "show", str(request.id)])
        self.assertEqual(code, 0)
        self.assertIn("runs (2)", stdout)
        self.assertIn("attempt=1", stdout)
        self.assertIn("attempt=2", stdout)
        self.assertIn("failed", stdout)
        self.assertIn("completed", stdout)


class ResearchListHappyPathTests(unittest.TestCase):
    def test_list_renders_filtered_requests(self):
        # 中文注释：--category web 过滤后，应只列出 web 行；输出包含 topic。
        from domain.research import ChallengeCategory

        web_req = _make_request(category="web", topic="SQLi")
        cats = [
            ChallengeCategory("web", "Web 安全", None),
            ChallengeCategory("pwn", "Pwn", None),
            ChallengeCategory("re", "Reverse", None),
        ]
        repo = SimpleNamespace(
            list_categories=lambda: cats,
            list_generation_requests=lambda **_kw: [web_req],
        )

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(
                ["research", "list", "--category", "web"]
            )
        self.assertEqual(code, 0)
        self.assertIn(str(web_req.id), stdout)
        self.assertIn("SQLi", stdout)
        self.assertIn("web", stdout)


class ResearchCategoriesOutputTests(unittest.TestCase):
    def test_categories_lists_each_row(self):
        # 中文注释：每行应包含 code + display_name + description。
        from domain.research import ChallengeCategory

        cats = [
            ChallengeCategory("web", "Web 安全", "HTTP/Web 服务的题目"),
            ChallengeCategory("pwn", "Pwn", "二进制利用"),
        ]
        repo = SimpleNamespace(list_categories=lambda: cats)

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["research", "categories"])
        self.assertEqual(code, 0)
        self.assertIn("web", stdout)
        self.assertIn("Web 安全", stdout)
        self.assertIn("pwn", stdout)
        self.assertIn("二进制利用", stdout)

    def test_categories_empty_message(self):
        repo = SimpleNamespace(list_categories=lambda: [])

        @contextlib.contextmanager
        def _ctx():
            yield "session"

        with patch("persistence.session.transaction", _ctx), patch(
            "persistence.repositories.ResearchRepository", return_value=repo
        ):
            code, stdout, _stderr = _capture_run(["research", "categories"])
        self.assertEqual(code, 0)
        self.assertIn("(no categories)", stdout)


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
