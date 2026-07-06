"""Optional Hermes-driven planner for hard/expert design tasks.

D5=b: easy/medium task planning stays deterministic (template-only) inside
:mod:`services.design_task_planning_service`; for hard and expert the
planner can optionally call Hermes once to lock the technique chain and
the business scenario seed BEFORE the full design call runs.

This module is intentionally side-effect free at import time and
self-contained — the only entry point is :class:`HermesPlannerService`.
When the calling code does not inject one, the planner simply does not
run and the template-only fallback applies.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.paths import ProjectPaths
from domain.design.parser import (
    _find_balanced_json_object_end,
    _strip_json_fences,
)
from domain.research import ResearchFinding
from hermes.design import invoke_design_agent
from hermes.process import HermesProcessResult

_LOGGER = logging.getLogger(__name__)

# Hard/expert planner calls are short by design — they only output 4 fields.
DEFAULT_PLANNER_TIMEOUT_SECONDS = 300
DEFAULT_PLANNER_PROFILE = "default"
_PLANNER_PROMPT_FILENAME = "design_planner_prompt.md"

# Output keys the planner MUST emit. ``novelty_seed`` is permitted to be
# null for hard but must be a substantive string for expert.
_REQUIRED_PLANNER_KEYS: tuple[str, ...] = (
    "considered_techniques",
    "chain_outline",
    "scenario_seed",
    "chosen_mechanism",
    "semantic_fingerprint",
    "diversity_rationale",
)


@dataclass(frozen=True)
class PlannerEnrichment:
    """Successful Hermes planner output, ready to merge into a candidate row."""

    considered_techniques: list[str]
    chain_outline: str
    scenario_seed: str
    novelty_seed: str | None
    chosen_mechanism: str
    semantic_fingerprint: str
    diversity_rationale: str
    raw_response: str


PlannerInvoke = Callable[..., HermesProcessResult]


class HermesPlannerService:
    """Run Hermes once to plan ONE hard/expert task.

    Any failure (timeout, JSON parse error, missing fields) returns
    ``None`` rather than raising, so the calling planner can fall back to
    its deterministic template without aborting the whole batch.
    """

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        profile_name: str = DEFAULT_PLANNER_PROFILE,
        timeout_seconds: int = DEFAULT_PLANNER_TIMEOUT_SECONDS,
        hermes_invoke: PlannerInvoke = invoke_design_agent,
        prompt_template: str | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.paths = paths
        self.profile_name = profile_name
        self.timeout_seconds = timeout_seconds
        self.hermes_invoke = hermes_invoke
        self._prompt_template = prompt_template or _load_prompt_template(paths)

    def plan(
        self,
        *,
        category: str,
        difficulty: str,
        topic: str,
        primary: ResearchFinding,
        secondaries: Sequence[ResearchFinding] = (),
        avoid_techniques: Sequence[str] = (),
        log_path: Path | None = None,
    ) -> PlannerEnrichment | None:
        """Return planner enrichment, or ``None`` on any failure."""
        if difficulty not in {"hard", "expert"}:
            return None

        prompt_text = self._prompt_template.format(
            category=category,
            difficulty=difficulty,
            topic=topic,
            primary_kind=primary.kind,
            primary_label=primary.label,
            primary_summary=primary.summary,
            secondary_block=_render_secondary_block(secondaries),
            avoid_techniques=_render_avoid_techniques(avoid_techniques),
        )

        actual_log = log_path or (
            self.paths.design_logs / f"planner-{primary.id}.log"
        )
        actual_log.parent.mkdir(parents=True, exist_ok=True)
        workspace = self.paths.design_executions / f"planner-{primary.id}"
        workspace.mkdir(parents=True, exist_ok=True)

        started_at = time.monotonic()
        try:
            result = self.hermes_invoke(
                prompt_text,
                profile_name=self.profile_name,
                log_path=actual_log,
                timeout=self.timeout_seconds,
                paths=self.paths,
                cwd=workspace,
            )
        except Exception as exc:  # noqa: BLE001 — defensive fallback
            _LOGGER.warning(
                "design_planner_hermes invoke failed: %s; falling back to template",
                exc,
            )
            return None
        elapsed = time.monotonic() - started_at

        if result.returncode != 0:
            _LOGGER.warning(
                "design_planner_hermes exited %s in %.1fs; falling back to template",
                result.returncode,
                elapsed,
            )
            return None

        parsed = _safe_parse(result.stdout)
        if parsed is None:
            _LOGGER.warning(
                "design_planner_hermes output not parseable; falling back"
            )
            return None

        try:
            enrichment = _validate_planner_payload(parsed, difficulty)
        except ValueError as exc:
            _LOGGER.warning(
                "design_planner_hermes payload rejected: %s; falling back",
                exc,
            )
            return None

        return PlannerEnrichment(
            considered_techniques=enrichment["considered_techniques"],
            chain_outline=enrichment["chain_outline"],
            scenario_seed=enrichment["scenario_seed"],
            novelty_seed=enrichment["novelty_seed"],
            chosen_mechanism=enrichment["chosen_mechanism"],
            semantic_fingerprint=enrichment["semantic_fingerprint"],
            diversity_rationale=enrichment["diversity_rationale"],
            raw_response=result.stdout,
        )


def _load_prompt_template(paths: ProjectPaths) -> str:
    path = paths.prompts / _PLANNER_PROMPT_FILENAME
    return path.read_text(encoding="utf-8")


def _render_secondary_block(secondaries: Sequence[ResearchFinding]) -> str:
    if not secondaries:
        return "  - (none)"
    lines = []
    for finding in secondaries:
        lines.append(
            f"  - [{finding.kind}] {finding.label}: {finding.summary}"
        )
    return "\n".join(lines)


def _render_avoid_techniques(avoid_techniques: Sequence[str]) -> str:
    unique = [item for item in dict.fromkeys(str(t).strip() for t in avoid_techniques if str(t).strip())]
    if not unique:
        return "  - (none)"
    return "\n".join(f"  - {item}" for item in unique)


def _safe_parse(stdout: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction — tolerates code fences and prose."""
    if not isinstance(stdout, str) or not stdout.strip():
        return None
    text = _strip_json_fences(stdout)
    cursor = 0
    while True:
        start = text.find("{", cursor)
        if start < 0:
            return None
        end = _find_balanced_json_object_end(text, start)
        if end is None:
            return None
        block = text[start : end + 1]
        cursor = end + 1
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "considered_techniques" in parsed:
            return parsed


def _validate_planner_payload(
    payload: dict[str, Any], difficulty: str
) -> dict[str, Any]:
    for key in _REQUIRED_PLANNER_KEYS:
        if key not in payload:
            raise ValueError(f"missing required planner field {key!r}")

    techniques = payload["considered_techniques"]
    if not isinstance(techniques, list) or not all(
        isinstance(t, str) and t.strip() for t in techniques
    ):
        raise ValueError("considered_techniques must be a list of non-empty strings")
    distinct = {t.strip().lower() for t in techniques}
    minimum = 3 if difficulty == "hard" else 2
    if len(distinct) < minimum:
        raise ValueError(
            f"{difficulty} planner requires at least {minimum} distinct techniques"
        )

    chain = payload["chain_outline"]
    if not isinstance(chain, str) or len(chain.strip()) < 30:
        raise ValueError("chain_outline must be a substantive string")

    scenario = payload["scenario_seed"]
    if not isinstance(scenario, str) or len(scenario.strip()) < 30:
        raise ValueError("scenario_seed must be a substantive string")

    chosen = payload["chosen_mechanism"]
    if not isinstance(chosen, str) or len(chosen.strip()) < 3:
        raise ValueError("chosen_mechanism must be a non-empty string")

    fingerprint = payload["semantic_fingerprint"]
    if not isinstance(fingerprint, str) or len(fingerprint.strip()) < 10:
        raise ValueError("semantic_fingerprint must be a substantive string")

    rationale = payload["diversity_rationale"]
    if not isinstance(rationale, str) or len(rationale.strip()) < 20:
        raise ValueError("diversity_rationale must explain the model choice")

    novelty = payload.get("novelty_seed")
    if difficulty == "expert":
        if not isinstance(novelty, str) or len(novelty.strip()) < 40:
            raise ValueError(
                "expert planner requires a substantive novelty_seed (>=40 chars)"
            )
    elif novelty is not None and not isinstance(novelty, str):
        raise ValueError("novelty_seed must be a string or null for hard")

    return {
        "considered_techniques": [t.strip() for t in techniques],
        "chain_outline": chain.strip(),
        "scenario_seed": scenario.strip(),
        "novelty_seed": (novelty.strip() if isinstance(novelty, str) else None),
        "chosen_mechanism": chosen.strip(),
        "semantic_fingerprint": fingerprint.strip(),
        "diversity_rationale": rationale.strip(),
    }
