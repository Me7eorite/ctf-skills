from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import domain.pwn_debug as pwn_debug
from domain.pwn_debug import classify_leak_value, classify_pwn_failure_stage, tcp_readiness_probe


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


def test_pwn_debug_compose_command_uses_legacy_docker_compose_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    challenge = tmp_path / "pwn-demo"
    deploy = challenge / "deploy"
    deploy.mkdir(parents=True)
    compose = deploy / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_optional(command, *, cwd, timeout, tail_limit=4000):
        calls.append(list(command))
        return {"status": "ok", "command": list(command), "returncode": 0}

    monkeypatch.setattr(pwn_debug, "_run_optional", fake_run_optional)

    result = pwn_debug._compose_command(challenge, "ps", timeout=5)

    assert result["status"] == "ok"
    assert calls == [["docker-compose", "-f", str(compose), "ps"]]
