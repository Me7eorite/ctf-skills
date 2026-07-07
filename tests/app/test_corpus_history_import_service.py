"""Tests for reviewed historical corpus import helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from core.jsonio import write_json
from persistence.models import challenge_corpus as corpus_model
from persistence.session import SessionFactory
from services.corpus_history_import_service import CorpusHistoryImportService

ROOT = Path(__file__).resolve().parents[2]


def _reset_schema(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(scope="module")
def session_factory() -> SessionFactory:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    factory = SessionFactory(create_engine(url))
    yield factory
    _reset_schema(url)


@pytest.fixture(autouse=True)
def clean_db(session_factory: SessionFactory):
    with session_factory() as session:
        with session.begin():
            session.execute(sa.delete(corpus_model.CorpusHistoryEntry))


def _write_legacy_challenge(tmp_path: Path) -> Path:
    challenge = tmp_path / "web" / "web-legacy"
    (challenge / "deploy").mkdir(parents=True)
    (challenge / "writenup").mkdir()
    write_json(
        challenge / "metadata.json",
        {
            "id": "web-legacy",
            "category": "web",
            "publishable": True,
            "solve_status": "passed",
            "validation_status": "passed",
            "validation_final_flag_candidate": "flag{demo}",
        },
    )
    (challenge / "deploy" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (challenge / "writenup" / "exp.py").write_text("print('solve')\n", encoding="utf-8")
    return challenge


def test_history_import_preview_generates_minimal_fingerprints(tmp_path: Path) -> None:
    challenge = _write_legacy_challenge(tmp_path)

    preview = CorpusHistoryImportService().preview(challenge)

    assert preview.challenge_id == "web-legacy"
    assert preview.category == "web"
    assert preview.governance_scope == "history_projection_only"
    assert preview.fingerprint_version == 1
    assert preview.fingerprints["schema_version"] == 1
    assert preview.fingerprints["source"]["sha256"]
    assert preview.fingerprints["solver"]["sha256"]
    assert preview.fingerprints["combined"] == "legacy-unprofiled"


@pytest.mark.postgres
def test_history_import_apply_writes_reviewed_history_entry(
    tmp_path: Path,
    session_factory: SessionFactory,
) -> None:
    challenge = _write_legacy_challenge(tmp_path)

    result = CorpusHistoryImportService(
        session_factory=session_factory,
    ).import_reviewed(
        challenge,
        status="retired",
        audit_reason="manual reviewed historical import",
    )

    assert result.challenge_id == "web-legacy"
    assert result.status == "retired"
    assert result.governance_scope == "history_projection_only"
    with session_factory() as session:
        row = session.get(corpus_model.CorpusHistoryEntry, UUID(result.history_entry_id))
        assert row is not None
        assert row.build_attempt_id is None
        assert row.design_evidence_id is None
        assert row.artifact_observation_id is None
        assert row.audit_reason == "manual reviewed historical import"
        assert row.fingerprints["source"]["sha256"]
