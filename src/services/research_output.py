"""Pure research-output parsing plus explicit raw-text materialization."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from core.paths import ProjectPaths
from domain.design.technique_taxonomy import CATEGORY_TECHNIQUE_FAMILIES, families_for_category, resolve_family
from domain.research_validators import (
    CONTENT_HASH_RE,
    ResearchValidationError,
    apply_research_quality_gate,
    extract_terminal_json_object,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedResearchOutput:
    sources: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    trial_only: bool = False


def parse_research_output(
    stdout_text: str,
    *,
    target_count: int = 1,
    category: str | None = None,
    enforce_quality: bool = True,
) -> ParsedResearchOutput:
    """Parse, normalize, and quality-gate Hermes research stdout without I/O."""
    res_data = extract_terminal_json_object(stdout_text)
    if not _is_research_result_shape(res_data):
        res_data = (
            _extract_research_result_object(stdout_text)
            or _extract_python_research_assignment(stdout_text)
        )
    if res_data is None:
        raise ResearchValidationError("unparseable_output:no_terminal_json_object")
    res_data = _normalize_legacy_result_shape(res_data)
    res_data = _normalize_source_content_hashes(res_data)
    source_items = res_data.get("sources")
    finding_items = res_data.get("findings")
    if not isinstance(source_items, list):
        raise ResearchValidationError("research output field 'sources' must be a list")
    if not isinstance(finding_items, list):
        raise ResearchValidationError("research output field 'findings' must be a list")
    if not source_items and not finding_items:
        raise ResearchValidationError("research_finalize_no_evidence")

    source_payloads = [
        _normalize_source_payload(source_item)
        for source_item in source_items
    ]
    finding_payloads = [
        _normalize_finding_payload(
            finding_item,
            source_count=len(source_payloads),
            source_payloads=source_payloads,
            category=category,
        )
        for finding_item in finding_items
    ]
    _reject_duplicate_findings(finding_payloads)
    res_data["sources"] = source_payloads
    res_data["findings"] = finding_payloads
    if enforce_quality:
        ok, error = apply_research_quality_gate(res_data, target_count, category=category)
        if not ok:
            raise ResearchValidationError(error or "unparseable_output:quality_gate_failed")
    return ParsedResearchOutput(
        sources=source_payloads,
        findings=finding_payloads,
        trial_only=not enforce_quality or not parsed_output_contains_designable_findings(
            ParsedResearchOutput(source_payloads, finding_payloads)
        ),
    )


def parsed_output_contains_designable_findings(parsed: ParsedResearchOutput) -> bool:
    return any(item.get("kind") in {"technique", "variant"} for item in parsed.findings)


def _is_research_result_shape(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("sources"), list)
        and isinstance(value.get("findings"), list)
    )


def _extract_python_research_assignment(stdout_text: str) -> dict[str, Any] | None:
    sources = _extract_last_python_list_assignment(stdout_text, "sources")
    findings = _extract_last_python_list_assignment(stdout_text, "findings")
    if isinstance(sources, list) and isinstance(findings, list):
        LOGGER.warning("recovered research output from Python list assignments in stdout")
        return {"sources": sources, "findings": findings}
    return None


def _extract_research_result_object(stdout_text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    result: dict[str, Any] | None = None
    for match in re.finditer(r"\{", stdout_text):
        try:
            parsed, _end = decoder.raw_decode(stdout_text, match.start())
        except json.JSONDecodeError:
            continue
        if _is_research_result_shape(parsed):
            result = parsed
    return result


def _extract_last_python_list_assignment(stdout_text: str, name: str) -> list[Any] | None:
    result: list[Any] | None = None
    for match in re.finditer(rf"(?m)^\+?{re.escape(name)}\s*=\s*\[", stdout_text):
        bracket_start = stdout_text.find("[", match.start())
        if bracket_start < 0:
            continue
        bracket_end = _matching_bracket_end(stdout_text, bracket_start)
        if bracket_end is None:
            continue
        try:
            parsed = ast.literal_eval(_strip_unified_diff_prefix(stdout_text[bracket_start : bracket_end + 1]))
        except (SyntaxError, ValueError):
            continue
        if isinstance(parsed, list):
            result = parsed
    return result


def _strip_unified_diff_prefix(text: str) -> str:
    return "\n".join(line[1:] if line.startswith("+") else line for line in text.splitlines())


def _matching_bracket_end(text: str, start: int) -> int | None:
    depth = 0
    in_string: str | None = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = None
            continue
        if char in ("'", '"'):
            in_string = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
    return None


def _normalize_legacy_result_shape(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Accept salvageable finalize JSON that used older compact field names."""
    normalized = dict(parsed)
    sources = normalized.get("sources")
    if isinstance(sources, list):
        normalized_sources: list[Any] = []
        for source in sources:
            if not isinstance(source, Mapping):
                normalized_sources.append(source)
                continue
            source_payload = dict(source)
            if "content_hash" not in source_payload and isinstance(source_payload.get("hash"), str):
                source_payload["content_hash"] = source_payload["hash"]
            if "summary" not in source_payload:
                title = source_payload.get("title")
                url = source_payload.get("url")
                if isinstance(title, str) and title.strip():
                    source_payload["summary"] = title.strip()
                elif isinstance(url, str) and url.strip():
                    source_payload["summary"] = url.strip()
            normalized_sources.append(source_payload)
        normalized["sources"] = normalized_sources

    findings = normalized.get("findings")
    if isinstance(findings, list):
        normalized_findings: list[Any] = []
        for index, finding in enumerate(findings):
            if not isinstance(finding, Mapping):
                normalized_findings.append(finding)
                continue
            finding_payload = dict(finding)
            content = finding_payload.get("content")
            if "summary" not in finding_payload and isinstance(content, str) and content.strip():
                finding_payload["summary"] = content.strip()
            if "label" not in finding_payload and isinstance(content, str) and content.strip():
                finding_payload["label"] = _derive_finding_label(finding_payload, index)
            if "kind" not in finding_payload and isinstance(content, str) and content.strip():
                finding_payload["kind"] = "technique"
            normalized_findings.append(finding_payload)
        normalized["findings"] = normalized_findings
    return normalized


def _derive_finding_label(finding: Mapping[str, Any], index: int) -> str:
    text = finding.get("summary") or finding.get("content")
    if isinstance(text, str):
        head = text.strip().split(".", 1)[0].split(":", 1)[0].strip()
        if head:
            return head[:80]
    return f"finding-{index + 1}"


def _normalize_source_content_hashes(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Replace model-invented hashes with stable sha256 values.

    The prompt asks for a lower-case sha256, but model-only research runs often
    emit mnemonic or truncated placeholders. The database only needs a stable
    deduplication key here, so derive one from the source fields instead of
    rejecting an otherwise usable run.
    """
    normalized = dict(parsed)
    sources = normalized.get("sources")
    if not isinstance(sources, list):
        return normalized
    normalized_sources: list[Any] = []
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            normalized_sources.append(source)
            continue
        source_payload = dict(source)
        content_hash = source_payload.get("content_hash")
        if not isinstance(content_hash, str) or not CONTENT_HASH_RE.match(content_hash):
            source_payload["content_hash"] = _derived_source_hash(source_payload)
            LOGGER.warning(
                "replaced invalid research source content_hash at index %s",
                index,
            )
        normalized_sources.append(source_payload)
    normalized["sources"] = normalized_sources
    return normalized


def _derived_source_hash(source: Mapping[str, Any]) -> str:
    payload = {
        "url": source.get("url"),
        "title": source.get("title"),
        "summary": source.get("summary"),
        "raw_text": source.get("raw_text"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _normalize_finding_payload(
    finding_item: Any,
    *,
    source_count: int,
    source_payloads: list[dict[str, Any]],
    category: str | None = None,
) -> dict[str, Any]:
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
    if len(source_indices) != len(set(source_indices)):
        raise ResearchValidationError("finding source_indices must not contain duplicates")
    for source_index in source_indices:
        if not isinstance(source_index, int) or isinstance(source_index, bool):
            raise ResearchValidationError(
                f"finding source_indices must contain integers, got {source_index!r}"
            )
        if source_index < 0 or source_index >= source_count:
            raise ResearchValidationError(f"source index {source_index} is out of range")
        _ = source_payloads[source_index]
    finding_payload["technique_family"] = _normalize_output_technique_family(
        finding_payload,
        category=category,
    )
    return finding_payload


def _normalize_output_technique_family(
    finding_payload: Mapping[str, Any],
    *,
    category: str | None,
) -> str:
    raw_family = finding_payload.get("technique_family")
    if not isinstance(raw_family, str) or not raw_family.strip():
        return resolve_family(finding_payload, category=category)
    normalized = raw_family.strip().lower().replace("-", "_").replace(" ", "_")
    if category is None:
        allowed = {family for lane_values in CATEGORY_TECHNIQUE_FAMILIES.values() for family in lane_values}
    else:
        allowed = set(families_for_category(category))
    if normalized not in allowed:
        raise ResearchValidationError(
            f"technique_family {raw_family!r} is not allowed; allowed: {sorted(allowed)}"
        )
    return normalized


def _reject_duplicate_findings(findings: list[dict[str, Any]]) -> None:
    seen: set[tuple[Any, ...]] = set()
    for finding in findings:
        key = (
            finding.get("kind"),
            str(finding.get("label")).strip().casefold(),
            str(finding.get("summary")).strip().casefold(),
            finding.get("technique_family"),
            tuple(finding.get("source_indices", [])),
        )
        if key in seen:
            raise ResearchValidationError("quality_gate:duplicate_finding")
        seen.add(key)


def _empty_parsed_output() -> ParsedResearchOutput:
    return ParsedResearchOutput(sources=[], findings=[])


def _required_text(payload: Mapping[str, Any], field_name: str, item_name: str) -> str:
    field_value = payload.get(field_name)
    if not isinstance(field_value, str) or not field_value:
        raise ResearchValidationError(
            f"{item_name} field {field_name!r} must be a non-empty string"
        )
    return field_value
