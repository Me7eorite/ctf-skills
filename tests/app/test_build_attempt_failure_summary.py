"""Unit coverage for build-attempt failure-summary derivation."""

from __future__ import annotations

from web.build_attempts_endpoints import _derive_failure_summary


def test_failure_summary_preserves_error_tail_with_spaces():
    summary = _derive_failure_summary(
        [
            {
                "stage": "validate",
                "status": "failed",
                "message": "validator: status=contract_failed error=validate.sh missing",
            }
        ],
        "shard execution failed",
    )

    assert summary == "校验失败：validate.sh missing"


def test_failure_summary_preserves_contract_error_details():
    summary = _derive_failure_summary(
        [
            {
                "stage": "validate",
                "status": "failed",
                "message": (
                    "validator: status=contract_failed "
                    "error=metadata.build_status is not passed; missing deploy/Dockerfile"
                ),
            }
        ],
        "shard execution failed",
    )

    assert summary == "校验失败：metadata.build_status is not passed; missing deploy/Dockerfile"


def test_failure_summary_localizes_broad_reconciler_error():
    assert _derive_failure_summary([], "shard execution failed") == "构建执行失败"
