"""Tests for src/domain/research.py — DTO + validator behaviour.

Pure-Python; no DB connection required.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from domain.research import (
    DIFFICULTY_LABELS,
    BindingStatus,
    ChallengeCategory,
    GenerationRequest,
    GenerationRequestStatus,
    ResearchFinding,
    ResearchFindingKind,
    ResearchRunStatus,
)
from domain.research_validators import (
    ResearchValidationError,
    validate_category,
    validate_distribution,
    validate_finding,
    validate_runtime_constraints,
)

# ---------------------------------------------------------------------------
# Allowed-value sets exist and contain the right labels.
# ---------------------------------------------------------------------------


def test_status_and_label_constants_have_expected_values():
    assert GenerationRequestStatus == ("draft", "researching", "researched", "failed")
    assert ResearchRunStatus == ("queued", "running", "completed", "failed")
    assert ResearchFindingKind == ("technique", "variant", "scenario", "prerequisite")
    assert BindingStatus == ("enabled", "disabled")
    assert DIFFICULTY_LABELS == ("easy", "medium", "hard", "expert")


# ---------------------------------------------------------------------------
# Frozen DTOs reject reassignment.
# ---------------------------------------------------------------------------


def test_dto_is_frozen():
    cat = ChallengeCategory(code="web", display_name="Web", description="...")
    with pytest.raises(FrozenInstanceError):
        cat.code = "pwn"  # type: ignore[misc]


def test_generation_request_accepts_seed_urls_tuple():
    now = datetime.now(tz=timezone.utc)
    req = GenerationRequest(
        id=uuid4(),
        category="web",
        topic="SQL injection",
        target_count=4,
        difficulty_distribution={"easy": 2, "medium": 2},
        runtime_constraints={},
        seed_urls=("https://example.com/a", "https://example.com/b"),
        max_attempts=3,
        status="draft",
        created_at=now,
        updated_at=now,
    )
    assert req.seed_urls == ("https://example.com/a", "https://example.com/b")
    assert req.category == "web"


def test_runtime_constraints_accept_windows_exe_target():
    constraints = validate_runtime_constraints(
        {"target_format": "exe", "target_platform": "windows/amd64"}
    )

    assert constraints == {
        "target_format": "exe",
        "target_platform": "windows/amd64",
    }


def test_runtime_constraints_accept_search_keywords_array_and_csv():
    assert validate_runtime_constraints(
        {"search_keywords": ["JWT kid", "path traversal"]}
    ) == {
        "search_keywords": ["JWT kid", "path traversal"],
    }
    assert validate_runtime_constraints(
        {"search_keywords": "prototype pollution, sandbox escape"}
    ) == {
        "search_keywords": ["prototype pollution", "sandbox escape"],
    }


def test_runtime_constraints_reject_empty_search_keywords():
    with pytest.raises(ResearchValidationError, match="search_keywords"):
        validate_runtime_constraints({"search_keywords": " , "})


def test_runtime_constraints_accept_generation_policy():
    constraints = validate_runtime_constraints(
        {
            "generation_policy": (
                "XOR 类题最多 9 题。\n"
                "solve.py 必须复现算法或从二进制中提取参数。"
            )
        }
    )

    assert "XOR 类题最多 9 题" in constraints["generation_policy"]
    assert "solve.py 必须复现算法" in constraints["generation_policy"]


def test_runtime_constraints_reject_empty_generation_policy():
    with pytest.raises(ResearchValidationError, match="generation_policy"):
        validate_runtime_constraints({"generation_policy": "   "})


# ---------------------------------------------------------------------------
# validate_distribution
# ---------------------------------------------------------------------------


def test_validate_distribution_ok():
    validate_distribution(20, {"easy": 5, "medium": 10, "hard": 5})


def test_validate_distribution_sum_mismatch():
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_distribution(20, {"easy": 5, "medium": 10, "hard": 3})
    msg = str(excinfo.value)
    assert "sums to 18" in msg
    assert "target_count is 20" in msg


def test_validate_distribution_unknown_label():
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_distribution(20, {"easy": 5, "trivial": 15})
    msg = str(excinfo.value)
    assert "trivial" in msg
    assert "easy" in msg  # allowed list mentioned in message


def test_validate_distribution_empty():
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_distribution(5, {})
    assert "empty" in str(excinfo.value)


def test_validate_distribution_negative_count():
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_distribution(5, {"easy": -1, "hard": 6})
    assert "non-negative" in str(excinfo.value)


def test_validate_distribution_target_not_positive():
    with pytest.raises(ResearchValidationError):
        validate_distribution(0, {"easy": 0})
    with pytest.raises(ResearchValidationError):
        validate_distribution(-3, {"easy": 0})


# ---------------------------------------------------------------------------
# validate_category
# ---------------------------------------------------------------------------


def test_validate_category_ok():
    validate_category("web", ["web", "pwn", "re"])
    validate_category("pwn", {"web", "pwn", "re"})  # accepts any Iterable


def test_validate_category_unknown():
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_category("crypto", ["web", "pwn", "re"])
    msg = str(excinfo.value)
    assert "crypto" in msg
    assert "pwn" in msg  # allowed set mentioned


def test_validate_category_empty():
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_category("", ["web"])
    assert "required" in str(excinfo.value)


def test_validate_category_none():
    with pytest.raises(ResearchValidationError):
        validate_category(None, ["web"])


# ---------------------------------------------------------------------------
# validate_finding
# ---------------------------------------------------------------------------


def test_validate_finding_ok():
    validate_finding("technique", [uuid4(), uuid4()])
    validate_finding("variant", [uuid4()])


def test_validate_finding_empty_sources():
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_finding("technique", [])
    assert "at least one source" in str(excinfo.value)


def test_validate_finding_duplicate_sources():
    sid = uuid4()
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_finding("technique", [sid, sid])
    msg = str(excinfo.value)
    assert "duplicate" in msg
    assert str(sid) in msg


def test_validate_finding_unknown_kind():
    with pytest.raises(ResearchValidationError) as excinfo:
        validate_finding("unknown_kind", [uuid4()])
    assert "unknown_kind" in str(excinfo.value)


def test_validate_finding_does_not_check_cross_run_membership():
    """The cross-run-source-belongs-to-this-run check is the
    repository's responsibility (it requires a DB query). The domain
    validator only checks structural rules.
    """
    # No exception even if the source ids would belong to other runs —
    # domain has no way to know, and that's intentional.
    validate_finding("technique", [uuid4(), uuid4(), uuid4()])


# ---------------------------------------------------------------------------
# ResearchValidationError is a ValueError (so callers may catch the broader
# exception type if they're handling generic input validation).
# ---------------------------------------------------------------------------


def test_research_validation_error_is_value_error():
    assert issubclass(ResearchValidationError, ValueError)


# ---------------------------------------------------------------------------
# ResearchFinding kind matches the enum set.
# ---------------------------------------------------------------------------


def test_research_finding_kind_round_trip():
    finding = ResearchFinding(
        id=uuid4(),
        research_run_id=uuid4(),
        kind="technique",
        label="union-based",
        summary="SELECT ... UNION SELECT ...",
    )
    assert finding.kind in ResearchFindingKind
