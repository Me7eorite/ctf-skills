"""Pure research-output parsing plus explicit raw-text materialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from core.paths import ProjectPaths
from domain.research_validators import (
    ResearchValidationError,
    apply_research_quality_gate,
    extract_terminal_json_object,
)


@dataclass(frozen=True)
class ParsedResearchOutput:
    sources: list[dict[str, Any]]
    findings: list[dict[str, Any]]


def parse_research_output(
    stdout_text: str,
    *,
    target_count: int = 1,
) -> ParsedResearchOutput:
    """Parse, normalize, and quality-gate Hermes research stdout without I/O."""
    res_data = extract_terminal_json_object(stdout_text)
    if res_data is None:
        raise ResearchValidationError("unparseable_output:no_terminal_json_object")
    ok, error = apply_research_quality_gate(res_data, target_count)
    if not ok:
        raise ResearchValidationError(error or "unparseable_output:quality_gate_failed")

    source_items = res_data.get("sources")
    finding_items = res_data.get("findings")
    if not isinstance(source_items, list):
        raise ResearchValidationError("research output field 'sources' must be a list")
    if not isinstance(finding_items, list):
        raise ResearchValidationError("research output field 'findings' must be a list")

    source_payloads = [
        _normalize_source_payload(source_item)
        for source_item in source_items
    ]
    finding_payloads = [
        _normalize_finding_payload(finding_item, source_count=len(source_payloads))
        for finding_item in finding_items
    ]
    return ParsedResearchOutput(sources=source_payloads, findings=finding_payloads)


def materialize_research_raw_text(
    parsed: ParsedResearchOutput,
    *,
    paths: ProjectPaths,
    run_id: UUID,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Write optional source raw_text into staging and return DB-ready payloads."""
    source_payloads: list[dict[str, Any]] = []
    for source_index, source in enumerate(parsed.sources):
        source_payload = dict(source)
        raw_text = source_payload.pop("raw_text", None)
        if raw_text is not None:
            if not isinstance(raw_text, str):
                raise ResearchValidationError("source raw_text must be a string when present")
            staging_dir = paths.research_sources_staging / str(run_id)
            staging_dir.mkdir(parents=True, exist_ok=True)
            staged_path = staging_dir / f"{source_index}.txt"
            staged_path.write_text(raw_text, encoding="utf-8")
            source_payload["raw_text_path"] = str(
                paths.research_sources / str(run_id) / f"{source_index}.txt"
            )
        source_payloads.append(source_payload)
    return source_payloads, [dict(finding) for finding in parsed.findings]


def _normalize_source_payload(source_item: Any) -> dict[str, Any]:
    if not isinstance(source_item, Mapping):
        raise ResearchValidationError("each source must be a JSON object")
    source_payload = dict(source_item)
    for field_name in ("url", "title", "summary", "content_hash"):
        _required_text(source_payload, field_name, "source")
    raw_text = source_payload.get("raw_text")
    if raw_text is not None and not isinstance(raw_text, str):
        raise ResearchValidationError("source raw_text must be a string when present")
    return source_payload


def _normalize_finding_payload(finding_item: Any, *, source_count: int) -> dict[str, Any]:
    if not isinstance(finding_item, Mapping):
        raise ResearchValidationError("each finding must be a JSON object")
    finding_payload = dict(finding_item)
    for field_name in ("kind", "label", "summary"):
        _required_text(finding_payload, field_name, "finding")
    source_indices = finding_payload.get("source_indices")
    if not isinstance(source_indices, list):
        raise ResearchValidationError("finding source_indices must be a list")
    if not source_indices:
        raise ResearchValidationError("finding source_indices must be non-empty")
    for source_index in source_indices:
        if not isinstance(source_index, int) or isinstance(source_index, bool):
            raise ResearchValidationError(
                f"finding source_indices must contain integers, got {source_index!r}"
            )
        if source_index < 0 or source_index >= source_count:
            raise ResearchValidationError(f"source index {source_index} is out of range")
    return finding_payload


def _required_text(payload: Mapping[str, Any], field_name: str, item_name: str) -> str:
    field_value = payload.get(field_name)
    if not isinstance(field_value, str) or not field_value:
        raise ResearchValidationError(
            f"{item_name} field {field_name!r} must be a non-empty string"
        )
    return field_value
