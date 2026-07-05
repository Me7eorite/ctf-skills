from __future__ import annotations

import socket
import threading
import time

import domain.pwn_debug as pwn_debug
from domain.pwn_debug import (
    classify_leak_value,
    classify_pwn_failure_stage,
    run_pwn_debug,
    tcp_readiness_probe,
)


def test_tcp_readiness_probe_succeeds_without_waiting_for_eof() -> None:
    ready = threading.Event()
    stop = threading.Event()

    def serve(sock: socket.socket) -> None:
        sock.listen(1)
        ready.set()
        conn, _addr = sock.accept()
        with conn:
            conn.sendall(b"Welcome\nChoice: ")
            stop.wait(1.0)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        thread = threading.Thread(target=serve, args=(listener,), daemon=True)
        thread.start()
        assert ready.wait(1.0)

        started = time.monotonic()
        result = tcp_readiness_probe("127.0.0.1", port, timeout=0.5)
        elapsed = time.monotonic() - started
        stop.set()
        thread.join(timeout=1.0)

    assert result["status"] == "ready"
    assert result["matched_token"] == "Choice:"
    assert "Choice:" in result["raw_output_tail"]
    assert elapsed < 0.4


def test_payload_after_successful_probe_without_flag_is_payload_control_flow() -> None:
    stage = classify_pwn_failure_stage(
        status="nonzero_exit",
        stderr_tail="Service is ready\nleaked canary=0x4141414100\nfailed to extract flag\n",
        returncode=1,
        pwn_debug={
            "service_readiness": {
                "tcp_probe": {
                    "status": "ready",
                    "raw_output_tail": "Welcome\nChoice: ",
                }
            },
            "format_string_leak_sampling": {"stable": True},
            "exp_execution": {"returncode": 1},
        },
    )

    assert stage == "payload_control_flow"


def test_canary_candidate_classification_rejects_pointer_like_values() -> None:
    assert classify_leak_value("0x00007fffd331b600") == "stack"
    assert classify_leak_value("0x00007f1234dd2600") == "libc"
    assert classify_leak_value("0x0000555555555000") == "pie"
    assert classify_leak_value("0x0") == "null"
    assert classify_leak_value("0x50") == "small"

def test_pwn_debug_managed_uses_docker_compose_only(tmp_path, monkeypatch) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    (challenge / "deploy").mkdir(parents=True)
    (challenge / "deploy" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (challenge / "metadata.json").write_text('{"id":"pwn-0001","category":"pwn"}', encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, *, cwd=None, timeout=5, tail_limit=4000):
        commands.append(list(command))
        return {"status": "ok", "command": command, "returncode": 0}

    monkeypatch.setattr(pwn_debug, "_run_optional", fake_run)
    monkeypatch.setattr(pwn_debug, "_format_string_leak_sampling", lambda *a, **k: {"status": "skipped"})

    result = run_pwn_debug(challenge, service_mode="managed", run_exp=False, timeout=1)

    compose_commands = [command for command in commands if command and command[0] == "docker-compose"]
    assert result["service_mode"] == "managed"
    assert any(command[-2:] == ["up", "-d"] for command in compose_commands)
    assert any(command[-2:] == ["down", "--remove-orphans"] for command in compose_commands)
    assert all(command[:2] != ["docker", "compose"] for command in commands)
    assert all("-p" in command and "-f" in command for command in compose_commands)


def test_pwn_debug_not_started_does_not_run_exp_or_claim_readiness(tmp_path, monkeypatch) -> None:
    challenge = tmp_path / "pwn-0001-demo"
    challenge.mkdir()
    (challenge / "metadata.json").write_text('{"id":"pwn-0001","category":"pwn"}', encoding="utf-8")

    def fail_run_exp(*_args, **_kwargs):
        raise AssertionError("not_started mode must not run exp.py")

    monkeypatch.setattr(pwn_debug, "_run_exp", fail_run_exp)

    result = run_pwn_debug(challenge, service_mode="not_started", run_exp=True, timeout=1)

    assert result["failure_stage"] == "service_not_started"
    assert result["exp_execution"]["reason"] == "service_not_started"
    assert "did not start service" in result["actionable_summary"]


def test_external_connection_refused_is_external_unavailable_not_readiness() -> None:
    stage = classify_pwn_failure_stage(
        status="nonzero_exit",
        stderr_tail="ConnectionRefusedError: [Errno 111] Connection refused",
        returncode=1,
        pwn_debug={
            "service_mode": "external",
            "service_readiness": {"tcp_probe": {"status": "failed", "raw_output_tail": ""}},
        },
    )

    assert stage == "external_unavailable"
