"""Unit tests for the optional Hermes-driven hard/expert planner."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from core.paths import ProjectPaths
from domain.research import ResearchFinding
from hermes.process import HermesProcessResult
from services.design_planner_hermes import (
    HermesPlannerService,
    _safe_parse,
)


def _paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    paths.prompts.mkdir(parents=True, exist_ok=True)
    (paths.prompts / "design_planner_prompt.md").write_text(
        # The template is interpolated via str.format, so we use a minimal
        # body for the test that exercises every placeholder once.
        "category={category} difficulty={difficulty} topic={topic}\n"
        "primary=[{primary_kind}] {primary_label}: {primary_summary}\n"
        "secondaries:\n{secondary_block}\n",
        encoding="utf-8",
    )
    return paths


def _finding(label: str) -> ResearchFinding:
    return ResearchFinding(
        id=uuid4(),
        research_run_id=uuid4(),
        kind="technique",
        label=label,
        summary=f"summary about {label}",
    )


def _result(stdout: str, returncode: int = 0) -> HermesProcessResult:
    return HermesProcessResult(stdout=stdout, returncode=returncode, cancelled=False)


def test_plan_returns_none_for_easy_and_medium(tmp_path: Path):
    service = HermesPlannerService(
        paths=_paths(tmp_path),
        hermes_invoke=lambda *a, **kw: pytest.fail("Hermes should not be called"),
    )

    out = service.plan(
        category="web",
        difficulty="easy",
        topic="JWT",
        primary=_finding("DOM XSS"),
    )

    assert out is None


def test_plan_returns_enrichment_for_hard_with_valid_json(tmp_path: Path):
    captured = {}

    def fake_invoke(prompt, **kw):
        captured["prompt"] = prompt
        return _result(
            json.dumps(
                {
                    "considered_techniques": [
                        "JWT kid path traversal",
                        "Key confusion via kid override",
                        "Session token replay",
                    ],
                    "chain_outline": (
                        "Player observes the JWT kid claim, traverses to a "
                        "writable key path, signs a forged admin token, then "
                        "replays it to the admin endpoint."
                    ),
                    "chosen_mechanism": "kid path traversal to attacker-controlled signing key",
                    "semantic_fingerprint": "jwt-kid-path-key-confusion-admin-replay",
                    "diversity_rationale": (
                        "This uses key material routing and token replay rather "
                        "than a SQL or template injection web flow."
                    ),
                    "scenario_seed": (
                        "Internal customer-support note portal with a kid-based "
                        "rotating JWT signing scheme."
                    ),
                    "novelty_seed": None,
                }
            )
        )

    service = HermesPlannerService(paths=_paths(tmp_path), hermes_invoke=fake_invoke)

    out = service.plan(
        category="web",
        difficulty="hard",
        topic="JWT",
        primary=_finding("JWT kid path traversal"),
        secondaries=[_finding("Key confusion via kid override")],
    )

    assert out is not None
    assert len(out.considered_techniques) == 3
    assert out.scenario_seed.startswith("Internal")
    assert "writable key path" in out.chain_outline
    assert out.chosen_mechanism.startswith("kid path")
    assert out.semantic_fingerprint == "jwt-kid-path-key-confusion-admin-replay"
    assert "token replay" in out.diversity_rationale
    assert out.novelty_seed is None
    # The template MUST have been formatted with every placeholder once.
    assert "category=web difficulty=hard topic=JWT" in captured["prompt"]
    assert "[technique] JWT kid path traversal" in captured["prompt"]
    assert "Key confusion via kid override" in captured["prompt"]


def test_plan_rejects_expert_response_without_substantive_novelty(tmp_path: Path):
    def fake_invoke(prompt, **kw):
        return _result(
            json.dumps(
                {
                    "considered_techniques": [
                        "Algorithm confusion",
                        "Parser differential",
                    ],
                    "chain_outline": (
                        "Player triggers algorithm confusion across two "
                        "verifiers and forges a token."
                    ),
                    "chosen_mechanism": "dual-verifier parser differential",
                    "semantic_fingerprint": "jws-parser-differential-forgery",
                    "diversity_rationale": (
                        "The flow depends on inconsistent verifier behavior "
                        "instead of a static weak-key shortcut."
                    ),
                    "scenario_seed": (
                        "Internal monitoring stack with two JWS libraries "
                        "verifying the same token at different layers."
                    ),
                    "novelty_seed": "advanced",  # too short, < 40 chars
                }
            )
        )

    service = HermesPlannerService(paths=_paths(tmp_path), hermes_invoke=fake_invoke)

    out = service.plan(
        category="web",
        difficulty="expert",
        topic="JWS",
        primary=_finding("Algorithm confusion"),
    )

    assert out is None  # Falls back to template-only planning.


def test_plan_falls_back_when_hermes_returns_nonzero(tmp_path: Path):
    def fake_invoke(prompt, **kw):
        return _result(stdout="", returncode=2)

    service = HermesPlannerService(paths=_paths(tmp_path), hermes_invoke=fake_invoke)

    out = service.plan(
        category="web",
        difficulty="hard",
        topic="JWT",
        primary=_finding("X"),
    )
    assert out is None


def test_plan_falls_back_when_invoke_raises(tmp_path: Path):
    def fake_invoke(prompt, **kw):
        raise RuntimeError("simulated network error")

    service = HermesPlannerService(paths=_paths(tmp_path), hermes_invoke=fake_invoke)

    out = service.plan(
        category="web",
        difficulty="hard",
        topic="JWT",
        primary=_finding("X"),
    )
    assert out is None


def test_safe_parse_extracts_object_after_prose():
    stdout = (
        "Thinking out loud about the topic flag{ignored}\n"
        '{"considered_techniques": ["a", "b", "c"], "chain_outline": "...", '
        '"chosen_mechanism": "model chosen", '
        '"semantic_fingerprint": "chosen-flow", '
        '"diversity_rationale": "model explains why this differs", '
        '"scenario_seed": "..."}\n'
    )
    parsed = _safe_parse(stdout)
    assert parsed is not None
    assert parsed["considered_techniques"] == ["a", "b", "c"]
