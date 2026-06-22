from __future__ import annotations

import pytest

from domain.build_failure_taxonomy import classify_hermes_exit


def test_marker_auth_precedence():
    assert (
        classify_hermes_exit(
            1,
            "stack trace only",
            600.0,
            {"error_type": "authentication_error"},
        )
        == "hermes_auth"
    )


def test_marker_rate_limit_precedence():
    assert (
        classify_hermes_exit(1, "payload only", 4.0, {"status_code": 429})
        == "hermes_rate_limit"
    )


@pytest.mark.parametrize(
    "log_tail",
    [
        "Anthropic 401",
        "invalid x-api-key",
        "gic密钥已失效",
    ],
)
def test_fast_auth_tail_matches_auth_specific_text(log_tail):
    assert classify_hermes_exit(1, log_tail, 4.0) == "hermes_auth"


def test_slow_401_payload_is_runtime():
    assert classify_hermes_exit(1, "generated payload mentions 401", 600.0) == "hermes_runtime"


def test_bare_429_payload_is_runtime():
    assert classify_hermes_exit(1, "payload mentions 429 only", 4.0) == "hermes_runtime"


def test_provider_overload_tail_is_rate_limit():
    assert classify_hermes_exit(1, "provider overloaded_error", 4.0) == "hermes_rate_limit"


def test_generic_invalid_request_is_runtime():
    assert classify_hermes_exit(1, "invalid_request_error", 4.0) == "hermes_runtime"


def test_long_runtime_failure_is_runtime():
    assert classify_hermes_exit(1, "exploit failed", 600.0) == "hermes_runtime"


def test_timeout_and_cancelled_branches():
    assert classify_hermes_exit(124, "", 2700.0) == "hermes_timeout"
    assert classify_hermes_exit(-2, "", 12.0) == "hermes_cancelled"
    assert classify_hermes_exit(-15, "", 60.0) == "hermes_cancelled"


def test_zero_returncode_rejected():
    with pytest.raises(ValueError, match="non-zero"):
        classify_hermes_exit(0, "", 12.0)


@pytest.mark.parametrize("raw", ["0", "-1", "abc"])
def test_fail_fast_env_validation(monkeypatch, raw):
    monkeypatch.setenv("BUILD_HERMES_FAIL_FAST_MIN_SECONDS", raw)
    with pytest.raises(ValueError, match="BUILD_HERMES_FAIL_FAST_MIN_SECONDS"):
        classify_hermes_exit(1, "Anthropic 401", 4.0)
