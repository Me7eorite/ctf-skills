"""Consistency checks for validated execution outputs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from core.jsonio import read_json


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output_manifest_hash(candidates: Mapping[str, Path]) -> str:
    records: list[tuple[str, str, str, str, str]] = []
    for challenge_id, root in candidates.items():
        for path in sorted(
            [root, *root.rglob("*")],
            key=lambda item: item.relative_to(root).as_posix(),
        ):
            rel = path.relative_to(root).as_posix()
            stat = path.lstat()
            if path.is_dir():
                entry_type = "dir"
                content_hash = ""
            elif path.is_file():
                entry_type = "file"
                content_hash = file_sha256(path)
            else:
                raise OSError(f"special file is not allowed: {rel}")
            records.append(
                (
                    challenge_id,
                    rel,
                    entry_type,
                    str(stat.st_mode & 0o777),
                    content_hash,
                )
            )
    digest = hashlib.sha256()
    for record in records:
        digest.update(f"{len(record)}:".encode("ascii"))
        for field_value in record:
            encoded = field_value.encode("utf-8")
            digest.update(f"{len(encoded)}:".encode("ascii"))
            digest.update(encoded)
    return digest.hexdigest()


def validate_workspace_success_state(execution_current: Path) -> dict[str, Any]:
    """Validate that success marker, validation marker, and output tree agree."""
    state = execution_current / "state"
    publish_status = read_json(state / "publish-status.json", None)
    validated_output = read_json(state / "validated-output.json", None)
    if publish_status is None and validated_output is None:
        return {"ok": True, "legacy": True}
    if not isinstance(publish_status, dict):
        return _inconclusive(
            "publish-status.json missing or unreadable",
            code="publish_status_missing",
            path="state/publish-status.json",
            hint="Regenerate or repair the workspace terminal publish marker before treating the attempt as published.",
        )
    if publish_status.get("status") not in {"succeeded", "noop"}:
        return _inconclusive(
            f"publish-status.json status is {publish_status.get('status')!r}",
            code="publish_status_not_successful",
            path="state/publish-status.json",
            observed=str(publish_status.get("status")),
            hint="Inspect the failed publication phase and repair the workspace output before retrying validation.",
        )
    if not isinstance(validated_output, dict):
        return _inconclusive(
            "validated-output.json missing or unreadable",
            code="validated_output_missing",
            path="state/validated-output.json",
            hint=(
                "Re-run host validation so validated-output.json records the exact "
                "output tree and validation results."
            ),
        )

    publish_hash = publish_status.get("output_manifest_hash")
    validated_hash = validated_output.get("output_manifest_hash")
    if not isinstance(publish_hash, str) or not publish_hash:
        return _inconclusive(
            "publish-status.json has no output_manifest_hash",
            code="publish_manifest_hash_missing",
            path="state/publish-status.json",
            hint="Do not publish from a terminal marker without the validated output manifest hash.",
        )
    if publish_hash != validated_hash:
        return _inconclusive(
            "publish-status.json and validated-output.json manifest hashes differ",
            code="manifest_hash_mismatch",
            path="state/publish-status.json",
            source="state/validated-output.json",
            expected=str(validated_hash or ""),
            observed=publish_hash,
            hint=(
                "Use the output tree validated by state/validated-output.json; "
                "do not publish or trust a different manifest."
            ),
        )

    output_paths = validated_output.get("output_paths")
    validate_paths = validated_output.get("validate_paths")
    if not isinstance(output_paths, dict) or not output_paths:
        return _inconclusive(
            "validated-output.json has no output_paths",
            code="validated_output_paths_missing",
            path="state/validated-output.json",
            hint="Record the exact challenge output directory that passed host validation.",
        )
    if not isinstance(validate_paths, dict) or not validate_paths:
        return _inconclusive(
            "validated-output.json has no validate_paths",
            code="validated_validate_paths_missing",
            path="state/validated-output.json",
            hint="Record the exact validate.sh path for each validated challenge.",
        )

    candidates: dict[str, Path] = {}
    for challenge_id, rel in output_paths.items():
        if not isinstance(challenge_id, str) or not isinstance(rel, str):
            return _inconclusive(
                "validated-output.json output_paths are malformed",
                code="validated_output_paths_malformed",
                path="state/validated-output.json",
                hint="Rewrite output_paths as challenge_id to workspace-relative directory paths.",
            )
        path = execution_current / rel
        if not path.is_dir() or path.is_symlink():
            return _inconclusive(
                f"validated output path is missing: {rel}",
                code="validated_output_path_missing",
                path=rel,
                challenge_id=challenge_id,
                hint="Restore or regenerate the validated challenge output directory before repair or publication.",
            )
        candidates[challenge_id] = path
    for challenge_id, rel in validate_paths.items():
        if not isinstance(challenge_id, str) or not isinstance(rel, str):
            return _inconclusive(
                "validated-output.json validate_paths are malformed",
                code="validated_validate_paths_malformed",
                path="state/validated-output.json",
                hint="Rewrite validate_paths as challenge_id to workspace-relative validate.sh paths.",
            )
        path = execution_current / rel
        if not path.is_file() or path.is_symlink():
            return _inconclusive(
                f"validated validate.sh path is missing: {rel}",
                code="validated_validate_path_missing",
                path=rel,
                challenge_id=challenge_id,
                hint="Restore validate.sh at the exact path that was recorded during host validation.",
            )

    try:
        actual_hash = output_manifest_hash(candidates)
    except OSError as exc:
        return _inconclusive(
            str(exc),
            code="output_manifest_unreadable",
            path="output/",
            hint="Remove special files or unreadable entries from the final output tree.",
        )
    if actual_hash != validated_hash:
        return _inconclusive(
            "validated-output.json manifest hash does not match current output",
            code="validated_manifest_hash_mismatch",
            path="state/validated-output.json",
            source="output/",
            expected=str(validated_hash or ""),
            observed=actual_hash,
            hint=(
                "Re-run validation after any output mutation; do not publish an "
                "output tree that differs from the validated manifest."
            ),
        )

    result_check = _validated_results_ok(validated_output.get("results"))
    if result_check is not None:
        return result_check

    artifact_check = _candidate_artifacts_ok(candidates)
    if artifact_check is not None:
        return artifact_check

    return {"ok": True, "output_manifest_hash": actual_hash}


def _validated_results_ok(results: Any) -> dict[str, Any] | None:
    if not isinstance(results, list) or not results:
        return _inconclusive(
            "validated-output.json has no validation results",
            code="validated_results_missing",
            path="state/validated-output.json",
            hint="Re-run host validation and persist the per-challenge validation results.",
        )
    for result in results:
        if not isinstance(result, dict):
            return _inconclusive(
                "validated-output.json validation results are malformed",
                code="validated_results_malformed",
                path="state/validated-output.json",
                hint="Store validation results as a list of per-challenge objects.",
            )
        if (
            result.get("solve_status") != "passed"
            or result.get("validation_status") != "passed"
            or result.get("validation_returncode") != 0
            or not result.get("validation_final_flag_candidate")
        ):
            return _inconclusive(
                "validated-output.json does not contain a passed host validation",
                code="validated_result_not_passed",
                path="state/validated-output.json",
                challenge_id=str(result.get("challenge_id") or ""),
                hint=(
                    "Repair the challenge and re-run host validation until "
                    "validate.sh passes with a captured flag candidate."
                ),
            )
        if result.get("validation_contract_errors"):
            return _contract_failed(
                "validated-output.json retains validation_contract_errors",
                code="validated_result_contract_errors",
                path="state/validated-output.json",
                challenge_id=str(result.get("challenge_id") or ""),
                hint=(
                    "Keep the challenge in repair flow until validation_contract_errors "
                    "are cleared by a successful final validation."
                ),
            )
    return None


def _candidate_artifacts_ok(candidates: Mapping[str, Path]) -> dict[str, Any] | None:
    for challenge_id, root in candidates.items():
        metadata = read_json(root / "metadata.json", None)
        if not isinstance(metadata, dict):
            return _inconclusive(
                f"{challenge_id} metadata.json missing or unreadable",
                code="metadata_missing",
                path=f"{root.name}/metadata.json",
                challenge_id=challenge_id,
                hint="Restore metadata.json for the validated challenge output.",
            )
        if metadata.get("publishable") is not True:
            return _inconclusive(
                f"{challenge_id} metadata.json is not publishable",
                code="metadata_not_publishable",
                path="metadata.json",
                challenge_id=challenge_id,
                observed=str(metadata.get("publishable")),
                hint="Do not publish until host validation stamps metadata.publishable=true.",
            )
        if metadata.get("validation_status") != "passed":
            return _inconclusive(
                f"{challenge_id} metadata validation_status is not passed",
                code="metadata_validation_not_passed",
                path="metadata.json",
                challenge_id=challenge_id,
                observed=str(metadata.get("validation_status")),
                hint="Repair the failing challenge and let host validation stamp validation_status=passed.",
            )
        if metadata.get("validation_contract_errors"):
            return _contract_failed(
                f"{challenge_id} metadata retains validation_contract_errors",
                code="metadata_contract_errors",
                path="metadata.json",
                challenge_id=challenge_id,
                hint=(
                    "Do not clear validation_contract_errors by hand; repair the "
                    "contract violation and re-run validation."
                ),
            )
        report = read_json(root / "logs" / "report.json", None)
        if isinstance(report, dict):
            for entry in report.get("challenges", []):
                if not isinstance(entry, dict):
                    continue
                if entry.get("id") != challenge_id and entry.get("challenge_id") != challenge_id:
                    continue
                if entry.get("validation_contract_errors"):
                    return _contract_failed(
                        f"{challenge_id} logs/report.json retains validation_contract_errors",
                        code="report_contract_errors",
                        path="logs/report.json",
                        challenge_id=challenge_id,
                        hint=(
                            "Repair the final artifact/report mismatch and re-run "
                            "validation so report.json is restamped cleanly."
                        ),
                    )
                if entry.get("validation_status") not in {None, "passed"}:
                    return _inconclusive(
                        f"{challenge_id} logs/report.json validation_status is not passed",
                        code="report_validation_not_passed",
                        path="logs/report.json",
                        challenge_id=challenge_id,
                        observed=str(entry.get("validation_status")),
                        hint=(
                            "Re-run host validation after repair so logs/report.json "
                            "reports validation_status=passed."
                        ),
                    )
    return None


def _inconclusive(
    reason: str,
    *,
    code: str = "validation_inconclusive",
    path: str | None = None,
    source: str | None = None,
    challenge_id: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    return _failure(
        "validation_inconclusive",
        reason,
        code=code,
        path=path,
        source=source,
        challenge_id=challenge_id,
        expected=expected,
        observed=observed,
        hint=hint,
    )


def _contract_failed(
    reason: str,
    *,
    code: str = "contract_failed",
    path: str | None = None,
    source: str | None = None,
    challenge_id: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    return _failure(
        "contract_failed",
        reason,
        code=code,
        path=path,
        source=source,
        challenge_id=challenge_id,
        expected=expected,
        observed=observed,
        hint=hint,
    )


def _failure(
    status: str,
    reason: str,
    *,
    code: str,
    path: str | None,
    source: str | None,
    challenge_id: str | None,
    expected: str | None,
    observed: str | None,
    hint: str | None,
) -> dict[str, Any]:
    detail: dict[str, str] = {
        "phase": "publish_consistency",
        "code": code,
        "status": status,
        "message": reason,
        "repair_action": hint or "Repair the inconsistent validation/publication state before publishing.",
    }
    if path:
        detail["path"] = path
    if source:
        detail["source"] = source
    if challenge_id:
        detail["challenge_id"] = challenge_id
    if expected:
        detail["expected"] = expected
    if observed:
        detail["observed"] = observed
    if hint:
        detail["hint"] = hint
    return {
        "ok": False,
        "status": status,
        "reason": reason,
        "failure_details": [detail],
    }
