"""Hermes Research Agent 调用辅助函数测试。"""

from __future__ import annotations

from types import SimpleNamespace

from core.paths import ProjectPaths
from hermes import process as hermes_process
from hermes import research as hermes_research
from hermes.process import HermesProcessResult


def test_build_arguments_injects_profile_before_chat(monkeypatch):
    # 中文注释：验证普通 Hermes 命令会在 chat 子命令前插入 profile 参数。
    monkeypatch.setattr(
        hermes_process,
        "hermes_arguments",
        lambda: ["hermes", "chat", "-Q", "--yolo", "-q"],
    )

    hermes_arguments = hermes_research._build_arguments("research-bot")

    assert hermes_arguments == [
        "hermes",
        "-p",
        "research-bot",
        "chat",
        "-Q",
        "--yolo",
        "-q",
    ]


def test_build_arguments_handles_uvx_wrapped_command(monkeypatch):
    # 中文注释：验证 uvx 包装命令仍能定位 hermes chat 并正确插入 profile 参数。
    monkeypatch.setattr(
        hermes_process,
        "hermes_arguments",
        lambda: ["uvx", "--from", "hermes-agent", "hermes", "chat", "-Q", "-q"],
    )

    hermes_arguments = hermes_research._build_arguments("research-bot")

    assert hermes_arguments == [
        "uvx",
        "--from",
        "hermes-agent",
        "hermes",
        "-p",
        "research-bot",
        "chat",
        "-Q",
        "-q",
    ]


def test_invoke_research_agent_forwards_capture_arguments(monkeypatch, tmp_path):
    # 中文注释：验证 Research Agent 调用会把命令、日志路径、工作目录和超时转交给捕获执行器。
    captured_call_map = {}

    def fake_invoke_capture(prompt_text, **keyword_args):
        captured_call_map["prompt_text"] = prompt_text
        captured_call_map.update(keyword_args)
        return HermesProcessResult(returncode=0, stdout='{"sources":[],"findings":[]}', cancelled=False)

    monkeypatch.setattr(
        hermes_process,
        "hermes_arguments",
        lambda: ["hermes", "chat", "-Q", "-q"],
    )
    monkeypatch.setattr(hermes_process, "apply_legacy_custom_provider", lambda *_args: False)
    monkeypatch.setattr(hermes_research, "invoke_capture", fake_invoke_capture)

    project_paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    log_path = tmp_path / "research.log"

    res_data = hermes_research.invoke_research_agent(
        "research prompt",
        profile_name="research-bot",
        log_path=log_path,
        timeout=30,
        paths=project_paths,
    )

    assert res_data.returncode == 0
    assert captured_call_map["prompt_text"] == "research prompt"
    assert captured_call_map["arguments"] == [
        "hermes",
        "-p",
        "research-bot",
        "chat",
        "-Q",
        "-q",
    ]
    assert captured_call_map["log_path"] == log_path
    assert captured_call_map["cwd"] == tmp_path
    assert captured_call_map["timeout"] == 30


def test_profile_exists_uses_profile_show_command(monkeypatch):
    # 中文注释：验证 profile 存在性检查会把 chat 命令改写成 profile show 命令。
    captured_command_map = {}

    def fake_run(profile_arguments, **keyword_args):
        captured_command_map["profile_arguments"] = profile_arguments
        captured_command_map["keyword_args"] = keyword_args
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        hermes_process,
        "hermes_arguments",
        lambda: ["uvx", "--from", "hermes-agent", "hermes", "chat", "-Q", "-q"],
    )
    monkeypatch.setattr(hermes_process.subprocess, "run", fake_run)

    assert hermes_process.profile_exists("research-bot") is True
    assert captured_command_map["profile_arguments"] == [
        "uvx",
        "--from",
        "hermes-agent",
        "hermes",
        "profile",
        "show",
        "research-bot",
    ]
    assert captured_command_map["keyword_args"]["timeout"] == 10


def test_profile_exists_returns_false_on_missing_binary(monkeypatch):
    # 中文注释：验证 Hermes 不可执行时，profile 检查统一返回 False 方便上层处理。
    monkeypatch.setattr(hermes_process, "hermes_arguments", lambda: ["/missing/hermes", "chat"])

    def fake_run(_profile_arguments, **_keyword_args):
        raise FileNotFoundError

    monkeypatch.setattr(hermes_process.subprocess, "run", fake_run)

    assert hermes_process.profile_exists("research-bot") is False
