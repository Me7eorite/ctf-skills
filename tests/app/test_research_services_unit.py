"""research services 的无数据库单元测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.paths import ProjectPaths
from domain.research import GenerationRequest, ResearchRun
from domain.research_validators import ResearchValidationError
from hermes.process import HermesProcessResult
from services.research_agent_executor import ResearchAgentExecutor, _parse_research_output
from services.research_job_service import _finding_source_ids
from services.research_worker import ResearchWorker, _sigterm_as_keyboard_interrupt


def test_parse_research_output_writes_raw_text(tmp_path):
    # 中文注释：raw_text 不入库，executor 负责写文件并替换成 raw_text_path。
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    run_id = uuid4()
    stdout_text = json.dumps(
        {
            "sources": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "summary": "Summary",
                    "content_hash": "a" * 64,
                    "raw_text": "captured body",
                }
            ],
            "findings": [
                {
                    "kind": "technique",
                    "label": "Technique",
                    "summary": "Finding summary",
                    "source_indices": [0],
                }
            ],
        }
    )

    source_payloads, finding_payloads = _parse_research_output(
        stdout_text,
        paths=paths,
        run_id=run_id,
    )

    raw_text_path = paths.research_sources / str(run_id) / "0.txt"
    assert raw_text_path.read_text(encoding="utf-8") == "captured body"
    assert "raw_text" not in source_payloads[0]
    assert source_payloads[0]["raw_text_path"] == str(raw_text_path)
    assert finding_payloads[0]["source_indices"] == [0]


@pytest.mark.parametrize(
    ("stdout_payload", "error_text"),
    [
        (
            {
                "sources": [
                    {"title": "A", "summary": "Summary", "content_hash": "a" * 64}
                ],
                "findings": [
                    {
                        "kind": "technique",
                        "label": "Technique",
                        "summary": "Finding summary",
                        "source_indices": [0],
                    }
                ],
            },
            "source field 'url'",
        ),
        (
            {
                "sources": [
                    {
                        "url": "https://example.com/a",
                        "title": "A",
                        "summary": "Summary",
                        "content_hash": "a" * 64,
                    }
                ],
                "findings": [
                    {
                        "kind": "technique",
                        "summary": "Finding summary",
                        "source_indices": [0],
                    }
                ],
            },
            "finding field 'label'",
        ),
        (
            {
                "sources": [
                    {
                        "url": "https://example.com/a",
                        "title": "A",
                        "summary": "Summary",
                        "content_hash": "a" * 64,
                    }
                ],
                "findings": [
                    {
                        "kind": "technique",
                        "label": "Technique",
                        "summary": "Finding summary",
                        "source_indices": [],
                    }
                ],
            },
            "source_indices must be non-empty",
        ),
    ],
)
def test_parse_research_output_rejects_incomplete_payloads(tmp_path, stdout_payload, error_text):
    # 中文注释：parse 阶段必须提前拒绝缺字段和空 source_indices，避免真实原因被 lease expired 覆盖。
    with pytest.raises(ResearchValidationError, match=error_text):
        _parse_research_output(
            json.dumps(stdout_payload),
            paths=ProjectPaths(root=tmp_path, repository=tmp_path),
            run_id=uuid4(),
        )


def test_finding_source_ids_rejects_negative_index():
    # 中文注释：source_indices 必须是 0-based 非负索引，不能使用 Python 负索引语义。
    with pytest.raises(ResearchValidationError, match="out of range"):
        _finding_source_ids({"source_indices": [-1]}, [uuid4()])


class FakeExecutorJobService:
    def __init__(self):
        self.failed_errors = []

    def get_binding(self, _role):
        return None

    def set_profile_name_used(self, *_args):
        return None

    def mark_run_started(self, *_args):
        return None

    def heartbeat(self, *_args):
        return True

    def complete_run_with_results(self, *_args, **_kwargs):
        raise ResearchValidationError("commit validation failed")

    def mark_run_failed(self, _run_id, _agent_id, _claim_token, last_error, **_kwargs):
        self.failed_errors.append(last_error)


def _make_generation_request(request_id):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return GenerationRequest(
        id=request_id,
        category="web",
        topic="SQL injection",
        target_count=1,
        difficulty_distribution={"easy": 1},
        runtime_constraints={},
        seed_urls=(),
        max_attempts=3,
        status="researching",
        created_at=now,
        updated_at=now,
    )


def _make_research_run(request_id):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return ResearchRun(
        id=uuid4(),
        generation_request_id=request_id,
        parent_run_id=None,
        attempt=1,
        status="running",
        claimed_by="worker-1",
        claim_token=uuid4(),
        claimed_at=now,
        heartbeat_at=now,
        lease_expires_at=now,
        started_at=None,
        finished_at=None,
        last_error=None,
        hermes_log_path=None,
        profile_name_used=None,
        created_at=now,
    )


def test_executor_marks_failed_when_commit_validation_fails(monkeypatch, tmp_path):
    # 中文注释：commit 阶段的 ResearchValidationError 必须转成 failed，而不是逃出 worker。
    request_id = uuid4()
    research_run = _make_research_run(request_id)
    job_service = FakeExecutorJobService()

    def fake_hermes_invoke(**_kwargs):
        return HermesProcessResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "sources": [
                        {
                            "url": "https://example.com/a",
                            "title": "A",
                            "summary": "Summary",
                            "content_hash": "a" * 64,
                        }
                    ],
                    "findings": [
                        {
                            "kind": "technique",
                            "label": "Technique",
                            "summary": "Finding summary",
                            "source_indices": [0],
                        }
                    ],
                }
            ),
            cancelled=False,
        )

    monkeypatch.setattr("services.research_agent_executor.profile_exists", lambda _name: True)
    executor = ResearchAgentExecutor(
        ProjectPaths(root=tmp_path, repository=tmp_path),
        hermes_invoke=fake_hermes_invoke,
    )
    executor.job_service = job_service
    executor._load_generation_request = lambda _request_id: _make_generation_request(request_id)

    executor.execute(
        research_run,
        "worker-1",
        lease_seconds=60,
        hermes_timeout_seconds=30,
    )

    assert job_service.failed_errors == ["commit validation failed"]


class FakeJobService:
    def __init__(self, runs):
        self.runs = list(runs)

    def claim_next_run(self, _agent_id, _lease_seconds):
        if not self.runs:
            return None
        return self.runs.pop(0)


class FakeAgentExecutor:
    def __init__(self):
        self.seen_runs = []

    def execute(self, research_run, _agent_id, _lease_seconds, _hermes_timeout_seconds):
        self.seen_runs.append(research_run)


def test_worker_processes_max_jobs(tmp_path):
    # 中文注释：worker 达到 max_jobs 后应停止，即使队列里还有可 claim 的任务。
    fake_runs = [SimpleNamespace(id=f"r{i}", attempt=1) for i in range(3)]
    job_service = FakeJobService(fake_runs)
    agent_executor = FakeAgentExecutor()
    worker = ResearchWorker(
        ProjectPaths(root=tmp_path, repository=tmp_path),
        job_service,
        agent_executor,
    )

    result = worker.run(
        "worker-1",
        loop=True,
        max_jobs=2,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        hermes_timeout_seconds=30,
    )

    assert result == {"processed": 2, "agent_id": "worker-1"}
    assert len(agent_executor.seen_runs) == 2


def test_worker_rejects_timeout_greater_than_lease(tmp_path):
    # 中文注释：配置错误必须在访问数据库队列前暴露。
    worker = ResearchWorker(
        ProjectPaths(root=tmp_path, repository=tmp_path),
        FakeJobService([]),
        FakeAgentExecutor(),
    )

    with pytest.raises(ValueError, match="less than lease_seconds"):
        worker.run(
            "worker-1",
            loop=False,
            lease_seconds=60,
            hermes_timeout_seconds=60,
        )


def test_worker_logs_transitions_to_injected_stream(tmp_path):
    # 中文注释：spec 9.2b 要求 transition 写 stderr；这里注入 StringIO 断言关键事件都出现。
    import io

    runs = [SimpleNamespace(id=f"r{i}", attempt=1) for i in range(2)]
    job_service = FakeJobService(runs)
    agent_executor = FakeAgentExecutor()
    log_stream = io.StringIO()
    worker = ResearchWorker(
        ProjectPaths(root=tmp_path, repository=tmp_path),
        job_service,
        agent_executor,
        log_stream=log_stream,
    )

    worker.run(
        "worker-2",
        loop=False,
        max_jobs=2,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        hermes_timeout_seconds=30,
    )
    output = log_stream.getvalue()
    assert "started" in output
    assert "claimed run" in output
    assert "finished run" in output
    assert "max_jobs=2" in output


def test_sigterm_handler_is_restored():
    # 中文注释：SIGTERM 转换只在 worker 运行期间生效，退出上下文后必须恢复原 handler。
    import signal

    previous_handler = signal.getsignal(signal.SIGTERM)
    with _sigterm_as_keyboard_interrupt():
        assert signal.getsignal(signal.SIGTERM) is not previous_handler
    assert signal.getsignal(signal.SIGTERM) == previous_handler
