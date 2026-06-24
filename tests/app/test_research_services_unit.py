"""research services 的无数据库单元测试。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.paths import ProjectPaths
from domain.research import GenerationRequest, ResearchRun
from domain.research_validators import (
    ResearchValidationError,
    apply_research_quality_gate,
    extract_terminal_json_object,
)
from hermes.process import HermesProcessResult
from services.research_agent_executor import ResearchAgentExecutor, _parse_research_output
from services.research_job_service import _finding_source_ids
from services.research_output import materialize_research_raw_text, parse_research_output
from services.research_worker import ResearchWorker, _sigterm_as_keyboard_interrupt


def test_parse_research_output_is_pure_until_materialize(tmp_path):
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

    parsed = parse_research_output(stdout_text)

    staged_path = paths.research_sources_staging / str(run_id) / "0.txt"
    final_path = paths.research_sources / str(run_id) / "0.txt"
    assert not staged_path.exists()
    assert not final_path.exists()
    assert parsed.sources[0]["raw_text"] == "captured body"

    source_payloads, finding_payloads = materialize_research_raw_text(
        parsed,
        paths=paths,
        run_id=run_id,
    )

    assert staged_path.read_text(encoding="utf-8") == "captured body"
    assert not final_path.exists()  # promote 由 service 层在事务里做
    assert "raw_text" not in source_payloads[0]
    assert source_payloads[0]["raw_text_path"] == str(final_path)
    assert finding_payloads[0]["source_indices"] == [0]
    assert finding_payloads[0]["technique_family"] == "other"


def test_parse_research_output_preserves_valid_family_and_coerces_unknown(caplog):
    stdout_text = json.dumps(
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
                    "label": "blind SQLi",
                    "technique_family": "injection",
                    "summary": "Finding summary",
                    "source_indices": [0],
                },
                {
                    "kind": "technique",
                    "label": "JWT confusion",
                    "technique_family": "made-up",
                    "summary": "Finding summary",
                    "source_indices": [0],
                },
            ],
        }
    )

    with caplog.at_level("WARNING"):
        parsed = parse_research_output(stdout_text, target_count=1, category="web")

    assert parsed.findings[0]["technique_family"] == "injection"
    assert parsed.findings[1]["technique_family"] == "other"
    assert "unknown technique_family" in caplog.text


def test_parse_research_output_replaces_invalid_content_hash(caplog):
    stdout_text = json.dumps(
        {
            "sources": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "summary": "Summary",
                    "content_hash": "not-a-sha256",
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

    with caplog.at_level("WARNING"):
        parsed = parse_research_output(stdout_text)

    assert re.fullmatch(r"[0-9a-f]{64}", parsed.sources[0]["content_hash"])
    assert parsed.sources[0]["content_hash"] != "not-a-sha256"
    assert "replaced invalid research source content_hash at index 0" in caplog.text


def test_legacy_parse_wrapper_still_materializes_raw_text(tmp_path):
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

    source_payloads, _finding_payloads = _parse_research_output(
        stdout_text,
        paths=paths,
        run_id=run_id,
    )

    staged_path = paths.research_sources_staging / str(run_id) / "0.txt"
    assert staged_path.read_text(encoding="utf-8") == "captured body"
    assert source_payloads[0]["raw_text_path"] == str(
        paths.research_sources / str(run_id) / "0.txt"
    )


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
            "url_shape_invalid",
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


def test_terminal_json_extraction_and_quality_gate_contracts():
    parsed = extract_terminal_json_object(
        'debug {not json}\n```json\n{"sources": [], "findings": [{"summary": "brace } in text"}]}\n```'
    )
    assert parsed == {
        "sources": [],
        "findings": [{"summary": "brace } in text"}],
    }
    assert extract_terminal_json_object("no object here") is None

    valid_source = {
        "url": "https://example.com/source",
        "content_hash": "a" * 64,
    }
    assert apply_research_quality_gate(
        {"sources": [{**valid_source, "url": "not-a-url"}], "findings": [{}]},
        1,
    ) == (False, "url_shape_invalid:not-a-url")
    assert apply_research_quality_gate(
        {"sources": [{**valid_source, "content_hash": "bad"}], "findings": [{}]},
        1,
    ) == (False, "content_hash_shape_invalid:bad")
    assert apply_research_quality_gate(
        {"sources": [valid_source, valid_source], "findings": [{}]},
        1,
    ) == (False, f"content_hash_dup:{'a' * 64}")
    assert apply_research_quality_gate(
        {"sources": [valid_source], "findings": []},
        3,
    ) == (False, "insufficient_findings:got=0,need=2")


def test_quality_gate_ratio_env_var(monkeypatch):
    # GLM-5 deployments lower the ratio so target_count=10 only needs 3
    # findings, not 5. Default behavior (ratio=0.5) is preserved when
    # the env var is unset.
    from domain.research_validators import apply_research_quality_gate

    valid_source = {"url": "https://example.com/x", "content_hash": "a" * 64}
    payload = {
        "sources": [valid_source],
        "findings": [{}, {}, {}],  # only 3 findings
    }

    # Default ratio 0.5 with target_count=10 requires 5 → reject 3.
    monkeypatch.delenv("RESEARCH_QUALITY_RATIO", raising=False)
    monkeypatch.delenv("RESEARCH_QUALITY_SOFT_PASS_BELOW_BY", raising=False)
    ok, error = apply_research_quality_gate(payload, 10)
    assert (ok, error) == (False, "insufficient_findings:got=3,need=5")

    # Lower ratio to 0.3 → needs ceil(10*0.3)=3 → 3 passes.
    monkeypatch.setenv("RESEARCH_QUALITY_RATIO", "0.3")
    ok, error = apply_research_quality_gate(payload, 10)
    assert (ok, error) == (True, None)


def test_quality_gate_soft_pass_slack_env_var(monkeypatch, caplog):
    # Slack=1 with default 0.5 ratio: target_count=10 needs 5, accepts 4
    # with a warning.
    from domain.research_validators import apply_research_quality_gate

    valid_source = {"url": "https://example.com/x", "content_hash": "a" * 64}
    payload = {
        "sources": [valid_source],
        "findings": [{}, {}, {}, {}],  # 4 findings
    }

    monkeypatch.setenv("RESEARCH_QUALITY_RATIO", "0.5")
    monkeypatch.setenv("RESEARCH_QUALITY_SOFT_PASS_BELOW_BY", "1")

    with caplog.at_level("WARNING", logger="domain.research_validators"):
        ok, error = apply_research_quality_gate(payload, 10)

    assert (ok, error) == (True, None)
    assert any(
        "research quality gate soft-passed" in rec.message
        for rec in caplog.records
    )

    # got=2 still below the soft floor (needed=5 - slack=1 = 4) → reject.
    payload["findings"] = [{}, {}]
    ok, error = apply_research_quality_gate(payload, 10)
    assert (ok, error) == (False, "insufficient_findings:got=2,need=5")


def test_quality_gate_invalid_env_falls_back(monkeypatch):
    # Garbage env vars do not break the gate; the validator logs a
    # warning and uses the safe default.
    from domain.research_validators import apply_research_quality_gate

    valid_source = {"url": "https://example.com/x", "content_hash": "a" * 64}
    payload = {"sources": [valid_source], "findings": [{}]}

    monkeypatch.setenv("RESEARCH_QUALITY_RATIO", "not-a-float")
    monkeypatch.setenv("RESEARCH_QUALITY_SOFT_PASS_BELOW_BY", "-3")

    # Falls back to ratio=0.5 (needs ceil(2*0.5)=1) and slack=0 → 1 finding passes.
    ok, error = apply_research_quality_gate(payload, 2)
    assert (ok, error) == (True, None)


class _FakeBinding:
    # 中文注释：R1 之后 binding 缺失会 fail-fast，commit-validation 路径需要先满足
    # binding 这一关，所以提供一个 enabled binding 让测试能走到 commit 那一步。
    profile_name = "default"
    status = "enabled"


class FakeExecutorJobService:
    def __init__(self):
        self.failed_errors = []

    def get_binding(self, _role):
        return _FakeBinding()

    def set_profile_name_used(self, *_args):
        return None

    def mark_run_started(self, *_args, **_kwargs):
        return None

    def heartbeat(self, *_args):
        return True

    def complete_run_with_results(self, *_args, **_kwargs):
        raise ResearchValidationError("commit validation failed")

    def complete_run_with_staged_results(self, *_args, **_kwargs):
        # 中文注释：R2 之后 executor 走 staged 版本，仍然由 service 决定 commit 是否通过。
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


def _valid_research_stdout() -> str:
    return json.dumps(
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


class SuccessfulExecutorJobService(FakeExecutorJobService):
    def __init__(self):
        super().__init__()
        self.completed_runs = []

    def complete_run_with_staged_results(self, run_id, *_args, **_kwargs):
        self.completed_runs.append(run_id)


def test_executor_accepts_valid_stdout_from_nonzero_hermes_exit(monkeypatch, tmp_path):
    request_id = uuid4()
    research_run = _make_research_run(request_id)
    job_service = SuccessfulExecutorJobService()

    def fake_hermes_invoke(**_kwargs):
        return HermesProcessResult(
            returncode=7,
            stdout=_valid_research_stdout(),
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

    assert job_service.completed_runs == [research_run.id]
    assert job_service.failed_errors == []


class FakeJobService:
    def __init__(self, runs):
        self.runs = list(runs)

    def claim_next_run(self, _agent_id, _lease_seconds, **_kwargs):
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
