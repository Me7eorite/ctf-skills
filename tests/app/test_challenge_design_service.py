"""Postgres-backed tests for ChallengeDesignService orchestration."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from core.paths import ProjectPaths
from hermes.process import HERMES_TIMEOUT_RETURNCODE
from persistence.models import challenge_designs as cd_model
from persistence.models import design_profile_reservations as reservation_model
from persistence.models import design_tasks as dt_model
from persistence.models import research as model
from persistence.repositories import (
    ChallengeDesignRepository,
    DesignEvidenceRepository,
    DesignProfileReservationRepository,
    DesignTaskRepository,
)
from persistence.session import SessionFactory
from services.challenge_design_service import (
    ChallengeDesignConflictError,
    ChallengeDesignService,
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
        session.execute(sa.delete(cd_model.DesignDifficultyReview))
        session.execute(sa.delete(cd_model.DesignEvidence))
        session.execute(sa.delete(cd_model.ChallengeDesign))
        session.execute(sa.delete(cd_model.DesignAttempt))
        session.execute(sa.delete(reservation_model.DesignProfileReservation))
        session.execute(sa.delete(reservation_model.DesignProfileLedger))
        session.execute(sa.delete(dt_model.DesignTask))
        session.execute(sa.delete(model.ResearchFindingSource))
        session.execute(sa.delete(model.ResearchFinding))
        session.execute(sa.delete(model.ResearchSource))
        session.execute(sa.delete(model.HermesProfileBinding))
        session.execute(sa.delete(model.ResearchRun))
        session.execute(sa.delete(model.GenerationRequest))
        session.commit()
    yield


class FakeDesignExecutor:
    def __init__(self, *, stdout: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.exit_code = exit_code
        self.calls: list[dict[str, object]] = []

    def execute(self, prompt_text, profile_name, timeout_seconds, log_path, workspace):
        self.calls.append(
            {
                "prompt_text": prompt_text,
                "profile_name": profile_name,
                "timeout_seconds": timeout_seconds,
                "log_path": Path(log_path),
                "workspace": Path(workspace),
            }
        )
        return self.stdout, self.exit_code, 0.01


def _context_loader(_paths: ProjectPaths):
    from services.design_prompt import DesignPromptContext

    references = {
        "design-core.md": "design core",
        "category-tactics.md": "category tactics",
        "difficulty-rubric.md": "difficulty rubric",
        "shared_generation_strategy.md": "shared generation strategy",
    }
    return DesignPromptContext(skill_text="design skill", references=references)


def _seed(
    session_factory: SessionFactory,
    *,
    max_attempts: int = 2,
    binding_profile: str | None = "design-profile",
) -> tuple[UUID, UUID]:
    request_id = uuid4()
    run_id = uuid4()
    source_id = uuid4()
    finding_id = uuid4()
    task_id = uuid4()
    with session_factory() as session:
        if binding_profile is not None:
            session.add(
                model.HermesProfileBinding(
                    role="design",
                    profile_name=binding_profile,
                    description="design binding",
                    status="enabled",
                )
            )
        session.add(
            model.GenerationRequest(
                id=request_id,
                category="web",
                topic="SQL injection",
                target_count=1,
                difficulty_distribution={"medium": 1},
                runtime_constraints={},
                seed_urls=[],
                max_attempts=max_attempts,
                status="researched",
            )
        )
        session.add(
            model.ResearchRun(
                id=run_id,
                generation_request_id=request_id,
                attempt=1,
                status="completed",
                finished_at=datetime.now(timezone.utc),
                profile_name_used="default",
            )
        )
        session.add(
            model.ResearchSource(
                id=source_id,
                research_run_id=run_id,
                url="https://example.com/sqli",
                title="SQLi",
                summary="boolean blind SQL injection",
                content_hash="hash",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            model.ResearchFinding(
                id=finding_id,
                research_run_id=run_id,
                kind="technique",
                label="boolean blind sqli",
                summary="branch on truthy responses",
            )
        )
        session.add(model.ResearchFindingSource(finding_id=finding_id, source_id=source_id))
        session.add(
            dt_model.DesignTask(
                id=task_id,
                generation_request_id=request_id,
                research_run_id=run_id,
                task_no=1,
                challenge_id="web-0001",
                title="Blind Login",
                category="web",
                difficulty="medium",
                primary_technique="boolean blind sqli",
                learning_objective="Extract data through conditional responses.",
                points=200,
                port=8081,
                scenario="Login leaks booleans through redirects.",
                constraints={"runtime": "docker"},
                evidence_summary="Finding supports boolean inference.",
                finding_ids=[str(finding_id)],
                status="queued",
            )
        )
        session.commit()
    return task_id, finding_id


def _valid_stdout() -> str:
    # Phase 2 rubric for medium: 2–3 distinct techniques, 2–5
    # intended_path steps, ≥60-char player prompt.
    return json.dumps(
        {
            "event": {"flag_format": "flag{...}"},
            "challenges": [
                {
                    "id": "web-0001",
                    "title": "Blind Login",
                    "category": "web",
                    "difficulty": "medium",
                    "points": 200,
                    "deployment": "single docker compose service on port 8081",
                    "port": 8081,
                    "primary_technique": "boolean blind sqli",
                    "secondary_technique": "session-cookie role flag",
                    "techniques": [
                        "boolean blind sqli",
                        "session-cookie role flag",
                    ],
                    "learning_objective": "Extract data through conditional responses.",
                    "difficulty_reason": (
                        "Medium difficulty is justified because the player must "
                        "first recover an admin password through boolean "
                        "conditions, then use that credential to access a "
                        "separate gated note containing the flag."
                    ),
                    "prompt": (
                        "Customer-support agents triage tickets in this portal; "
                        "recover the admin's pinned note."
                    ),
                    "intended_path": [
                        "Spot the conditional response on the login form",
                        "Extract the admin password byte-by-byte",
                        "Log in and read the pinned note",
                    ],
                    "artifacts": [
                        "README.md",
                        "metadata.json",
                        "validate.sh",
                        "deploy/Dockerfile",
                        "deploy/docker-compose.yml",
                        "deploy/src/app.py",
                        "deploy/_files/start.sh",
                        "writenup/wp.md",
                        "writenup/exp.py",
                    ],
                    "flag_location": "FLAG environment variable",
                    "validation": "Run exp.py against the compose service.",
                    "hints": [
                        "Observe redirects.",
                        "Boolean conditions change the response.",
                        "Extract the secret one character at a time.",
                    ],
                    "unintended_solutions": [
                        "Dumping the DB via a second injection point — only one "
                        "parameter is unsanitized.",
                        "Reading FLAG from the image — it is injected via env at "
                        "runtime only.",
                    ],
                    "asset_flow": [
                        {
                            "stage": 1,
                            "player_input_or_capability": "Anonymous login form",
                            "technique": "boolean blind sqli",
                            "produced_asset_or_capability": "Recovered admin password",
                            "why_next_stage_requires_it": "The pinned note is visible only after admin login.",
                        }
                    ],
                    "shortcut_closure": [
                        "The pinned note route checks the authenticated admin session server-side.",
                        "The FLAG value is injected at runtime and is not stored in public files.",
                    ],
                    "fingerprint": {
                        "entrypoint_type": "login form",
                        "asset_flow_shape": "blind_sqli -> admin_password -> gated_note -> flag",
                        "flag_access_model": "admin-session gated note",
                        "scenario_type": "customer support ticket portal",
                    },
                    "actual_solution_type": ["boolean_blind_sqli"],
                }
            ],
        }
    )


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
            "scenario_type": "ticket_queue",
            "input_model": "web_form",
        },
    }


def _build_contract(profile: dict[str, object]) -> dict[str, object]:
    return {
        "artifact_ids": ["primary"],
        "fixture_ids": ["admin-password", "runtime-key"],
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
                "input_fixture": None,
                "assertion": "stdout_not_contains_flag",
            }
        ],
        "acceptance_tests": [
            {
                "id": "solver-pass",
                "test_kind": "solver_with_fixture",
                "fixture_ref": "admin-password",
                "assertion": "outputs_flag",
            }
        ],
        "allowed_implementation_freedom": ["file_names"],
    }


def _governed_stdout(finding_id: UUID, *, profile: dict[str, object] | None = None) -> str:
    payload = json.loads(_valid_stdout())
    challenge = payload["challenges"][0]
    governed_profile = profile or _governed_profile()
    challenge["governed_profile"] = governed_profile
    challenge["design_evidence"] = {
        "research_finding_ids": [str(finding_id)],
        "claims": ["Boolean response branching supports payload injection."],
    }
    challenge["distinctness_claim"] = (
        "This differs from ledger siblings by using a payload-injection solve "
        "and database-record concealment profile."
    )
    challenge["compared_challenge_ids"] = []
    challenge["build_contract"] = _build_contract(governed_profile)
    return json.dumps(payload)


def _attach_reservation(session_factory: SessionFactory, task_id: UUID) -> UUID:
    from domain.design.profile_taxonomy import canonical_profile_signatures

    profile = _governed_profile()
    with session_factory() as session:
        task = session.get(dt_model.DesignTask, task_id)
        assert task is not None
        repo = DesignProfileReservationRepository(session)
        ledger = repo.lock_ledger("web", policy_version=1)
        signatures = canonical_profile_signatures(
            profile,
            category="web",
            policy_version=1,
        )
        reservation = repo.reserve_task(
            design_task_id=task_id,
            generation_request_id=task.generation_request_id,
            profile=profile,
            profile_signature=signatures.combined_profile_signature,
            occupancy_scope="web",
            exclusive_signature_key=None,
            taxonomy_version=1,
            policy_version=1,
            ledger_version=ledger.ledger_version,
        )
        repo.set_current_reservation(task_id, reservation.id)
        session.commit()
        return reservation.id


def _attach_sibling_reservation(session_factory: SessionFactory, task_id: UUID) -> UUID:
    from domain.design.profile_taxonomy import canonical_profile_signatures

    profile = _governed_profile()
    profile["solve"] = {
        **profile["solve"],
        "required_action": "credential_forgery",
    }
    with session_factory() as session:
        task = session.get(dt_model.DesignTask, task_id)
        assert task is not None
        sibling_id = uuid4()
        session.add(
            dt_model.DesignTask(
                id=sibling_id,
                generation_request_id=task.generation_request_id,
                research_run_id=task.research_run_id,
                task_no=2,
                challenge_id="web-0002",
                title="Forged Session",
                category="web",
                difficulty="medium",
                primary_technique="jwt",
                learning_objective="Forge a credential.",
                points=200,
                port=8082,
                scenario="Sibling task.",
                constraints={},
                evidence_summary="Sibling evidence.",
                finding_ids=list(task.finding_ids),
                status="queued",
            )
        )
        repo = DesignProfileReservationRepository(session)
        ledger = repo.lock_ledger("web", policy_version=1)
        signatures = canonical_profile_signatures(
            profile,
            category="web",
            policy_version=1,
        )
        reservation = repo.reserve_task(
            design_task_id=sibling_id,
            generation_request_id=task.generation_request_id,
            profile=profile,
            profile_signature=signatures.combined_profile_signature,
            occupancy_scope="web",
            exclusive_signature_key=None,
            taxonomy_version=1,
            policy_version=1,
            ledger_version=ledger.ledger_version,
        )
        repo.set_current_reservation(sibling_id, reservation.id)
        session.commit()
        return reservation.id


def _insert_conflicting_reservation(session_factory: SessionFactory, task_id: UUID) -> None:
    from domain.design.profile_taxonomy import canonical_profile_signatures

    profile = _governed_profile()
    with session_factory() as session:
        task = session.get(dt_model.DesignTask, task_id)
        assert task is not None
        sibling_id = uuid4()
        session.add(
            dt_model.DesignTask(
                id=sibling_id,
                generation_request_id=task.generation_request_id,
                research_run_id=task.research_run_id,
                task_no=99,
                challenge_id="web-conflict",
                title="Conflict",
                category="web",
                difficulty="medium",
                primary_technique="boolean blind sqli",
                learning_objective="Conflict profile.",
                points=200,
                port=8099,
                scenario="Conflict.",
                constraints={},
                evidence_summary="Conflict.",
                finding_ids=list(task.finding_ids),
                status="queued",
            )
        )
        repo = DesignProfileReservationRepository(session)
        ledger = repo.lock_ledger("web", policy_version=1)
        ledger.ledger_version += 1
        signatures = canonical_profile_signatures(
            profile,
            category="web",
            policy_version=1,
        )
        repo.reserve_task(
            design_task_id=sibling_id,
            generation_request_id=task.generation_request_id,
            profile=profile,
            profile_signature=signatures.combined_profile_signature,
            occupancy_scope="web",
            exclusive_signature_key=None,
            taxonomy_version=1,
            policy_version=1,
            ledger_version=ledger.ledger_version,
        )
        session.commit()


def _service(
    tmp_path: Path,
    session_factory: SessionFactory,
    executor: FakeDesignExecutor,
) -> ChallengeDesignService:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    return ChallengeDesignService(
        paths=paths,
        session_factory=session_factory,
        executor=executor,  # type: ignore[arg-type]
        timeout_seconds=30,
        prompt_context_loader=_context_loader,
    )


def test_happy_path_inserts_design_and_marks_task_designed(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, finding_id = _seed(session_factory)
    executor = FakeDesignExecutor(stdout=_valid_stdout())

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "designed"
    assert result.attempt_status == "completed"
    assert result.error is None
    assert result.challenge_design is not None
    assert executor.calls[0]["profile_name"] == "design-profile"
    assert executor.calls[0]["timeout_seconds"] == 30
    assert "boolean blind sqli" in executor.calls[0]["prompt_text"]
    assert executor.calls[0]["workspace"] == (
        tmp_path / "work" / "design" / "executions" / str(result.attempt_id)
    )
    prompt_path = tmp_path / "work" / "design" / "prompts" / f"{result.attempt_id}.md"
    assert prompt_path.exists()

    with session_factory() as session:
        task = DesignTaskRepository(session).get_design_task(task_id)
        assert task is not None and task.status == "designed"
        attempts = ChallengeDesignRepository(session).list_attempts(task_id)
        assert len(attempts) == 1
        assert attempts[0].prompt_path == f"work/design/prompts/{result.attempt_id}.md"
        assert attempts[0].hermes_log_path == f"work/design/logs/{result.attempt_id}.log"
        assert ChallengeDesignRepository(session).latest_design(task_id) is not None


def test_governed_design_commits_evidence_and_reservation(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, finding_id = _seed(session_factory)
    reservation_id = _attach_reservation(session_factory, task_id)
    executor = FakeDesignExecutor(stdout=_governed_stdout(finding_id))

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "designed"
    assert "Governed Design Reservation" in executor.calls[0]["prompt_text"]
    with session_factory() as session:
        evidence = DesignEvidenceRepository(session).latest_live_for_task(task_id)
        assert evidence is not None
        assert evidence.research_finding_ids == (finding_id,)
        assert evidence.build_contract["required_profile"] == _governed_profile()
        task = DesignTaskRepository(session).get_design_task(task_id)
        assert task is not None
        assert task.current_design_evidence_id == evidence.id
        reservation = DesignProfileReservationRepository(session).get(reservation_id)
        assert reservation is not None
        assert reservation.state == "committed"


def test_governed_prompt_includes_sibling_reservation_ledger(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, finding_id = _seed(session_factory)
    _attach_reservation(session_factory, task_id)
    sibling_reservation_id = _attach_sibling_reservation(session_factory, task_id)
    executor = FakeDesignExecutor(stdout=_governed_stdout(finding_id))

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "designed"
    prompt = str(executor.calls[0]["prompt_text"])
    assert f"reservation:{sibling_reservation_id}" in prompt
    assert "sibling_reservation:reserved" in prompt


def test_governed_design_rejects_forged_finding_and_keeps_reservation(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _finding_id = _seed(session_factory)
    reservation_id = _attach_reservation(session_factory, task_id)
    executor = FakeDesignExecutor(stdout=_governed_stdout(uuid4()))

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.attempt_status == "failed"
    assert result.error is not None
    assert "outside the task ResearchRun" in result.error
    with session_factory() as session:
        assert DesignEvidenceRepository(session).latest_live_for_task(task_id) is None
        reservation = DesignProfileReservationRepository(session).get(reservation_id)
        assert reservation is not None
        assert reservation.state == "reserved"


def test_governed_design_rejects_profile_drift(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, finding_id = _seed(session_factory)
    reservation_id = _attach_reservation(session_factory, task_id)
    drifted = _governed_profile()
    drifted["solve"] = {**drifted["solve"], "required_action": "credential_forgery"}
    executor = FakeDesignExecutor(stdout=_governed_stdout(finding_id, profile=drifted))

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.attempt_status == "failed"
    assert result.error is not None
    assert "exactly match the reservation" in result.error
    with session_factory() as session:
        reservation = DesignProfileReservationRepository(session).get(reservation_id)
        assert reservation is not None
        assert reservation.state == "reserved"


def test_governed_design_rejects_incomplete_build_contract(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, finding_id = _seed(session_factory)
    _attach_reservation(session_factory, task_id)
    payload = json.loads(_governed_stdout(finding_id))
    payload["challenges"][0]["build_contract"].pop("required_asset_flow")
    executor = FakeDesignExecutor(stdout=json.dumps(payload))

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.attempt_status == "failed"
    assert result.error is not None
    assert "build_contract.required_asset_flow is required" in result.error


def test_governed_design_rejects_stale_ledger_and_preserves_retry(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, finding_id = _seed(session_factory)
    reservation_id = _attach_reservation(session_factory, task_id)

    class LedgerAdvancingExecutor(FakeDesignExecutor):
        def execute(self, prompt_text, profile_name, timeout_seconds, log_path, workspace):
            _insert_conflicting_reservation(session_factory, task_id)
            return super().execute(
                prompt_text,
                profile_name,
                timeout_seconds,
                log_path,
                workspace,
            )

    executor = LedgerAdvancingExecutor(stdout=_governed_stdout(finding_id))

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.attempt_status == "failed"
    assert result.error == "stale_design_ledger"
    with session_factory() as session:
        assert DesignEvidenceRepository(session).latest_live_for_task(task_id) is None
        reservation = DesignProfileReservationRepository(session).get(reservation_id)
        assert reservation is not None
        assert reservation.state == "reserved"
        task = DesignTaskRepository(session).get_design_task(task_id)
        assert task is not None
        assert task.status == "queued"


def test_project_root_output_leak_fails_design_attempt(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory)

    class LeakingExecutor(FakeDesignExecutor):
        def execute(self, prompt_text, profile_name, timeout_seconds, log_path, workspace):
            result = super().execute(
                prompt_text,
                profile_name,
                timeout_seconds,
                log_path,
                workspace,
            )
            leaked = tmp_path / "output" / "pwn-leak.json"
            leaked.parent.mkdir(parents=True, exist_ok=True)
            leaked.write_text("{}\n", encoding="utf-8")
            return result

    executor = LeakingExecutor(stdout=_valid_stdout())

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.attempt_status == "failed"
    assert result.challenge_design is None
    assert result.error is not None
    assert "outside the design workspace" in result.error
    assert "output/pwn-leak.json" in result.error
    with session_factory() as session:
        assert ChallengeDesignRepository(session).latest_design(task_id) is None


def test_schema_invalid_requeues_when_retry_remains(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=2)
    executor = FakeDesignExecutor(stdout="{}")

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "queued"
    assert result.attempt_status == "failed"
    assert result.challenge_design is None
    assert "event" in result.error
    assert "challenges" in result.error
    with session_factory() as session:
        assert DesignTaskRepository(session).get_design_task(task_id).status == "queued"
        attempts = ChallengeDesignRepository(session).list_attempts(task_id)
        assert len(attempts) == 1
        assert attempts[0].attempt == 1
        assert attempts[0].last_error == result.error


def test_retry_prompt_includes_previous_validation_error(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=2)
    executor = FakeDesignExecutor(stdout="{}")
    service = _service(tmp_path, session_factory, executor)

    first = service.design_for_task(task_id, "alice")
    service.design_for_task(task_id, "alice")

    assert first.error is not None
    assert "## Retry Feedback" in executor.calls[1]["prompt_text"]
    assert first.error in executor.calls[1]["prompt_text"]


def test_retry_prompt_includes_previous_draft_seed_path(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=2)
    first_payload = json.loads(_valid_stdout())
    first_payload["challenges"][0]["techniques"] = ["DOM XSS", "CSP bypass"]
    executor = FakeDesignExecutor(stdout=json.dumps(first_payload))
    service = _service(tmp_path, session_factory, executor)

    service.design_for_task(task_id, "alice")
    service.design_for_task(task_id, "alice")

    assert "## Previous Draft Seed" in executor.calls[1]["prompt_text"]
    assert "./state/previous_design.json" in executor.calls[1]["prompt_text"]


def test_successful_design_writes_snapshot_for_retry_reuse(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=2)
    executor = FakeDesignExecutor(stdout=_valid_stdout())
    service = _service(tmp_path, session_factory, executor)

    first = service.design_for_task(task_id, "alice")
    snapshot = (
        tmp_path
        / "work"
        / "design"
        / "executions"
        / str(first.attempt_id)
        / "state"
        / "last_design_draft.json"
    )

    assert snapshot.exists()
    assert "\"challenges\"" in snapshot.read_text(encoding="utf-8")


def test_retry_prompt_includes_prebuild_review_feedback_from_completed_attempt(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=2)
    with session_factory() as session:
        session.add(
            cd_model.DesignAttempt(
                id=uuid4(),
                design_task_id=task_id,
                attempt=1,
                status="completed",
                claim_token=uuid4(),
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                profile_name_used="default",
                last_error=(
                    "Pre-build difficulty review failed.\n"
                    "Required revisions:\n- revise asset_flow"
                ),
            )
        )
        session.get(dt_model.DesignTask, task_id).status = "queued"
        session.commit()
    executor = FakeDesignExecutor(stdout=_valid_stdout())

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.attempt_status == "completed"
    assert result.design_task_status == "designed"
    assert "## Retry Feedback" in executor.calls[0]["prompt_text"]
    assert "revise asset_flow" in executor.calls[0]["prompt_text"]
    with session_factory() as session:
        attempts = ChallengeDesignRepository(session).list_attempts(task_id)
        assert [attempt.attempt for attempt in attempts] == [1, 2]


def test_exhausted_retry_marks_task_failed(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=1)
    executor = FakeDesignExecutor(stdout="{}")

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "failed"
    assert result.attempt_status == "failed"
    assert "event" in result.error
    assert "challenges" in result.error


def test_timeout_path_records_timeout(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, max_attempts=2)
    executor = FakeDesignExecutor(exit_code=HERMES_TIMEOUT_RETURNCODE)

    result = _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert result.design_task_status == "queued"
    assert result.attempt_status == "failed"
    assert result.error == "timeout"


def test_missing_design_binding_falls_back_to_default_profile(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory, binding_profile=None)
    executor = FakeDesignExecutor(stdout=_valid_stdout())

    _service(tmp_path, session_factory, executor).design_for_task(task_id, "alice")

    assert executor.calls[0]["profile_name"] == "default"


def test_second_call_after_success_returns_conflict(
    session_factory: SessionFactory,
    tmp_path: Path,
):
    task_id, _ = _seed(session_factory)
    executor = FakeDesignExecutor(stdout=_valid_stdout())
    service = _service(tmp_path, session_factory, executor)
    service.design_for_task(task_id, "alice")

    with pytest.raises(ChallengeDesignConflictError, match="expected queued"):
        service.design_for_task(task_id, "bob")
