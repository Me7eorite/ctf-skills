"""Pytest session bootstrap.

Loads the project-root `.env` once at collection time so that postgres-
marked test fixtures (which read `TEST_DATABASE_URL` from `os.environ`
without going through `persistence.engine.create_engine_from_env`) can
see values written into `.env`. Without this, fixtures would skip
unless the operator exported the variable in the shell.

The loader is the same one used at runtime — `override=False` so a
shell `export` still wins over the file.
"""

from __future__ import annotations


def pytest_configure(config) -> None:
    # 中文注释：让 .env 在测试 collection 阶段就生效，方便 postgres fixture 直接读 os.environ。
    from persistence.engine import _ensure_dotenv_loaded

    _ensure_dotenv_loaded()
