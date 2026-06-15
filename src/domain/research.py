"""Domain DTOs and value sets for the research-planning workflow.

Mirrors the eight tables introduced by Alembic revision
``0002_research_tables``. DTOs are frozen dataclasses; the allowed value
sets for the three PG enum columns plus the binding-status CHECK are
exposed as tuple constants. The category and role sets are NOT hardcoded
here — those are lookup tables and the repository queries them at
runtime.

Validation logic lives in :mod:`domain.research_validators`; this file
is purely data shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# ---------------------------------------------------------------------------
# Allowed-value sets for the three PG enum types, the binding status CHECK,
# and the difficulty whitelist. Mirror the migration; keep as tuples so they
# have a stable order usable in error messages.
# ---------------------------------------------------------------------------

GenerationRequestStatus: tuple[str, ...] = ("draft", "researching", "researched", "failed")
ResearchRunStatus: tuple[str, ...] = ("queued", "running", "completed", "failed")
ResearchFindingKind: tuple[str, ...] = ("technique", "variant", "scenario", "prerequisite")
BindingStatus: tuple[str, ...] = ("enabled", "disabled")
DIFFICULTY_LABELS: tuple[str, ...] = ("easy", "medium", "hard", "expert")


# ---------------------------------------------------------------------------
# Frozen dataclass DTOs. They are the in-memory view of the eight tables.
# Frozen is enforced at the field-reassignment level; mutable values like
# dicts inside a row are still mutable by convention.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChallengeCategory:
    code: str
    display_name: str
    description: str | None = None


@dataclass(frozen=True)
class AgentRole:
    code: str
    display_name: str
    description: str | None = None


@dataclass(frozen=True)
class HermesProfileBinding:
    role: str
    profile_name: str
    description: str | None
    status: str
    last_used_at: datetime | None
    last_used_run_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class GenerationRequest:
    id: UUID
    category: str
    topic: str
    target_count: int
    difficulty_distribution: Mapping[str, int]
    runtime_constraints: Mapping[str, Any]
    seed_urls: tuple[str, ...]
    max_attempts: int
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ResearchRun:
    id: UUID
    generation_request_id: UUID
    parent_run_id: UUID | None
    attempt: int
    status: str
    claimed_by: str | None
    claim_token: UUID | None
    claimed_at: datetime | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    hermes_log_path: str | None
    profile_name_used: str | None
    created_at: datetime


@dataclass(frozen=True)
class ResearchSource:
    id: UUID
    research_run_id: UUID
    url: str
    title: str
    summary: str
    content_hash: str
    fetched_at: datetime
    raw_text_path: str | None = None


@dataclass(frozen=True)
class ResearchFinding:
    id: UUID
    research_run_id: UUID
    kind: str
    label: str
    summary: str


@dataclass(frozen=True)
class ResearchFindingSource:
    finding_id: UUID
    source_id: UUID
