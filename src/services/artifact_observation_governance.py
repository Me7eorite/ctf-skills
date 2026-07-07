"""Shared helpers for governed artifact observations."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from core.jsonio import read_json
from domain.output_consistency import output_manifest_hash

OBSERVATION_ACCEPTED_STATUSES = frozenset({"passed"})
OBSERVATION_REVIEW_ACCEPTED_DECISION = "accepted"
_HARNESS_ASSERTIONS: Mapping[str, frozenset[str]] = {
    "artifact_direct_run": frozenset({"stdout_not_contains_flag", "must_fail"}),
    "fixture_assertion": frozenset({"non_empty", "equals", "contains"}),
    "solver_with_fixture": frozenset({"must_pass", "outputs_flag"}),
    "solver_without_fixture": frozenset({"must_fail", "stdout_not_contains_flag"}),
    "random_flag_rebuild": frozenset({"outputs_new_flag", "old_flag_rejected"}),
}


def build_artifact_observation_payload(
    challenge_dir: Path,
    *,
    build_attempt_id: UUID,
    design_evidence_id: UUID | None,
    contract_sha256: str | None,
    required_profile: Mapping[str, Any] | None = None,
    build_contract: Mapping[str, Any] | None = None,
    validation_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = read_json(challenge_dir / "metadata.json", {})
    if not isinstance(metadata, dict):
        metadata = {}
    challenge_id = str(metadata.get("id") or challenge_dir.name)
    category = str(metadata.get("category") or challenge_dir.parent.name or "")
    manifest_hash = output_manifest_hash({challenge_id: challenge_dir})
    observed_profile = _observed_profile(challenge_dir, metadata)
    contract_evaluation = _evaluate_build_contract(
        build_contract,
        metadata=metadata,
        validation_result=validation_result,
    )
    profile_compare = _profile_compare(required_profile, observed_profile)
    status, status_reason = _observation_status(
        metadata,
        validation_result,
        required_profile,
        observed_profile,
        contract_evaluation=contract_evaluation,
        profile_compare=profile_compare,
    )
    contract_checks = {
        "publishable": metadata.get("publishable") is True,
        "validation_status": metadata.get("validation_status"),
        "solve_status": metadata.get("solve_status"),
        "validation_contract_errors": metadata.get("validation_contract_errors")
        or (validation_result or {}).get("validation_contract_errors"),
        "status_reason": status_reason,
        "profile_compare": profile_compare,
        "required_profile_present": required_profile is not None,
        "build_contract_present": build_contract is not None,
        "harness_results": contract_evaluation["harness_results"],
        "asset_flow_results": contract_evaluation["asset_flow_results"],
        "acceptance_results": contract_evaluation["acceptance_results"],
        "failure_code": contract_evaluation["failure_code"],
    }
    negative_test_results = {
        "final_flag_candidate": _stable_string(
            metadata.get("validation_final_flag_candidate")
            or (validation_result or {}).get("validation_final_flag_candidate")
        ),
        "validation_contract_errors": metadata.get("validation_contract_errors")
        or (validation_result or {}).get("validation_contract_errors"),
        "forbidden_shortcuts": contract_evaluation["negative_results"],
    }
    fingerprints = {
        "challenge_id": challenge_id,
        "category": category,
        "artifact_manifest_sha256": manifest_hash,
        "contract_sha256": contract_sha256,
        "validation_status": metadata.get("validation_status"),
        "source_token_sha256": _tree_token_fingerprint(
            challenge_dir,
            ("deploy/src", "deploy", "src"),
            suffixes=(".py", ".js", ".ts", ".go", ".rs", ".c", ".cc", ".cpp", ".h", ".java"),
        ),
        "solver_token_sha256": _tree_token_fingerprint(
            challenge_dir,
            ("writenup",),
            suffixes=(".py", ".sage", ".sh", ".md"),
        ),
        "intended_path_sha256": _intended_path_fingerprint(build_contract),
    }
    return {
        "build_attempt_id": str(build_attempt_id),
        "design_evidence_id": str(design_evidence_id) if design_evidence_id else None,
        "contract_sha256": contract_sha256,
        "artifact_manifest_sha256": manifest_hash,
        "observed_profile": observed_profile,
        "contract_checks": contract_checks,
        "negative_test_results": negative_test_results,
        "fingerprints": fingerprints,
        "status": status,
    }


def observation_is_bound_and_accepted(
    observation: Any,
    *,
    build_attempt_id: UUID,
    design_evidence_id: UUID | None,
    contract_sha256: str | None,
    artifact_manifest_sha256: str | None,
) -> bool:
    if observation is None:
        return False
    if getattr(observation, "build_attempt_id", None) != build_attempt_id:
        return False
    if getattr(observation, "design_evidence_id", None) != design_evidence_id:
        return False
    if getattr(observation, "contract_sha256", None) != (contract_sha256 or ""):
        return False
    if getattr(observation, "artifact_manifest_sha256", None) != artifact_manifest_sha256:
        return False
    return getattr(observation, "status", None) in OBSERVATION_ACCEPTED_STATUSES


def observation_status_is_accepted(status: str | None) -> bool:
    """Return validation-layer acceptance for observations without review context."""

    return status in OBSERVATION_ACCEPTED_STATUSES


def observation_is_effectively_accepted(
    observation: Any,
    *,
    has_allowed_review: bool = False,
) -> bool:
    """Return validation-layer acceptance, including explicitly allowed review."""

    status = getattr(observation, "status", None)
    if status in OBSERVATION_ACCEPTED_STATUSES:
        return True
    return status == "inconclusive" and has_allowed_review


def _observed_profile(challenge_dir: Path, metadata: Mapping[str, Any]) -> dict[str, Any]:
    category = str(metadata.get("category") or "")
    detected_language = _stable_string(metadata.get("language")) or _detect_language(challenge_dir)
    detected_runtime = _stable_string(
        metadata.get("runtime") or metadata.get("framework")
    ) or _detect_runtime(challenge_dir)
    return {
        "artifact_format": _stable_string(
            metadata.get("target_format")
            or metadata.get("artifact_format")
            or metadata.get("format")
        )
        or _detect_artifact_format(challenge_dir, metadata)
        or "unknown",
        "architecture": _stable_string(
            metadata.get("architecture")
            or str(metadata.get("target_platform") or "").rsplit("/", 1)[-1]
        )
        or "unknown",
        "language": detected_language or "unknown",
        "runtime": detected_runtime or "unknown",
        "interaction": _stable_string(metadata.get("interaction"))
        or ("http_form" if category == "web" else "binary" if category in {"pwn", "re"} else "unknown"),
        "flag_concealment": _stable_string(metadata.get("flag_concealment"))
        or ("database_record" if category == "web" else "runtime_derived_key" if category == "pwn" else "unknown"),
        "imports_or_apis": _detect_imports_or_apis(challenge_dir),
        "solve_status": _stable_string(metadata.get("solve_status")) or "unknown",
        "validation_status": _stable_string(metadata.get("validation_status")) or "unknown",
    }


def _observation_status(
    metadata: Mapping[str, Any],
    validation_result: Mapping[str, Any] | None,
    required_profile: Mapping[str, Any] | None,
    observed_profile: Mapping[str, Any],
    contract_evaluation: Mapping[str, Any],
    profile_compare: str,
) -> tuple[str, str | None]:
    solve_status = _stable_string(
        (validation_result or {}).get("solve_status")
        or metadata.get("solve_status")
    )
    validation_status = _stable_string(
        (validation_result or {}).get("validation_status")
        or metadata.get("validation_status")
    )
    flag_candidate = _stable_string(
        (validation_result or {}).get("validation_final_flag_candidate")
        or metadata.get("validation_final_flag_candidate")
    )
    if metadata.get("publishable") is not True:
        return "failed", "publishable_false"
    if solve_status != "passed" or validation_status != "passed":
        if validation_status in {"unknown", ""} or solve_status in {"unknown", ""}:
            return "inconclusive", "validation_fields_unknown"
        return "failed", "validation_not_passed"
    if not flag_candidate:
        return "inconclusive", "missing_flag_candidate"
    if metadata.get("validation_contract_errors") or (validation_result or {}).get("validation_contract_errors"):
        return "failed", "validation_contract_errors"
    if required_profile is not None:
        if profile_compare == "mismatch":
            return "failed", "implementation_contract_mismatch"
        if profile_compare == "unknown":
            return "inconclusive", "observation_inconclusive"
    if contract_evaluation.get("status") == "failed":
        return "failed", _stable_string(contract_evaluation.get("failure_code")) or "implementation_contract_mismatch"
    if contract_evaluation.get("status") == "inconclusive":
        return "inconclusive", _stable_string(contract_evaluation.get("failure_code")) or "observation_inconclusive"
    return "passed", None


def _profile_compare(
    required_profile: Mapping[str, Any] | None,
    observed_profile: Mapping[str, Any],
) -> str:
    if required_profile is None:
        return "match"
    required_axis = required_profile.get("implementation")
    if not isinstance(required_axis, Mapping):
        return "match"
    unknown = False
    for key in (
        "artifact_format",
        "architecture",
        "language",
        "runtime",
        "interaction",
        "flag_concealment",
    ):
        expected = required_axis.get(key)
        if expected in {None, "", "unknown"}:
            continue
        actual = observed_profile.get(key)
        if actual in {None, "", "unknown"}:
            unknown = True
            continue
        if actual != expected:
            return "mismatch"
    return "unknown" if unknown else "match"


def _evaluate_build_contract(
    build_contract: Mapping[str, Any] | None,
    *,
    metadata: Mapping[str, Any],
    validation_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if build_contract is None:
        return _contract_result("passed")
    harness_results: list[dict[str, Any]] = []
    negative_results: list[dict[str, Any]] = []
    acceptance_results: list[dict[str, Any]] = []
    asset_flow_results: list[dict[str, Any]] = []

    for harness in _mapping_list(build_contract.get("forbidden_shortcuts")):
        result = _run_harness(harness, metadata=metadata, validation_result=validation_result)
        negative_results.append(result)
        harness_results.append(result)
    for harness in _mapping_list(build_contract.get("acceptance_tests")):
        result = _run_harness(harness, metadata=metadata, validation_result=validation_result)
        acceptance_results.append(result)
        harness_results.append(result)
    for stage in _mapping_list(build_contract.get("required_asset_flow")):
        stage_result = {
            "stage_id": _stable_string(stage.get("stage_id")),
            "verification": _run_harness(
                stage.get("verification_harness"),
                metadata=metadata,
                validation_result=validation_result,
            ),
            "dependency": _run_harness(
                stage.get("dependency_harness"),
                metadata=metadata,
                validation_result=validation_result,
            ),
        }
        asset_flow_results.append(stage_result)
        harness_results.append(stage_result["verification"])
        harness_results.append(stage_result["dependency"])

    for result in negative_results:
        if result["status"] == "failed":
            return _contract_result(
                "failed",
                failure_code="unintended_solution_succeeded",
                harness_results=harness_results,
                negative_results=negative_results,
                acceptance_results=acceptance_results,
                asset_flow_results=asset_flow_results,
            )
    for stage_result in asset_flow_results:
        if stage_result["verification"]["status"] == "failed":
            return _contract_result(
                "failed",
                failure_code="asset_flow_not_required",
                harness_results=harness_results,
                negative_results=negative_results,
                acceptance_results=acceptance_results,
                asset_flow_results=asset_flow_results,
            )
        if stage_result["dependency"]["status"] == "failed":
            return _contract_result(
                "failed",
                failure_code="solver_not_artifact_derived",
                harness_results=harness_results,
                negative_results=negative_results,
                acceptance_results=acceptance_results,
                asset_flow_results=asset_flow_results,
            )
    for result in acceptance_results:
        if result["status"] == "failed":
            return _contract_result(
                "failed",
                failure_code="implementation_contract_mismatch",
                harness_results=harness_results,
                negative_results=negative_results,
                acceptance_results=acceptance_results,
                asset_flow_results=asset_flow_results,
            )
    if any(result["status"] == "inconclusive" for result in harness_results):
        return _contract_result(
            "inconclusive",
            failure_code="observation_inconclusive",
            harness_results=harness_results,
            negative_results=negative_results,
            acceptance_results=acceptance_results,
            asset_flow_results=asset_flow_results,
        )
    return _contract_result(
        "passed",
        harness_results=harness_results,
        negative_results=negative_results,
        acceptance_results=acceptance_results,
        asset_flow_results=asset_flow_results,
    )


def _contract_result(
    status: str,
    *,
    failure_code: str | None = None,
    harness_results: list[dict[str, Any]] | None = None,
    negative_results: list[dict[str, Any]] | None = None,
    acceptance_results: list[dict[str, Any]] | None = None,
    asset_flow_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "failure_code": failure_code,
        "harness_results": harness_results or [],
        "negative_results": negative_results or [],
        "acceptance_results": acceptance_results or [],
        "asset_flow_results": asset_flow_results or [],
    }


def _run_harness(
    harness: Any,
    *,
    metadata: Mapping[str, Any],
    validation_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(harness, Mapping):
        return _harness_result("unknown", None, None, "inconclusive", "malformed_harness")
    kind = _stable_string(harness.get("test_kind"))
    assertion = _stable_string(harness.get("assertion"))
    harness_id = _stable_string(harness.get("id")) or _harness_key(harness)
    if kind not in _HARNESS_ASSERTIONS or assertion not in _HARNESS_ASSERTIONS.get(kind, frozenset()):
        return _harness_result(kind, assertion, harness_id, "failed", "unknown_harness")
    explicit = _explicit_harness_result(harness, metadata, validation_result)
    if explicit is not None:
        return _harness_result(kind, assertion, harness_id, explicit, "explicit_result")
    if kind == "artifact_direct_run" and assertion == "stdout_not_contains_flag":
        reveals = _first_present_bool(
            validation_result,
            metadata,
            keys=("direct_run_reveals_flag", "artifact_direct_run_reveals_flag"),
        )
        if reveals is True:
            return _harness_result(kind, assertion, harness_id, "failed", "direct_run_reveals_flag")
        if reveals is False:
            return _harness_result(kind, assertion, harness_id, "passed", "direct_run_clean")
    if kind == "fixture_assertion" and assertion == "non_empty":
        fixture_ref = _stable_string(harness.get("fixture_ref"))
        fixtures = _mapping_value(metadata.get("fixtures")) or _mapping_value((validation_result or {}).get("fixtures"))
        if fixture_ref and fixtures is not None and fixture_ref in fixtures:
            return _harness_result(
                kind,
                assertion,
                harness_id,
                "passed" if _stable_string(fixtures.get(fixture_ref)) else "failed",
                "fixture_value",
            )
    if kind == "solver_with_fixture" and assertion in {"must_pass", "outputs_flag"}:
        if _validation_passed(metadata, validation_result):
            return _harness_result(kind, assertion, harness_id, "passed", "host_validation_passed")
    return _harness_result(kind, assertion, harness_id, "inconclusive", "missing_host_evidence")


def _harness_result(
    kind: str | None,
    assertion: str | None,
    harness_id: str | None,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "id": harness_id,
        "test_kind": kind,
        "assertion": assertion,
        "status": status,
        "reason": reason,
    }


def _explicit_harness_result(
    harness: Mapping[str, Any],
    metadata: Mapping[str, Any],
    validation_result: Mapping[str, Any] | None,
) -> str | None:
    results = _mapping_value(metadata.get("contract_harness_results")) or {}
    validation_results = _mapping_value((validation_result or {}).get("contract_harness_results")) or {}
    key = _stable_string(harness.get("id")) or _harness_key(harness)
    for source in (validation_results, results):
        raw = source.get(key)
        normalized = _normalize_harness_status(raw)
        if normalized is not None:
            return normalized
    return None


def _normalize_harness_status(raw: Any) -> str | None:
    if raw is True:
        return "passed"
    if raw is False:
        return "failed"
    if isinstance(raw, Mapping):
        return _normalize_harness_status(raw.get("status"))
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"passed", "pass", "ok", "success"}:
            return "passed"
        if value in {"failed", "fail", "error"}:
            return "failed"
        if value in {"unknown", "inconclusive", "skipped", "not_run"}:
            return "inconclusive"
    return None


def _harness_key(harness: Mapping[str, Any]) -> str:
    parts = [
        _stable_string(harness.get("test_kind")) or "unknown",
        _stable_string(harness.get("assertion")) or "unknown",
        _stable_string(harness.get("artifact_ref"))
        or _stable_string(harness.get("fixture_ref"))
        or _stable_string(harness.get("input_fixture"))
        or "none",
    ]
    return ":".join(parts)


def _detect_artifact_format(challenge_dir: Path, metadata: Mapping[str, Any]) -> str | None:
    if _stable_string(metadata.get("docker_image")) or (challenge_dir / "deploy" / "Dockerfile").is_file():
        return "container"
    artifact = _stable_string(metadata.get("artifact"))
    if artifact:
        path = challenge_dir / artifact
        if path.suffix == ".wasm":
            return "wasm"
        if path.is_file():
            try:
                if path.read_bytes()[:4] == b"\x7fELF":
                    return "elf"
            except OSError:
                return None
    return None


def _detect_language(challenge_dir: Path) -> str | None:
    markers = (
        ("Cargo.toml", "rust"),
        ("go.mod", "go"),
        ("package.json", "javascript"),
        ("requirements.txt", "python"),
        ("pyproject.toml", "python"),
    )
    for relative, language in markers:
        if any(path.name == relative for path in challenge_dir.rglob(relative)):
            return language
    suffixes = {
        ".py": "python",
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".js": "javascript",
        ".ts": "typescript",
        ".java": "java",
    }
    counts: dict[str, int] = {}
    for path in _iter_text_files(challenge_dir, suffixes=tuple(suffixes)):
        language = suffixes[path.suffix]
        counts[language] = counts.get(language, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _detect_runtime(challenge_dir: Path) -> str | None:
    if any(path.name == "app.py" for path in (challenge_dir / "deploy").rglob("*.py")):
        return "flask"
    if any(path.name == "package.json" for path in challenge_dir.rglob("package.json")):
        return "node"
    return None


def _detect_imports_or_apis(challenge_dir: Path) -> list[str]:
    names: set[str] = set()
    for path in _iter_text_files(
        challenge_dir,
        suffixes=(".py", ".js", ".ts", ".go", ".rs", ".c", ".cpp", ".cc", ".java"),
    ):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:20000]
        except OSError:
            continue
        for pattern in (
            r"^\s*import\s+([A-Za-z0-9_./-]+)",
            r"^\s*from\s+([A-Za-z0-9_./-]+)\s+import",
            r"#include\s+[<\"]([^>\"]+)",
            r"use\s+([A-Za-z0-9_:]+)",
        ):
            for match in re.finditer(pattern, text, flags=re.MULTILINE):
                names.add(match.group(1).split(".")[0].split("/")[0])
    return sorted(names)[:32]


def _tree_token_fingerprint(
    challenge_dir: Path,
    roots: Iterable[str],
    *,
    suffixes: tuple[str, ...],
) -> str | None:
    tokens: list[str] = []
    for root in roots:
        base = challenge_dir / root
        if not base.exists():
            continue
        for path in _iter_text_files(base, suffixes=suffixes):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            tokens.extend(_tokens(text))
    if not tokens:
        return None
    normalized = " ".join(sorted(tokens))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _intended_path_fingerprint(build_contract: Mapping[str, Any] | None) -> str | None:
    if build_contract is None:
        return None
    parts: list[str] = []
    for action in build_contract.get("required_player_actions") or []:
        if isinstance(action, str):
            parts.append(action)
    for stage in _mapping_list(build_contract.get("required_asset_flow")):
        value = _stable_string(stage.get("stage_id"))
        if value:
            parts.append(value)
    if not parts:
        return None
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()


def _iter_text_files(root: Path, *, suffixes: tuple[str, ...]) -> Iterable[Path]:
    if root.is_file():
        if root.suffix in suffixes:
            yield root
        return
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in suffixes:
            yield path


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
        if not token.startswith("flag")
    ]


def _mapping_list(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _mapping_value(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _first_present_bool(
    *sources: Mapping[str, Any] | None,
    keys: tuple[str, ...],
) -> bool | None:
    for source in sources:
        if source is None:
            continue
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def _validation_passed(
    metadata: Mapping[str, Any],
    validation_result: Mapping[str, Any] | None,
) -> bool:
    source = validation_result or {}
    return (
        _stable_string(source.get("solve_status") or metadata.get("solve_status")) == "passed"
        and _stable_string(
            source.get("validation_status") or metadata.get("validation_status")
        )
        == "passed"
        and bool(
            source.get("validation_final_flag_candidate")
            or metadata.get("validation_final_flag_candidate")
        )
    )


def _stable_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
