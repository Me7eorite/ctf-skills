"""Operator import tool for reviewed historical corpus fingerprints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from core.jsonio import read_json
from domain.challenge_corpus import (
    CORPUS_FINGERPRINT_SCHEMA_VERSION,
    CorpusHistoryStatus,
    canonical_token_fingerprint,
)
from persistence.repositories import CorpusRepository
from persistence.session import SessionFactory, transaction
from services.artifact_observation_governance import build_artifact_observation_payload


@dataclass(frozen=True)
class CorpusHistoryImportPreview:
    challenge_id: str
    category: str
    governance_scope: str
    fingerprint_version: int
    fingerprints: Mapping[str, Any]


@dataclass(frozen=True)
class CorpusHistoryImportResult(CorpusHistoryImportPreview):
    history_entry_id: str
    status: str


class CorpusHistoryImportService:
    """Fingerprint and optionally import manually reviewed historical artifacts."""

    def __init__(self, *, session_factory: SessionFactory | None = None) -> None:
        self.session_factory = session_factory or SessionFactory()

    def preview(
        self,
        challenge_dir: Path,
        *,
        challenge_id: str | None = None,
        category: str | None = None,
        profile: Mapping[str, Any] | None = None,
        build_contract: Mapping[str, Any] | None = None,
    ) -> CorpusHistoryImportPreview:
        resolved = challenge_dir.resolve()
        metadata = read_json(resolved / "metadata.json", {})
        if not isinstance(metadata, dict):
            metadata = {}
        effective_id = challenge_id or str(metadata.get("id") or resolved.name)
        effective_category = category or str(metadata.get("category") or resolved.parent.name)
        observation_payload = build_artifact_observation_payload(
            resolved,
            build_attempt_id=_ZERO_UUID,
            design_evidence_id=None,
            contract_sha256=None,
            required_profile=profile,
            build_contract=build_contract,
        )
        fingerprints = {
            "schema_version": CORPUS_FINGERPRINT_SCHEMA_VERSION,
            "semantic": _profile_fallback(profile, "semantic"),
            "solve": _profile_fallback(profile, "solve"),
            "implementation": _profile_fallback(profile, "implementation"),
            "combined": _profile_fallback(profile, "combined"),
            "source": _token_from_observation(observation_payload, "source_token_sha256"),
            "solver": _token_from_observation(observation_payload, "solver_token_sha256"),
            "intended_path": _token_from_observation(
                observation_payload,
                "intended_path_sha256",
            ),
        }
        return CorpusHistoryImportPreview(
            challenge_id=effective_id,
            category=effective_category,
            governance_scope="history_projection_only",
            fingerprint_version=CORPUS_FINGERPRINT_SCHEMA_VERSION,
            fingerprints=fingerprints,
        )

    def import_reviewed(
        self,
        challenge_dir: Path,
        *,
        status: str,
        audit_reason: str,
        challenge_id: str | None = None,
        category: str | None = None,
        profile: Mapping[str, Any] | None = None,
        build_contract: Mapping[str, Any] | None = None,
    ) -> CorpusHistoryImportResult:
        if status not in {item.value for item in CorpusHistoryStatus}:
            raise ValueError(f"unknown corpus history status {status!r}")
        if not audit_reason.strip():
            raise ValueError("audit_reason is required for reviewed import")
        preview = self.preview(
            challenge_dir,
            challenge_id=challenge_id,
            category=category,
            profile=profile,
            build_contract=build_contract,
        )
        with transaction(factory=self.session_factory) as session:
            entry = CorpusRepository(session).add_history_entry(
                challenge_id=preview.challenge_id,
                category=preview.category,
                fingerprint_version=preview.fingerprint_version,
                fingerprints=preview.fingerprints,
                status=status,
                audit_reason=audit_reason,
            )
        return CorpusHistoryImportResult(
            challenge_id=preview.challenge_id,
            category=preview.category,
            governance_scope=preview.governance_scope,
            fingerprint_version=preview.fingerprint_version,
            fingerprints=preview.fingerprints,
            history_entry_id=str(entry.id),
            status=entry.status,
        )


def _profile_fallback(profile: Mapping[str, Any] | None, key: str) -> str:
    if not isinstance(profile, Mapping):
        return "legacy-unprofiled"
    value = profile.get(key)
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return canonical_token_fingerprint(value).sha256
    return "legacy-unprofiled"


def _token_from_observation(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    fingerprints = payload.get("fingerprints")
    if isinstance(fingerprints, Mapping):
        value = fingerprints.get(key)
        if isinstance(value, str) and value:
            return {"sha256": value, "tokens": [], "token_count": 0}
    return canonical_token_fingerprint(()).as_mapping()


_ZERO_UUID = UUID("00000000-0000-0000-0000-000000000000")
