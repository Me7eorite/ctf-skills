"""Postgres-backed tests for DesignTaskPlanningService end-to-end flow."""

from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from domain.design_task_validators import DesignTaskValidationError
from persistence.models import build_attempts as build_model
from persistence.models import challenge_designs as cd_model
from persistence.models import design_profile_reservations as reservation_model
from persistence.models import design_tasks as dt_model
from persistence.models import research as model
from persistence.repositories import (
    BuildAttemptsRepository,
    DesignEvidenceRepository,
    DesignProfileReservationRepository,
    DesignTaskRepository,
)
from persistence.session import SessionFactory
from services import DesignTaskPlanningService, ResearchJobService
from services import design_task_planning_service as planning_module
from services.design_task_planning_service import (
    DesignTaskGenerationPersistenceError,
    _semantic_assignments_for_findings,
    validate_finding_provenance,
)

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
        session.execute(sa.delete(reservation_model.DesignProfileReservation))
        session.execute(sa.delete(reservation_model.DesignProfileLedger))
        session.execute(sa.delete(cd_model.DesignEvidence))
        session.execute(sa.delete(cd_model.DesignDifficultyReview))
        session.execute(sa.delete(cd_model.ChallengeDesign))
        session.execute(sa.delete(cd_model.DesignAttempt))
        session.execute(sa.delete(build_model.BuildAttempt))
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
        binding = session.get(model.HermesProfileBinding, "research")
        if binding is None:
            session.add(
                model.HermesProfileBinding(
                    role="research",
                    profile_name="default",
                    description="默认绑定，operator 可改",
                    status="enabled",
                )
            )
        else:
            binding.profile_name = "default"
            binding.description = "默认绑定，operator 可改"
            binding.status = "enabled"
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
    finding_labels = finding_labels or ["blind SQLi", "DOM XSS", "SSRF"]
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


def test_generate_uses_distinct_finding_semantics_after_successful_research(
    session_factory: SessionFactory,
):
    request, _ = _seed(
        session_factory,
        target_count=2,
        distribution={"easy": 2},
        finding_labels=["blind SQLi", "DOM XSS", "SSRF"],
    )
    service = DesignTaskPlanningService(session_factory)

    tasks = service.generate_for_request(request.id)

    assert len(tasks) == 2
    assert {t.primary_technique for t in tasks} == {"blind SQLi", "DOM XSS"}
    assert {t.diversity_flags["family"] for t in tasks} >= {"injection", "client_side"}


def test_pwn_bss_variable_write_gets_matching_reservation_profile(
    session_factory: SessionFactory,
):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        category="pwn",
        finding_labels=["BSS variable modification"],
    )
    service = DesignTaskPlanningService(session_factory)

    [task] = service.generate_for_request(request.id)

    assert task.primary_technique == "BSS variable modification"
    assert task.diversity_flags["family"] == "integer_oob"
    assert task.diversity_flags["sub_technique"] == "global_bss_write"
    assert task.current_reservation_id is not None
    with session_factory() as session:
        reservation = DesignProfileReservationRepository(session).get(
            task.current_reservation_id
        )
        assert reservation is not None
        assert reservation.profile["semantic"] == {
            "family": "integer_oob",
            "sub_technique": "global_bss_write",
        }
        assert reservation.profile["solve"]["required_action"] == "write_what_where"
        assert reservation.profile["solve"]["chain_shape"] == "global-write-win"


def test_pwn_heap_findings_map_to_supported_reservation_profile(
    session_factory: SessionFactory,
):
    request, _ = _seed(
        session_factory,
        target_count=3,
        distribution={"easy": 3},
        category="pwn",
        finding_labels=["glibc heap", "use after free", "tcache poisoning"],
    )
    service = DesignTaskPlanningService(session_factory)

    tasks = service.generate_for_request(request.id)

    assert len(tasks) == 3
    assert {task.diversity_flags["sub_technique"] for task in tasks} == {
        "heap_uaf_tcache"
    }
    assert all(task.current_reservation_id is not None for task in tasks)


def test_pwn_unsupported_profile_is_rejected_explicitly(
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        category="pwn",
        finding_labels=["rainbow table"],
    )
    monkeypatch.setattr(
        planning_module,
        "resolve_sub_technique",
        lambda _finding: "rainbow table",
    )
    service = DesignTaskPlanningService(session_factory)

    with pytest.raises(DesignTaskValidationError, match="unsupported_pwn_profile"):
        service.generate_for_request(request.id)


def test_semantic_assignments_use_real_findings() -> None:
    findings = [
        {"kind": "technique", "label": "64-bit stack offset determination"},
        {"kind": "technique", "label": "GOT overwrite with %n"},
        {"kind": "technique", "label": "Format string with stack pivot"},
    ]

    assignments = _semantic_assignments_for_findings("pwn", findings)

    assert {item["family"] for item in assignments} == {"format_string"}
    assert [item["sub_technique"] for item in assignments] == [
        "64_bit_stack_offset_determination",
        "got_overwrite_with_n",
        "format_string_with_stack_pivot",
    ]
    assert len({item["sub_technique"] for item in assignments}) == 3
    assert {item["sub_technique"] for item in assignments} != {"format_string_got"}


def test_generate_reservations_preserve_task_semantic_diversity(
    session_factory: SessionFactory,
):
    request, _ = _seed(
        session_factory,
        target_count=5,
        distribution={"easy": 5},
        category="pwn",
        finding_labels=[
            "64-bit stack offset determination",
            "GOT overwrite with %n",
            "Format string with stack pivot",
            "stack canary leak",
            "byte by byte leak",
        ],
    )
    service = DesignTaskPlanningService(session_factory)

    tasks = service.generate_for_request(request.id)

    assert len(tasks) == 5
    assert all(task.current_reservation_id is not None for task in tasks)
    with session_factory() as session:
        reservations = list(
            session.scalars(
                sa.select(reservation_model.DesignProfileReservation)
                .where(
                    reservation_model.DesignProfileReservation.generation_request_id
                    == request.id,
                    reservation_model.DesignProfileReservation.state == "reserved",
                )
                .order_by(reservation_model.DesignProfileReservation.created_at)
            )
        )
    subtechniques = [
        str(row.profile["semantic"]["sub_technique"]) for row in reservations
    ]
    assert len(reservations) == 5
    assert len(set(subtechniques)) > 1
    assert set(subtechniques) != {"format_string_got"}


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


def test_generate_uses_latest_completed_run_when_newer_run_failed(
    session_factory: SessionFactory,
):
    request, completed_run = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
    )
    with session_factory() as session:
        session.add(
            model.ResearchRun(
                id=uuid4(),
                generation_request_id=request.id,
                parent_run_id=completed_run.id,
                attempt=completed_run.attempt + 1,
                status="failed",
                finished_at=datetime.now(timezone.utc),
                last_error="design_diversity_exhausted",
            )
        )
        session.commit()
    service = DesignTaskPlanningService(session_factory)

    tasks = service.generate_for_request(request.id)

    assert len(tasks) == 1
    assert tasks[0].research_run_id == completed_run.id


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


def test_unreviewed_draft_queues_and_can_still_be_approved(
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
        queued = DesignTaskRepository(session).set_design_task_status(tasks[0].id, "queued")
        session.commit()
    finally:
        session.close()

    assert queued.status == "queued"
    assert queued.plan_reviewed_at is not None


def test_queue_auto_reviews_unreviewed_draft_plan(
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

    session = session_factory()
    try:
        queued = DesignTaskRepository(session).set_design_task_status(task.id, "queued")
        session.commit()
    finally:
        session.close()

    assert queued.status == "queued"
    assert queued.plan_reviewed_at is not None


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
        finding_labels=["blind SQLi", "DOM XSS", "SSTI template escape"],
    )
    service = DesignTaskPlanningService(session_factory)
    tasks = service.generate_for_request(request.id)
    assert [task.primary_technique for task in tasks[:2]] == ["blind SQLi", "DOM XSS"]

    result = service.regenerate_task(request.id, 2)

    assert result["outcome"] == "regenerated_with_warning"
    assert "family_quota_exceeded" in result["task"].diversity_flags["warnings"]
    assert result["task"].primary_technique == "SSTI template escape"


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


def test_generate_records_current_reservation(session_factory: SessionFactory):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi"],
    )
    service = DesignTaskPlanningService(session_factory)

    tasks = service.generate_for_request(request.id)

    with session_factory() as session:
        task_row = session.get(dt_model.DesignTask, tasks[0].id)
        reservation_row = session.get(
            reservation_model.DesignProfileReservation,
            task_row.current_reservation_id,
        )
        assert task_row.current_reservation_id is not None
        assert reservation_row is not None
        assert reservation_row.design_task_id == task_row.id
        assert reservation_row.state == "reserved"


def test_generate_returns_conflict_when_request_lock_is_busy(
    session_factory: SessionFactory,
    monkeypatch,
):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi"],
    )
    service = DesignTaskPlanningService(session_factory)

    def _busy_lock(*_args, **_kwargs):
        original = Exception("lock not available")
        original.pgcode = "55P03"  # type: ignore[attr-defined]
        raise DBAPIError("SELECT ... FOR UPDATE", {}, original)

    monkeypatch.setattr(
        planning_module.ResearchRepository,
        "lock_generation_request",
        _busy_lock,
    )

    with pytest.raises(DesignTaskValidationError, match="generation request is busy"):
        service.generate_for_request(request.id)


def test_generate_converts_design_task_insert_dbapi_error(
    session_factory: SessionFactory,
    monkeypatch,
):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi"],
    )
    service = DesignTaskPlanningService(session_factory)

    def _disk_full_insert(*_args, **_kwargs):
        original = Exception("No space left on device")
        raise DBAPIError("INSERT INTO design_tasks", {}, original)

    monkeypatch.setattr(
        planning_module.DesignTaskRepository,
        "replace_draft_or_archived_tasks",
        _disk_full_insert,
    )

    with pytest.raises(DesignTaskGenerationPersistenceError) as exc_info:
        service.generate_for_request(request.id)

    exc = exc_info.value
    assert exc.code == "design_task_persistence_failed"
    assert exc.stage == "design_task_insert"
    assert exc.request_id == request.id
    assert exc.retryable is True


def test_generate_converts_commit_dbapi_error(session_factory: SessionFactory):
    request, completed_run = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi"],
    )

    class CommitFailingSession:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def commit(self):
            original = Exception("No space left on device")
            raise DBAPIError("COMMIT", {}, original)

        def close(self):
            self._wrapped.close()

    def failing_factory():
        return CommitFailingSession(session_factory())

    service = DesignTaskPlanningService(failing_factory)

    with pytest.raises(DesignTaskGenerationPersistenceError) as exc_info:
        service.generate_for_request(request.id)

    exc = exc_info.value
    assert exc.code == "design_task_persistence_failed"
    assert exc.stage == "design_task_commit"
    assert exc.request_id == request.id
    assert exc.retryable is True
    assert exc.__cause__ is not None
    assert completed_run.id


def test_concurrent_generate_fails_fast_with_busy_code(session_factory: SessionFactory):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi"],
    )
    service = DesignTaskPlanningService(session_factory)
    lock_ready = threading.Event()
    release_lock = threading.Event()
    outcome = {}

    def hold_request_lock():
        with session_factory() as session:
            repo = planning_module.ResearchRepository(session)
            repo.lock_generation_request(request.id)
            lock_ready.set()
            release_lock.wait(timeout=10)
            session.rollback()

    def try_generate():
        lock_ready.wait(timeout=10)
        started = datetime.now(timezone.utc)
        try:
            service.generate_for_request(request.id)
            outcome["result"] = "success"
        except DesignTaskValidationError as exc:
            outcome["result"] = "error"
            outcome["code"] = exc.code
            outcome["message"] = str(exc)
        outcome["elapsed"] = (datetime.now(timezone.utc) - started).total_seconds()

    holder = threading.Thread(target=hold_request_lock)
    worker = threading.Thread(target=try_generate)
    holder.start()
    worker.start()
    assert lock_ready.wait(timeout=10)
    worker.join(timeout=10)
    release_lock.set()
    holder.join(timeout=10)

    assert outcome["result"] == "error"
    assert outcome["code"] == "generation_request_busy"
    assert outcome["elapsed"] < 2


def test_regenerate_releases_replaced_reservations(session_factory: SessionFactory):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi"],
    )
    service = DesignTaskPlanningService(session_factory)

    first_tasks = service.generate_for_request(request.id)
    first_task_id = first_tasks[0].id
    first_reservation_id = first_tasks[0].current_reservation_id
    second_tasks = service.generate_for_request(request.id)

    with session_factory() as session:
        first_task = session.get(dt_model.DesignTask, first_task_id)
        first_reservation = session.get(
            reservation_model.DesignProfileReservation,
            first_reservation_id,
        )
        second_task = session.get(dt_model.DesignTask, second_tasks[0].id)
        active_reservations = session.scalars(
            sa.select(reservation_model.DesignProfileReservation).where(
                reservation_model.DesignProfileReservation.generation_request_id == request.id,
                reservation_model.DesignProfileReservation.state.in_(("reserved", "committed")),
            )
        ).all()

        assert first_task is None
        assert first_reservation is not None
        assert first_reservation.state == "released"
        assert first_reservation.design_task_id is None
        assert second_task.current_reservation_id == second_tasks[0].current_reservation_id
        assert [row.id for row in active_reservations] == [second_task.current_reservation_id]


def _governed_profile() -> dict[str, object]:
    return {
        "semantic": {"family": "injection", "sub_technique": "sqli"},
        "solve": {
            "analysis_mode": "blackbox",
            "required_action": "payload_injection",
            "chain_shape": "inject-exfiltrate",
            "required_tool_class": "http_client",
        },
        "implementation": {
            "artifact_format": "container",
            "language": "python",
            "runtime": "flask",
            "interaction": "http_form",
            "control_structure": "route_handler",
            "flag_concealment": "database_record",
        },
        "presentation": {
            "scenario_type": "reporting_app",
            "input_model": "web_form",
        },
    }


def _build_contract(profile: dict[str, object]) -> dict[str, object]:
    return {
        "artifact_ids": ["primary"],
        "fixture_ids": ["admin-password"],
        "required_profile": profile,
        "required_player_actions": ["payload_injection"],
        "required_components": ["web-service"],
        "required_asset_flow": [
            {
                "stage_id": "recover-password",
                "produced_asset_or_capability": "admin password",
                "verification_harness": {
                    "test_kind": "fixture_assertion",
                    "fixture_ref": "admin-password",
                    "assertion": "non_empty",
                },
                "dependency_harness": {
                    "test_kind": "solver_without_fixture",
                    "fixture_ref": "admin-password",
                    "assertion": "must_fail",
                },
            }
        ],
        "forbidden_shortcuts": [
            {
                "id": "direct-run",
                "test_kind": "artifact_direct_run",
                "artifact_ref": "primary",
                "assertion": "stdout_not_contains_flag",
            }
        ],
        "acceptance_tests": [],
        "allowed_implementation_freedom": ["file_names"],
    }


def _commit_governed_design(session_factory: SessionFactory, task_id) -> dict[str, object]:
    from domain.design.profile_taxonomy import canonical_profile_signatures

    profile = _governed_profile()
    with session_factory() as session:
        task = session.get(dt_model.DesignTask, task_id)
        assert task is not None
        reservation = session.get(
            reservation_model.DesignProfileReservation,
            task.current_reservation_id,
        )
        assert reservation is not None
        profile = dict(reservation.profile)
        signatures = canonical_profile_signatures(profile, category=task.category, policy_version=1)
        reservation_repo = DesignProfileReservationRepository(session)
        attempt = cd_model.DesignAttempt(
            id=uuid4(),
            design_task_id=task.id,
            attempt=1,
            status="completed",
            claim_token=uuid4(),
            finished_at=datetime.now(timezone.utc),
            profile_name_used="default",
        )
        design = cd_model.ChallengeDesign(
            id=uuid4(),
            design_task_id=task.id,
            design_attempt_id=attempt.id,
            payload={"event": {"flag_format": "flag{...}"}, "challenges": [{"id": task.challenge_id}]},
            summary="governed design",
            flag_format="flag{...}",
            validation_notes="passed",
            quality_gate_passed=True,
            status="draft",
        )
        session.add_all([attempt, design])
        session.flush()
        reservation_repo.commit_reservation(reservation.id)
        evidence = DesignEvidenceRepository(session).create_live(
            design_task_id=task.id,
            challenge_design_id=design.id,
            research_finding_ids=[],
            profile=profile,
            profile_signature=signatures.combined_profile_signature,
            distinctness_claim="Distinct solve and implementation profile.",
            compared_challenge_ids=[],
            evidence={"claims": ["research-backed claim"]},
            build_contract=_build_contract(profile),
            ledger_version=reservation.ledger_version,
        )
        task.status = "designed"
        task.plan_reviewed_at = datetime.now(timezone.utc)
        session.commit()
        return {
            "reservation_id": reservation.id,
            "design_id": design.id,
            "attempt_id": attempt.id,
            "evidence_id": evidence.id,
        }


def test_request_design_revision_supersedes_evidence_and_returns_to_draft(
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
    ids = _commit_governed_design(session_factory, task.id)

    revised = service.request_design_revision(task.id, reason="quality failure")

    assert revised.status == "draft"
    assert revised.plan_reviewed_at is None
    assert revised.current_reservation_id is not None
    assert revised.current_reservation_id != ids["reservation_id"]
    assert revised.current_design_evidence_id is None
    with session_factory() as session:
        old_reservation = session.get(
            reservation_model.DesignProfileReservation,
            ids["reservation_id"],
        )
        old_design = session.get(cd_model.ChallengeDesign, ids["design_id"])
        old_attempt = session.get(cd_model.DesignAttempt, ids["attempt_id"])
        old_evidence = session.get(cd_model.DesignEvidence, ids["evidence_id"])
        active_reservations = session.scalars(
            sa.select(reservation_model.DesignProfileReservation).where(
                reservation_model.DesignProfileReservation.design_task_id == task.id,
                reservation_model.DesignProfileReservation.state.in_(("reserved", "committed")),
            )
        ).all()

        assert old_reservation is not None and old_reservation.state == "released"
        assert old_design is not None and old_design.status == "superseded"
        assert old_attempt is not None and old_attempt.last_error == "quality failure"
        assert old_evidence is not None
        assert old_evidence.superseded_at is not None
        assert old_evidence.supersession_reason == "quality failure"
        assert [row.id for row in active_reservations] == [revised.current_reservation_id]


def test_request_design_revision_rejects_active_build(
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
    ids = _commit_governed_design(session_factory, task.id)
    with session_factory() as session:
        BuildAttemptsRepository(session).create_attempt(task.id, f"{uuid4()}.json")
        session.commit()

    with pytest.raises(DesignTaskValidationError, match="active build attempt"):
        service.request_design_revision(task.id, reason="quality failure")

    with session_factory() as session:
        assert session.get(dt_model.DesignTask, task.id).status == "designed"
        assert session.get(cd_model.DesignEvidence, ids["evidence_id"]).superseded_at is None


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


def _fake_finding_with_kind(label: str, kind: str) -> model.ResearchFinding:
    from domain.research import ResearchFinding

    return ResearchFinding(
        id=uuid4(),
        research_run_id=uuid4(),
        kind=kind,
        label=label,
        summary=f"summary about {label}",
    )


def _fake_request(difficulty_distribution, *, category: str = "web"):
    from types import MappingProxyType

    from domain.research import GenerationRequest

    return GenerationRequest(
        id=uuid4(),
        category=category,
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
                chosen_mechanism="approval-chain token confusion",
                semantic_fingerprint="approval-token-confusion",
                diversity_rationale="Different state transition than sibling tasks.",
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
    assert any(
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
        "sqli",
        "xss",
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
    assert any(
        "subtechnique_duplicate" in c["diversity_flags"]["warnings"]
        for c in candidates
    )
    assert any(
        "family_quota_exceeded" in c["diversity_flags"]["warnings"]
        for c in candidates
    )


def test_plan_candidates_ignores_scenario_and_prerequisite_findings_for_primary_allocation():
    findings = [
        _fake_finding_with_kind("blind SQLi", "scenario"),
        _fake_finding_with_kind("DOM XSS", "prerequisite"),
        _fake_finding_with_kind("JWT confusion", "technique"),
        _fake_finding_with_kind("SSRF", "variant"),
    ]
    request = _fake_request({"easy": 2})
    run = _fake_run()

    candidates = planning_module._plan_candidates(request, run, findings)

    assert [c["primary_technique"] for c in candidates] == ["JWT confusion", "SSRF"]
    assert [c["finding_ids"][0] for c in candidates] == [findings[2].id, findings[3].id]


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



def test_plan_candidates_titles_are_short_names_not_task_labels():
    findings = [_fake_finding("heap use after free exploitation")]
    request = _fake_request({"easy": 1})
    run = _fake_run()

    [candidate] = planning_module._plan_candidates(request, run, findings)

    assert candidate["title"] == "HeapUseAfter"
    assert len(candidate["title"]) <= 15
    assert "task" not in candidate["title"].lower()

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


def test_plan_candidates_do_not_preassign_core_mechanism():
    findings = [_fake_finding("symbolic execution")]
    request = _fake_request({"easy": 1}, category="re")
    run = _fake_run()

    [candidate] = planning_module._plan_candidates(request, run, findings)
    flags = candidate["diversity_flags"]

    assert "core_mechanism" not in flags
    assert flags["chosen_mechanism"] is None
    assert flags["semantic_fingerprint"] is None
    assert "symbolic execution" not in flags["advisory_mechanism_vocabulary"]
    assert flags["advisory_mechanism_vocabulary"] == planning_module._DEFAULT_MECHANISMS["re"]


def test_mechanisms_for_category_honors_profile_override(monkeypatch):
    monkeypatch.setattr(
        planning_module,
        "_generation_profile_category",
        lambda category: {"advisory_mechanisms": ["custom_a", "custom_b"]},
    )
    assert planning_module._advisory_mechanisms_for_category("re") == ("custom_a", "custom_b")


def test_mechanisms_for_category_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(
        planning_module, "_generation_profile_category", lambda category: {}
    )
    assert planning_module._advisory_mechanisms_for_category("re") == (
        planning_module._DEFAULT_MECHANISMS["re"]
    )


def _reservation_profile(*, sub_technique: str = "blind sqli") -> dict[str, object]:
    return {
        "semantic": {"family": "injection", "sub_technique": sub_technique},
        "solve": {
            "analysis_mode": "blackbox",
            "required_action": "payload_injection",
            "chain_shape": "single-request-exploit",
            "required_tool_class": "http_client",
        },
        "implementation": {
            "artifact_format": "container",
            "language": "python",
            "runtime": "flask",
            "interaction": "http_form",
            "control_structure": "route_handler",
            "flag_concealment": "database_record",
        },
        "presentation": {
            "scenario_type": "reporting_app",
            "input_model": "web_form",
        },
    }


def test_concurrent_reservations_same_task_keep_one_active(session_factory: SessionFactory):
    request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi", "DOM XSS"],
    )
    service = DesignTaskPlanningService(session_factory)
    [task] = service.generate_for_request(request.id)
    with session_factory() as session:
        DesignProfileReservationRepository(session).release_active_for_request(request.id)
        session.commit()
    barrier = threading.Barrier(2)

    def reserve_and_commit(marker: str):
        with session_factory() as session:
            repo = DesignProfileReservationRepository(session)
            try:
                barrier.wait(timeout=5)
                reservation = repo.reserve_task(
                    design_task_id=task.id,
                    generation_request_id=request.id,
                    profile=_reservation_profile(sub_technique=f"blind sqli {marker}"),
                    profile_signature=f"sig-{marker}",
                    occupancy_scope="web",
                    exclusive_signature_key=f"exclusive-{marker}",
                    taxonomy_version=1,
                    policy_version=1,
                    ledger_version=0,
                )
                repo.set_current_reservation(task.id, reservation.id)
                session.commit()
                return {"marker": marker, "reservation_id": reservation.id}
            except Exception as exc:
                session.rollback()
                return {"marker": marker, "error": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(reserve_and_commit, "a"),
            pool.submit(reserve_and_commit, "b"),
        ]
        results = [future.result(timeout=30) for future in futures]

    assert {result.get("error") for result in results} <= {None, "IntegrityError"}
    with session_factory() as session:
        rows = session.scalars(
            sa.select(reservation_model.DesignProfileReservation).where(
                reservation_model.DesignProfileReservation.generation_request_id == request.id
            )
        ).all()
        active = [row for row in rows if row.state in {"reserved", "committed"}]
        assert len(active) == 1
        task_row = session.get(dt_model.DesignTask, task.id)
        assert task_row.current_reservation_id == active[0].id


def test_concurrent_reservations_same_signature_keep_one_active(
    session_factory: SessionFactory,
):
    first_request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["blind SQLi", "DOM XSS"],
    )
    second_request, _ = _seed(
        session_factory,
        target_count=1,
        distribution={"easy": 1},
        finding_labels=["SSRF", "XSS"],
    )
    service = DesignTaskPlanningService(session_factory)
    [first_task] = service.generate_for_request(first_request.id)
    [second_task] = service.generate_for_request(second_request.id)
    with session_factory() as session:
        repo = DesignProfileReservationRepository(session)
        repo.release_active_for_request(first_request.id)
        repo.release_active_for_request(second_request.id)
        session.commit()
    barrier = threading.Barrier(2)

    def reserve_with_signature(task_id, request_id, marker: str):
        with session_factory() as session:
            repo = DesignProfileReservationRepository(session)
            try:
                barrier.wait(timeout=5)
                reservation = repo.reserve_task(
                    design_task_id=task_id,
                    generation_request_id=request_id,
                    profile=_reservation_profile(sub_technique="shared"),
                    profile_signature="shared-signature",
                    occupancy_scope="web",
                    exclusive_signature_key="shared-exclusive",
                    taxonomy_version=1,
                    policy_version=1,
                    ledger_version=0,
                )
                repo.set_current_reservation(task_id, reservation.id)
                session.commit()
                return {"marker": marker, "reservation_id": reservation.id}
            except Exception as exc:
                session.rollback()
                return {"marker": marker, "error": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(reserve_with_signature, first_task.id, first_request.id, "a"),
            pool.submit(reserve_with_signature, second_task.id, second_request.id, "b"),
        ]
        results = [future.result(timeout=30) for future in futures]

    assert any(result.get("reservation_id") for result in results)
    with session_factory() as session:
        rows = session.scalars(sa.select(reservation_model.DesignProfileReservation)).all()
        active = [row for row in rows if row.state in {"reserved", "committed"}]
        assert len(active) == 1
        assert all(
            row.exclusive_signature_key != "shared-exclusive"
            or row.id == active[0].id
            for row in rows
        )
        task_rows = session.scalars(
            sa.select(dt_model.DesignTask).where(
                dt_model.DesignTask.id.in_([first_task.id, second_task.id])
            )
        ).all()
        assert sum(1 for row in task_rows if row.current_reservation_id is not None) == 1
        assert any(row.current_reservation_id == active[0].id for row in task_rows)
