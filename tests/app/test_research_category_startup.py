"""Startup consistency check: CLI warns when DB categories drift from
`core.queue.SUPPORTED_CATEGORIES`.

Postgres-backed: needs `TEST_DATABASE_URL` set so the fixture can spin
up a clean schema. Without that, the test skips cleanly so the default
`uv run pytest` stays green for users without a dev DB.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

import persistence.session as persistence_session
from persistence.models import research as model
from persistence.session import SessionFactory

import cli

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
def _route_default_factory(session_factory: SessionFactory):
    """Make `persistence.transaction()` use the TEST_DATABASE_URL factory."""
    original = persistence_session._default_factory
    persistence_session._default_factory = session_factory
    try:
        yield
    finally:
        persistence_session._default_factory = original


@pytest.fixture(autouse=True)
def _clean_categories(session_factory: SessionFactory):
    with session_factory() as session:
        session.execute(
            sa.delete(model.ChallengeCategory).where(
                model.ChallengeCategory.code.not_in(["web", "pwn", "re"])
            )
        )
        session.commit()


def _capture_stderr(callable_) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        callable_()
    return buffer.getvalue()


def test_no_warning_when_seed_matches_supported_set(session_factory: SessionFactory):
    # 中文注释：迁移种子默认就是 web/pwn/re，应当不打 warning。
    stderr = _capture_stderr(cli._check_category_consistency)
    assert "warning" not in stderr


def test_warns_when_db_has_extra_category(session_factory: SessionFactory):
    # 中文注释：插入一个 SUPPORTED_CATEGORIES 不认识的 category，应触发 warning。
    with session_factory() as session:
        session.add(
            model.ChallengeCategory(
                code="crypto", display_name="Cryptography", description="加解密题目"
            )
        )
        session.commit()

    stderr = _capture_stderr(cli._check_category_consistency)
    assert "category 'crypto' is allowed for research" in stderr
    assert "not yet supported by the shard pipeline" in stderr


def test_warns_when_supported_category_missing_from_db(
    session_factory: SessionFactory,
):
    # 中文注释：当 challenge_categories 缺少 SUPPORTED_CATEGORIES 中的一项时，
    # 也应该提示，便于发现 seed 与 SUPPORTED_CATEGORIES 不同步的情况。
    with session_factory() as session:
        session.execute(
            sa.delete(model.ChallengeCategory).where(model.ChallengeCategory.code == "pwn")
        )
        session.commit()

    stderr = _capture_stderr(cli._check_category_consistency)
    assert "'pwn' is in core.queue.SUPPORTED_CATEGORIES" in stderr
    assert "missing from challenge_categories" in stderr


def test_db_unreachable_is_silent():
    # 中文注释：当 transaction() 抛任何异常时，自检必须静默吞掉，不影响 CLI 启动。
    import persistence.session as ps

    saved = ps._default_factory

    class _BadFactory:
        def __call__(self):
            raise RuntimeError("boom")

    ps._default_factory = _BadFactory()
    try:
        stderr = _capture_stderr(cli._check_category_consistency)
    finally:
        ps._default_factory = saved
    assert stderr == ""
