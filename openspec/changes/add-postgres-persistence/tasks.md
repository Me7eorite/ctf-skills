## 1. Dependencies and tooling

- [x] 1.1 Add `sqlalchemy>=2.0`, `psycopg[binary]>=3`, `alembic>=1.13` to `[project] dependencies` in `pyproject.toml`.
- [x] 1.2 Add `pytest-postgresql` to the `dev` dependency group (per design.md DEC-3) and run `uv lock`.
- [x] 1.3 Add `tools/scripts/db.sh` wrapping `alembic upgrade head`, `alembic downgrade -1`, and `alembic revision --autogenerate -m`; make executable.
- [x] 1.4 Add `docs/persistence.md` covering: the required `DATABASE_URL` shape (`postgresql+psycopg://<user>:<password>@<host>:<port>/<db>`), the shared dev PostgreSQL host contributors point at, the default database name (`challenge_factory`), and the `alembic upgrade head` bootstrap. Credentials MUST NOT be committed — the doc shows only URL shape and host, with a note that the password is distributed out-of-band.

## 2. Persistence package

- [x] 2.1 Create `src/persistence/__init__.py` re-exporting `create_engine_from_env`, `SessionFactory`, `transaction`, `PersistenceConfigurationError`, `PersistenceConnectionError`.
- [x] 2.2 Implement `src/persistence/errors.py` with `PersistenceConfigurationError` and `PersistenceConnectionError` (both extend `Exception`).
- [x] 2.3 Implement `src/persistence/engine.py::create_engine_from_env()` reading `DATABASE_URL`, rejecting missing values and non-`postgresql+psycopg://` schemes with `PersistenceConfigurationError`, and constructing a SQLAlchemy 2 `Engine` with `pool_pre_ping=True`.
- [x] 2.4 Implement `src/persistence/session.py` with `SessionFactory` built from an `Engine`, and a `transaction()` context manager that yields a `Session`, commits on success, rolls back on exception, closes the session in `finally`, and re-raises the original exception unchanged.
- [x] 2.5 Confirm `persistence` imports only stdlib, third-party, `core`, and `domain` (no `web`, `hermes`, `packing`, `cli`).

## 3. Alembic framework

- [ ] 3.1 Add `alembic.ini` reading `sqlalchemy.url` from the `DATABASE_URL` environment variable.
- [ ] 3.2 Add `alembic/env.py` that imports the engine from `persistence.engine` and runs migrations in online mode against that engine.
- [ ] 3.3 Add `alembic/script.py.mako` template (standard Alembic template; no project customization needed yet).
- [ ] 3.4 Add `alembic/versions/0001_baseline.py` as an empty no-op revision (`def upgrade(): pass; def downgrade(): pass`) so a fresh database can be stamped to head.
- [ ] 3.5 Verify `alembic upgrade head` and `alembic downgrade base` both succeed against an empty database, with `alembic current` reporting the baseline revision after upgrade.

## 4. Failure surface

- [ ] 4.1 Verify `PersistenceConfigurationError` is raised when `DATABASE_URL` is missing; message names the missing variable.
- [ ] 4.2 Verify `PersistenceConfigurationError` is raised when `DATABASE_URL` has a non-Postgres scheme; message names the rejected scheme.
- [ ] 4.3 Verify `PersistenceConnectionError` is raised on first connect failure, with the original `psycopg` exception chained as `__cause__`.
- [ ] 4.4 Verify no error path constructs a SQLite engine, falls back to in-memory storage, or swallows the exception.

## 5. Dependency direction guardrail

- [ ] 5.1 Extend `tests/app/test_dependency_direction.py` to include `persistence` in the package matrix with allowed targets `domain, core`.
- [ ] 5.2 Extend the matrix entries for `cli` and `web` to allow importing `persistence`; leave `hermes` and `packing` unchanged.
- [ ] 5.3 Add scenarios for `hermes -> persistence`, `persistence -> web`, and `persistence -> hermes` being rejected with the offending file/line/edge in the diagnostic.

## 6. Tests

- [ ] 6.1 Add `tests/app/test_persistence_engine.py`: missing `DATABASE_URL`, wrong scheme (`sqlite:///`, `postgresql://` without `+psycopg`), malformed URL, valid URL builds an engine whose `dialect.name == "postgresql"`.
- [ ] 6.2 Add `tests/app/test_persistence_session.py`: `transaction()` commits on success path, rolls back when the block raises, propagates the original exception unchanged, closes the session in both paths. Use a mocked engine/session to keep this test free of Postgres dependency.
- [ ] 6.3 Add `tests/app/test_alembic_migrations.py` (marked `@pytest.mark.postgres`): against `TEST_DATABASE_URL`, upgrade an empty database to head, assert `alembic current` returns the baseline revision, downgrade back to base, assert no application tables remain. Skip cleanly with reason `"TEST_DATABASE_URL not set"` when the env var is absent.
- [ ] 6.4 Register the `postgres` pytest marker in `pyproject.toml` `[tool.pytest.ini_options].markers`.

## 7. Verification

- [ ] 7.1 Run `uv run ruff check`.
- [ ] 7.2 Run `uv run pytest tests/` and confirm Postgres-marked tests skip with the expected reason when `TEST_DATABASE_URL` is unset.
- [ ] 7.3 Export `TEST_DATABASE_URL` pointing at the shared dev Postgres, run `uv run pytest tests/ -m postgres`, and confirm zero skips and zero failures.
- [ ] 7.4 Run `openspec validate add-postgres-persistence --strict`.
