"""Postgres-backed tests for DesignTaskPlanningService end-to-end flow."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from domain.design_task_validators import DesignTaskValidationError
from persistence.models import design_tasks as dt_model
from persistence.models import research as model
from persistence.repositories import DesignTaskRepository
from persistence.session import SessionFactory
from services import DesignTaskPlanningService, ResearchJobService
from services import design_task_planning_service as planning_module
from services.design_task_planning_service import validate_finding_provenance

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def session_factory() -> SessionFactory:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        yield SessionFactory(engine)
    finally:
        engine.dispose()
        subprocess.run(
            ["uv", "run", "alembic", "downgrade", "base"],
            cwd=ROOT,
            env=env,
            check=False,
        )


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory):
    with session_factory() as session:
        session.execute(sa.delete(dt_model.DesignTask))
        session.execute(sa.delete(model.ResearchFindingSource))
        session.execute(sa.delete(model.ResearchFinding))
        session.execute(sa.delete(model.ResearchSource))
        session.execute(sa.delete(model.HermesProfileBinding))
        session.execute(sa.delete(model.ResearchRun))
        session.execute(sa.delete(model.GenerationRequest))
        session.execute(
            sa.delete(model.ChallengeCategory).where(
                model.ChallengeCategory.code.not_in(["web", "pwn", "re"])
            )
        )
        session.add(
            model.HermesProfileBinding(
                role="research",
                profile_name="default",
                description="默认绑定，operator 可改",
                status="enabled",
            )
        )
        session.commit()
    yield


def _seed(
    session_factory: SessionFactory,
    *,
    target_count: int = 3,
    distribution=None,
    category: str = "web",
    finished: bool = True,
    finding_labels: list[str] | None = None,
    technique_families: list[str | None] | None = None,
):
    distribution = distribution or {"easy": 1, "medium": 2}
    finding_labels = finding_labels or ["technique-0", "technique-1"]
    technique_families = technique_families or [None] * len(finding_labels)
    service = ResearchJobService(session_factory)
    request, run = service.submit_request(
        category=category,
        topic="SQL injection",
        target_count=target_count,
        difficulty_distribution=distribution,
    )
    session = session_factory()
    try:
        for index in range(2):
            session.add(
                model.ResearchSource(
                    id=uuid4(),
                    research_run_id=run.id,
                    url=f"https://example.com/{index}",
                    title=f"Source {index}",
                    summary=f"summary {index}",
                    content_hash=f"hash-{index}",
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        for index, label in enumerate(finding_labels):
            session.add(
                model.ResearchFinding(
                    id=uuid4(),
                    research_run_id=run.id,
                    kind="technique",
                    label=label,
                    summary=f"summary {index}",
                    technique_family=technique_families[index]
                    if index < len(technique_families)
                    else None,
                )
            )
        if finished:
            run_row = session.get(model.ResearchRun, run.id)
            run_row.status = "completed"
            run_row.finished_at = datetime.now(timezone.utc)
            request_row = session.get(model.GenerationRequest, request.id)
            request_row.status = "researched"
        session.commit()
    finally:
        session.close()
    return request, run


def test_generate_creates_target_count_drafts(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=3, distribution={"easy": 1, "medium": 2})
    service = DesignTaskPlanningService(session_factory)

    tasks = service.generate_for_request(request.id)

    assert len(tasks) == 3
    assert {t.status for t in tasks} == {"draft"}
    assert sorted(t.task_no for t in tasks) == [1, 2, 3]
    assert sorted(t.difficulty for t in tasks) == ["easy", "medium", "medium"]
    assert all(t.generation_request_id == request.id for t in tasks)
    assert all(t.challenge_id.startswith("web-") for t in tasks)
    assert all(t.finding_ids for t in tasks)


def test_generated_challenge_ids_are_distinct_across_requests(
    session_factory: SessionFactory,
):
    first_request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
    )
    second_request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
    )
    service = DesignTaskPlanningService(session_factory)

    first = service.generate_for_request(first_request.id)
    second = service.generate_for_request(second_request.id)

    assert first[0].task_no == second[0].task_no == 1
    assert first[0].challenge_id == f"web-{first_request.id.hex[:8]}-0001"
    assert second[0].challenge_id == f"web-{second_request.id.hex[:8]}-0001"
    assert first[0].challenge_id != second[0].challenge_id


def test_generate_rejected_when_no_completed_run(session_factory: SessionFactory):
    request, _ = _seed(session_factory, finished=False)
    service = DesignTaskPlanningService(session_factory)

    with pytest.raises(DesignTaskValidationError, match="latest_run_not_completed"):
        service.generate_for_request(request.id)

    session = session_factory()
    try:
        rows = DesignTaskRepository(session).list_design_tasks(request.id)
        assert rows == []
    finally:
        session.close()


def test_difficulty_distribution_is_preserved(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=3, distribution={"easy": 1, "medium": 2})
    service = DesignTaskPlanningService(session_factory)
    tasks = service.generate_for_request(request.id)
    easy = [t for t in tasks if t.difficulty == "easy"]
    medium = [t for t in tasks if t.difficulty == "medium"]
    assert len(easy) == 1
    assert len(medium) == 2


def test_generate_can_replace_draft_tasks(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)
    first = service.generate_for_request(request.id)
    second = service.generate_for_request(request.id)
    assert {t.id for t in first}.isdisjoint({t.id for t in second})
    session = session_factory()
    try:
        rows = DesignTaskRepository(session).list_design_tasks(request.id)
        assert {t.id for t in rows} == {t.id for t in second}
    finally:
        session.close()


def test_generate_blocked_when_any_task_queued(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)
    service.generate_for_request(request.id)
    initial = service.approve_plan(request.id)

    session = session_factory()
    try:
        DesignTaskRepository(session).set_design_task_status(initial[0].id, "queued")
        session.commit()
    finally:
        session.close()

    with pytest.raises(DesignTaskValidationError, match="cannot regenerate"):
        service.generate_for_request(request.id)


def test_unreviewed_draft_cannot_queue_then_approve_allows_queue(
    session_factory: SessionFactory,
):
    request, _ = _seed(
        session_factory,
        target_count=2,
        distribution={"easy": 1, "medium": 1},
        finding_labels=["blind SQLi", "DOM XSS"],
    )
    service = DesignTaskPlanningService(session_factory)
    tasks = service.generate_for_request(request.id)

    session = session_factory()
    try:
        with pytest.raises(DesignTaskValidationError, match="plan_not_reviewed"):
            DesignTaskRepository(session).set_design_task_status(tasks[0].id, "queued")
        session.rollback()
    finally:
        session.close()

    approved = service.approve_plan(request.id)
    assert all(task.plan_reviewed_at is not None for task in approved)

    session = session_factory()
    try:
        queued = DesignTaskRepository(session).set_design_task_status(approved[0].id, "queued")
        session.commit()
    finally:
        session.close()

    assert queued.status == "queued"


def test_regenerate_plan_clears_prior_approval(session_factory: SessionFactory):
    request, _ = _seed(
        session_factory,
        target_count=2,
        distribution={"easy": 1, "medium": 1},
        finding_labels=["blind SQLi", "DOM XSS"],
    )
    service = DesignTaskPlanningService(session_factory)
    service.generate_for_request(request.id)
    approved = service.approve_plan(request.id)
    assert all(task.plan_reviewed_at is not None for task in approved)

    regenerated = service.regenerate_plan(request.id)

    assert all(task.plan_reviewed_at is None for task in regenerated)
    assert {task.id for task in approved}.isdisjoint({task.id for task in regenerated})


def test_approve_plan_is_idempotent_refresh(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=1, distribution={"easy": 1})
    service = DesignTaskPlanningService(session_factory)
    service.generate_for_request(request.id)

    first = service.approve_plan(request.id)
    second = service.approve_plan(request.id)

    assert [task.id for task in second] == [task.id for task in first]
    assert all(task.plan_reviewed_at is not None for task in second)


def test_legacy_draft_without_diversity_flags_is_queue_exempt(
    session_factory: SessionFactory,
):
    request, _ = _seed(session_factory, target_count=1, distribution={"easy": 1})
    service = DesignTaskPlanningService(session_factory)
    [task] = service.generate_for_request(request.id)

    session = session_factory()
    try:
        row = session.get(dt_model.DesignTask, task.id)
        row.diversity_flags = None
        row.plan_reviewed_at = None
        session.flush()
        queued = DesignTaskRepository(session).set_design_task_status(task.id, "queued")
        session.commit()
    finally:
        session.close()

    assert queued.status == "queued"
    assert queued.diversity_flags is None


def test_regenerate_task_clean_pool_replaces_slot(session_factory: SessionFactory):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi", "DOM XSS"],
    )
    service = DesignTaskPlanningService(session_factory)
    [initial] = service.generate_for_request(request.id)

    result = service.regenerate_task(request.id, 1)

    assert result["outcome"] == "regenerated"
    assert result["task"].primary_technique != initial.primary_technique
    assert result["task"].plan_reviewed_at is None


def test_regenerate_task_family_saturation_warns_not_noops(
    session_factory: SessionFactory,
):
    request, _ = _seed(
        session_factory,
        target_count=2,
        distribution={"easy": 2},
        finding_labels=["blind SQLi", "DOM XSS", "second-order SQLi"],
    )
    service = DesignTaskPlanningService(session_factory)
    tasks = service.generate_for_request(request.id)
    assert [task.primary_technique for task in tasks[:2]] == ["blind SQLi", "DOM XSS"]

    result = service.regenerate_task(request.id, 2)

    assert result["outcome"] == "regenerated_with_warning"
    assert "family_quota_exceeded" in result["task"].diversity_flags["warnings"]
    assert result["task"].primary_technique == "second-order SQLi"


def test_regenerate_task_only_sibling_duplicates_noops(
    session_factory: SessionFactory,
):
    request, _ = _seed(
        session_factory,
        target_count=2,
        distribution={"easy": 2},
        finding_labels=["blind SQLi", "DOM XSS"],
    )
    service = DesignTaskPlanningService(session_factory)
    tasks = service.generate_for_request(request.id)

    result = service.regenerate_task(request.id, 1)

    assert result["outcome"] == "no_alternative"
    assert result["reason"] == "subtechnique_exhausted"
    assert result["task"].id == tasks[0].id
    assert result["task"].primary_technique == tasks[0].primary_technique


def test_regenerate_task_without_distinct_finding_noops(
    session_factory: SessionFactory,
):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi"],
    )
    service = DesignTaskPlanningService(session_factory)
    [task] = service.generate_for_request(request.id)

    result = service.regenerate_task(request.id, 1)

    assert result["outcome"] == "no_alternative"
    assert result["reason"] == "research_diversity_insufficient"
    assert result["task"].id == task.id


def test_regenerate_task_blocked_once_any_task_queued(session_factory: SessionFactory):
    request, _ = _seed(
        session_factory,
        target_count=2,
        distribution={"easy": 2},
        finding_labels=["blind SQLi", "DOM XSS"],
    )
    service = DesignTaskPlanningService(session_factory)
    service.generate_for_request(request.id)
    tasks = service.approve_plan(request.id)

    session = session_factory()
    try:
        DesignTaskRepository(session).set_design_task_status(tasks[0].id, "queued")
        session.commit()
    finally:
        session.close()

    with pytest.raises(DesignTaskValidationError, match="cannot regenerate"):
        service.regenerate_task(request.id, 2)


def test_validate_finding_provenance_rejects_empty_finding_ids():
    allowed = {uuid4()}
    candidates = [
        {"task_no": 1, "finding_ids": []},
    ]
    with pytest.raises(DesignTaskValidationError, match="cites no finding"):
        validate_finding_provenance(
            candidates, allowed_finding_ids=allowed, research_run_id=uuid4()
        )


def test_validate_finding_provenance_rejects_foreign_finding_id():
    allowed_finding = uuid4()
    foreign_finding = uuid4()
    candidates = [
        {"task_no": 1, "finding_ids": [foreign_finding]},
    ]
    with pytest.raises(DesignTaskValidationError, match="not from research run"):
        validate_finding_provenance(
            candidates,
            allowed_finding_ids={allowed_finding},
            research_run_id=uuid4(),
        )


def test_validate_finding_provenance_accepts_subset_from_run():
    a, b = uuid4(), uuid4()
    candidates = [
        {"task_no": 1, "finding_ids": [a]},
        {"task_no": 2, "finding_ids": [str(b)]},
    ]
    validate_finding_provenance(
        candidates, allowed_finding_ids={a, b}, research_run_id=uuid4()
    )


def test_generate_rejects_planner_with_empty_finding_ids(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)

    original = planning_module._plan_candidates

    def _bad_planner(req, run, findings, *, hermes_planner=None):
        rows = original(req, run, findings, hermes_planner=hermes_planner)
        for row in rows:
            row["finding_ids"] = []
        return rows

    monkeypatch.setattr(planning_module, "_plan_candidates", _bad_planner)

    with pytest.raises(DesignTaskValidationError, match="cites no finding"):
        service.generate_for_request(request.id)

    session = session_factory()
    try:
        rows = DesignTaskRepository(session).list_design_tasks(request.id)
        assert rows == []
    finally:
        session.close()


def test_generate_rejects_planner_with_foreign_finding_id(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)

    original = planning_module._plan_candidates
    foreign = uuid4()

    def _bad_planner(req, run, findings, *, hermes_planner=None):
        rows = original(req, run, findings, hermes_planner=hermes_planner)
        rows[0]["finding_ids"] = [foreign]
        return rows

    monkeypatch.setattr(planning_module, "_plan_candidates", _bad_planner)

    with pytest.raises(DesignTaskValidationError, match="not from research run"):
        service.generate_for_request(request.id)

    session = session_factory()
    try:
        rows = DesignTaskRepository(session).list_design_tasks(request.id)
        assert rows == []
    finally:
        session.close()


def _fake_finding(label: str) -> model.ResearchFinding:
    """Domain DTO doppelganger for unit tests that bypass the DB."""
    from domain.research import ResearchFinding

    return ResearchFinding(
        id=uuid4(),
        research_run_id=uuid4(),
        kind="technique",
        label=label,
        summary=f"summary about {label}",
    )


def _fake_request(difficulty_distribution):
    from types import MappingProxyType

    from domain.research import GenerationRequest

    return GenerationRequest(
        id=uuid4(),
        category="web",
        topic="JWT key confusion",
        target_count=sum(difficulty_distribution.values()),
        difficulty_distribution=MappingProxyType(dict(difficulty_distribution)),
        runtime_constraints=MappingProxyType({}),
        seed_urls=(),
        max_attempts=3,
        status="researched",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _fake_run():
    from domain.research import ResearchRun

    now = datetime.now(timezone.utc)
    return ResearchRun(
        id=uuid4(),
        generation_request_id=uuid4(),
        parent_run_id=None,
        attempt=1,
        status="completed",
        claimed_by=None,
        claim_token=None,
        claimed_at=None,
        heartbeat_at=None,
        lease_expires_at=None,
        started_at=None,
        finished_at=now,
        last_error=None,
        hermes_log_path=None,
        profile_name_used=None,
        created_at=now,
    )


def test_plan_candidates_assigns_multiple_findings_for_hard_and_expert():
    # Hard pulls 2 findings, expert pulls 3 — the difficulty rubric
    # expects ≥3 distinct techniques for hard and a chain for expert.
    findings = [_fake_finding(f"technique-{i}") for i in range(4)]
    request = _fake_request({"easy": 1, "medium": 1, "hard": 1, "expert": 1})
    run = _fake_run()

    candidates = planning_module._plan_candidates(request, run, findings)

    by_difficulty = {c["difficulty"]: c for c in candidates}
    assert len(by_difficulty["easy"]["finding_ids"]) == 1
    assert len(by_difficulty["medium"]["finding_ids"]) == 1
    assert len(by_difficulty["hard"]["finding_ids"]) == 2
    assert len(by_difficulty["expert"]["finding_ids"]) == 3


def test_plan_candidates_scenario_template_differs_by_difficulty():
    findings = [_fake_finding(f"technique-{i}") for i in range(4)]
    request = _fake_request({"easy": 1, "medium": 1, "hard": 1, "expert": 1})
    run = _fake_run()

    candidates = planning_module._plan_candidates(request, run, findings)
    scenarios = {c["difficulty"]: c["scenario"] for c in candidates}

    # easy is intentionally a "toy service" line.
    assert "Standalone" in scenarios["easy"]
    # medium-and-up must read like a business context for the rubric.
    assert "business" in scenarios["medium"].lower()
    assert "chain" in scenarios["hard"].lower()
    # expert template names the novelty contract explicitly so the
    # downstream Hermes design call cannot forget to set it.
    assert "novelty" in scenarios["expert"].lower()


def test_plan_candidates_applies_hermes_planner_enrichment_for_hard():
    # D5=b: when a Hermes planner is injected, hard tasks get its
    # scenario seed and the candidate is tagged with `_planner_source`.
    from services.design_planner_hermes import PlannerEnrichment

    class _StubPlanner:
        def __init__(self):
            self.calls = []

        def plan(self, **kw):
            self.calls.append(kw)
            return PlannerEnrichment(
                considered_techniques=["A", "B", "C"],
                chain_outline="A → B → C reach flag.",
                scenario_seed="Internal supervisor approval portal.",
                novelty_seed=None,
                raw_response="{...}",
            )

    findings = [_fake_finding(f"technique-{i}") for i in range(4)]
    request = _fake_request({"easy": 1, "hard": 1})
    run = _fake_run()
    planner = _StubPlanner()

    candidates = planning_module._plan_candidates(
        request, run, findings, hermes_planner=planner
    )

    # Only the hard task triggered the Hermes call.
    assert len(planner.calls) == 1
    assert planner.calls[0]["difficulty"] == "hard"
    assert set(planner.calls[0]["avoid_techniques"]) == {
        candidates[0]["diversity_flags"]["sub_technique"]
    }

    hard = next(c for c in candidates if c["difficulty"] == "hard")
    assert hard["scenario"] == "Internal supervisor approval portal."
    assert hard["constraints"]["_planner_source"] == "hermes"
    assert hard["constraints"]["_planner_techniques"] == ["A", "B", "C"]

    easy = next(c for c in candidates if c["difficulty"] == "easy")
    # Easy task untouched by the planner.
    assert "_planner_source" not in easy["constraints"]


def test_plan_candidates_falls_back_when_planner_returns_none():
    class _NonePlanner:
        def plan(self, **kw):
            return None

    findings = [_fake_finding(f"technique-{i}") for i in range(4)]
    request = _fake_request({"hard": 1})
    run = _fake_run()

    candidates = planning_module._plan_candidates(
        request, run, findings, hermes_planner=_NonePlanner()
    )

    hard = candidates[0]
    # Template scenario survives, fallback tag set.
    assert hard["scenario"].lower().startswith("multi-stage")
    assert hard["constraints"]["_planner_source"] == "template_fallback"


def test_plan_candidates_reuses_findings_when_pool_too_small_for_expert():
    # Expert wants 3 distinct findings; the planner falls back to a
    # stable wrap-around when fewer are available rather than failing.
    findings = [_fake_finding(f"technique-{i}") for i in range(2)]
    request = _fake_request({"expert": 1})
    run = _fake_run()

    candidates = planning_module._plan_candidates(request, run, findings)

    assert len(candidates[0]["finding_ids"]) == 3
    assert len(set(candidates[0]["finding_ids"])) <= 2  # wrap-around expected


def test_plan_candidates_flags_monocultural_pool_but_preserves_count():
    findings = [_fake_finding(label) for label in ("xor", "XOR", "xor-decrypt")]
    request = _fake_request({"easy": 3})
    run = _fake_run()

    candidates = planning_module._plan_candidates(request, run, findings)

    assert len(candidates) == 3
    assert all(
        c["diversity_flags"]["sub_technique"] == "xor"
        for c in candidates
    )
    assert all(
        "subtechnique_duplicate" in c["diversity_flags"]["warnings"]
        for c in candidates
    )


def test_plan_candidates_diverse_pool_has_no_diversity_warnings():
    findings = [
        _fake_finding("blind SQLi"),
        _fake_finding("DOM XSS"),
        _fake_finding("JWT confusion"),
    ]
    request = _fake_request({"easy": 3})
    run = _fake_run()

    candidates = planning_module._plan_candidates(request, run, findings)

    assert [c["diversity_flags"]["warnings"] for c in candidates] == [[], [], []]
    assert [c["diversity_flags"]["sub_technique"] for c in candidates] == [
        "blind sqli",
        "dom xss",
        "jwt confusion",
    ]


def test_plan_candidates_same_family_distinct_subtechniques_only_flags_family_quota():
    findings = [
        _fake_finding("blind SQLi"),
        _fake_finding("second-order SQLi"),
        _fake_finding("SQLi login bypass"),
    ]
    request = _fake_request({"easy": 3})
    run = _fake_run()

    candidates = planning_module._plan_candidates(request, run, findings)

    assert all(c["diversity_flags"]["family"] == "injection" for c in candidates)
    assert all(
        "subtechnique_duplicate" not in c["diversity_flags"]["warnings"]
        for c in candidates
    )
    assert any(
        "family_quota_exceeded" in c["diversity_flags"]["warnings"]
        for c in candidates
    )


def test_plan_candidates_is_deterministic_for_diversity_flags():
    findings = [
        _fake_finding("blind SQLi"),
        _fake_finding("DOM XSS"),
        _fake_finding("JWT confusion"),
        _fake_finding("SSRF"),
    ]
    request = _fake_request({"easy": 2, "medium": 2})
    run = _fake_run()

    first = planning_module._plan_candidates(request, run, findings)
    second = planning_module._plan_candidates(request, run, findings)

    assert [
        (c["task_no"], c["primary_technique"], c["diversity_flags"])
        for c in first
    ] == [
        (c["task_no"], c["primary_technique"], c["diversity_flags"])
        for c in second
    ]


def test_generate_replaces_archived_tasks(session_factory: SessionFactory):
    request, _ = _seed(session_factory, target_count=2, distribution={"easy": 1, "medium": 1})
    service = DesignTaskPlanningService(session_factory)
    first = service.generate_for_request(request.id)

    session = session_factory()
    try:
        repo = DesignTaskRepository(session)
        for task in first:
            repo.set_design_task_status(task.id, "archived")
        session.commit()
    finally:
        session.close()

    second = service.generate_for_request(request.id)
    assert {t.status for t in second} == {"draft"}
    assert {t.id for t in second}.isdisjoint({t.id for t in first})


def test_allocate_core_mechanisms_rotates_round_robin():
    mechs = planning_module._allocate_core_mechanisms("re", 10)
    catalog = planning_module._DEFAULT_MECHANISMS["re"]
    # 10 tasks over a 10-item catalog → every mechanism used exactly once,
    # so XOR is 1/10, not the whole batch.
    assert len(mechs) == 10
    assert set(mechs) == set(catalog)
    assert mechs.count("xor_keystream") == 1
    # no adjacent repeats
    assert all(a != b for a, b in zip(mechs, mechs[1:]))


def test_allocate_core_mechanisms_spreads_when_more_tasks_than_catalog():
    catalog = planning_module._DEFAULT_MECHANISMS["re"]
    mechs = planning_module._allocate_core_mechanisms("re", len(catalog) + 3)
    # even spread: max count - min count <= 1
    counts = {m: mechs.count(m) for m in catalog}
    assert max(counts.values()) - min(counts.values()) <= 1


def test_mechanisms_for_category_honors_profile_override(monkeypatch):
    monkeypatch.setattr(
        planning_module,
        "_generation_profile_category",
        lambda category: {"mechanisms": ["custom_a", "custom_b"]},
    )
    assert planning_module._mechanisms_for_category("re") == ("custom_a", "custom_b")


def test_mechanisms_for_category_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(
        planning_module, "_generation_profile_category", lambda category: {}
    )
    assert planning_module._mechanisms_for_category("re") == (
        planning_module._DEFAULT_MECHANISMS["re"]
    )
