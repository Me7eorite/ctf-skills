"""Tests for the structured challenge-design Hermes executor."""

from __future__ import annotations

import os
import sys

from core.paths import ProjectPaths
from hermes import design as hermes_design
from hermes import process as hermes_process
from hermes.process import HERMES_TIMEOUT_RETURNCODE, HermesProcessResult, invoke_capture
from services.design_agent_executor import (
    DesignChallengeExecutor,
    PROVIDER_RATE_LIMITED_ERROR,
    last_error_for_exit_code,
)


def test_invoke_design_agent_forwards_skill_prompt_profile_and_log(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    captured_call_map = {}

    def fake_invoke_capture(prompt_text, **keyword_args):
        captured_call_map["prompt_text"] = prompt_text
        captured_call_map.update(keyword_args)
        return HermesProcessResult(returncode=0, stdout='{"event":{},"challenges":[]}', cancelled=False)

    monkeypatch.setattr(
        hermes_process,
        "hermes_arguments",
        lambda: ["hermes", "chat", "-Q", "-q"],
    )
    monkeypatch.setattr(hermes_process, "apply_legacy_custom_provider", lambda *_args: False)
    monkeypatch.setattr(hermes_design, "invoke_capture", fake_invoke_capture)

    project_paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    log_path = tmp_path / "work" / "design" / "logs" / "attempt.log"
    prompt_text = "/skill design-challenges\n\nDesign one challenge."

    result = hermes_design.invoke_design_agent(
        prompt_text,
        profile_name="design-bot",
        log_path=log_path,
        timeout=45,
        paths=project_paths,
    )

    assert result.returncode == 0
    assert captured_call_map["prompt_text"].startswith("/skill design-challenges")
    assert captured_call_map["arguments"] == [
        "hermes",
        "-p",
        "design-bot",
        "chat",
        "-Q",
        "-q",
    ]
    assert captured_call_map["log_path"] == log_path
    assert captured_call_map["cwd"] == tmp_path
    assert captured_call_map["timeout"] == 45
    assert "HERMES_HOME" not in captured_call_map["environment"]


def test_invoke_design_agent_uses_supplied_cwd(
    monkeypatch,
    tmp_path,
):
    captured_call_map = {}

    def fake_invoke_capture(prompt_text, **keyword_args):
        captured_call_map["prompt_text"] = prompt_text
        captured_call_map.update(keyword_args)
        return HermesProcessResult(returncode=0, stdout='{"event":{},"challenges":[]}', cancelled=False)

    monkeypatch.setattr(
        hermes_process,
        "hermes_arguments",
        lambda: ["hermes", "chat", "-Q", "-q"],
    )
    monkeypatch.setattr(hermes_process, "apply_legacy_custom_provider", lambda *_args: False)
    monkeypatch.setattr(hermes_design, "invoke_capture", fake_invoke_capture)

    project_paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    workspace = tmp_path / "work" / "design" / "executions" / "attempt"

    hermes_design.invoke_design_agent(
        "Design one challenge.",
        profile_name="design-bot",
        log_path=tmp_path / "attempt.log",
        timeout=45,
        paths=project_paths,
        cwd=workspace,
    )

    assert captured_call_map["cwd"] == workspace
    assert captured_call_map["environment"]["HERMES_HOME"] == str(
        workspace / "hermes-home"
    )
    assert captured_call_map["environment"]["CTF_SKILLS_HERMES_SESSION_HOME"] == str(
        workspace / "hermes-home"
    )


def test_executor_returns_stdout_exit_code_and_duration(tmp_path):
    captured_call_map = {}

    def fake_invoke(prompt_text, **keyword_args):
        captured_call_map["prompt_text"] = prompt_text
        captured_call_map.update(keyword_args)
        return HermesProcessResult(returncode=0, stdout='{"ok": true}', cancelled=False)

    project_paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    executor = DesignChallengeExecutor(project_paths, hermes_invoke=fake_invoke)
    log_path = project_paths.design_logs / "attempt.log"

    stdout, exit_code, duration_s = executor.execute(
        "/skill design-challenges\n{}",
        "design-bot",
        30,
        log_path,
        project_paths.design_executions / "attempt-1",
    )

    assert stdout == '{"ok": true}'
    assert exit_code == 0
    assert duration_s >= 0
    assert captured_call_map["profile_name"] == "design-bot"
    assert captured_call_map["log_path"] == log_path
    assert captured_call_map["timeout"] == 30
    assert captured_call_map["paths"] == project_paths
    assert captured_call_map["cwd"] == project_paths.design_executions / "attempt-1"
    assert (project_paths.design_executions / "attempt-1").is_dir()


def test_executor_rejects_non_positive_timeout(tmp_path):
    executor = DesignChallengeExecutor(ProjectPaths(root=tmp_path, repository=tmp_path))

    try:
        executor.execute(
            "/skill design-challenges",
            "default",
            0,
            tmp_path / "x.log",
            tmp_path / "workspace",
        )
    except ValueError as exc:
        assert "timeout_seconds must be positive" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected ValueError")


def test_timeout_exit_code_maps_to_timeout_error():
    assert last_error_for_exit_code(HERMES_TIMEOUT_RETURNCODE) == "timeout"


def test_nonzero_exit_code_maps_to_hermes_error():
    assert last_error_for_exit_code(7) == "Hermes exited with 7"


def test_nonzero_rate_limit_output_maps_to_provider_rate_limited():
    assert (
        last_error_for_exit_code(
            1,
            "HTTP 429 Too Many Requests: quota exceeded",
        )
        == PROVIDER_RATE_LIMITED_ERROR
    )


def test_success_exit_code_has_no_error():
    assert last_error_for_exit_code(0) is None


def test_invoke_capture_replaces_invalid_output_bytes(tmp_path):
    log_path = tmp_path / "hermes.log"
    script = (
        "import sys; "
        "sys.stdout.buffer.write(b'\\x8a\\n'); "
        "sys.stderr.buffer.write(b'\\x8a\\n')"
    )

    result = invoke_capture(
        "prompt",
        arguments=[sys.executable, "-c", script],
        log_path=log_path,
        cwd=tmp_path,
        environment=os.environ.copy(),
        timeout=5,
    )

    assert result.returncode == 0
    assert "\ufffd" in result.stdout
    log_text = log_path.read_text(encoding="utf-8")
    assert "--- stdout ---" in log_text
    assert "--- stderr ---" in log_text
    assert "\ufffd" in log_text
