## Why

The current control plane is file-backed and uses SQLite (`work/state.sqlite3`) only as an append-only progress event store. Upcoming research, planning, approval, and versioned-spec capabilities need a relational store with schema evolution and strict referential integrity — none of which the event SQLite is shaped for. This change lands the database substrate as its own reviewable diff so follow-up changes (`add-research-planning-core`, `add-plan-evaluation-and-approval`, `add-research-planning-ui`) can stack cleanly on top.

## What Changes

- Add runtime deps: `sqlalchemy>=2.0`, `psycopg[binary]>=3`, `alembic>=1.13`.
- Add a new `src/persistence/` package:
  - `engine.py` — `create_engine_from_env()` reads `DATABASE_URL`, requires the `postgresql+psycopg://` scheme, sets `pool_pre_ping=True`.
  - `session.py` — `SessionFactory` plus a `transaction()` context manager that commits on success and rolls back + re-raises on exception.
  - `errors.py` — `PersistenceConfigurationError`, `PersistenceConnectionError`.
  - `__init__.py` — public re-exports.
- Add Alembic at the repo root: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, and `alembic/versions/0001_baseline.py` (empty no-op revision so a fresh database can be stamped to head).
- Add `tools/scripts/db.sh` wrapping `alembic upgrade head`, `alembic downgrade -1`, and `alembic revision --autogenerate -m`.
- Add `docs/persistence.md` documenting the `DATABASE_URL` shape, the shared dev Postgres host, the default database name `challenge_factory`, and the bootstrap workflow. Credentials are NEVER committed — only URL shape and host. Passwords are distributed out-of-band.
- Make Postgres connection failure fatal at startup: missing `DATABASE_URL`, a non-Postgres scheme, or an unreachable host raises a typed error. The runtime SHALL NOT fall back to SQLite, in-memory storage, or any silent degraded mode.
- Extend the `module-architecture` spec to recognize `persistence` and update the dependency direction matrix. `persistence -> domain, core`; `cli` and `web` may import `persistence`; `hermes` MUST NOT.
- No `docker-compose.dev.yml`: contributors point `DATABASE_URL` at the shared lab Postgres host. Rationale lives in `design.md`.
- No business tables. No replacement of the event SQLite. No touch to `src/core/state.py`, the shard queue, the matrix format, or any prompt.

**BREAKING**: None — no existing module imports `persistence` yet.

## Capabilities

### New Capabilities
- `postgres-persistence`: PostgreSQL connection lifecycle, session and transaction boundary, Alembic-driven schema migration framework, the no-silent-fallback contract, and the persistence package boundary rules.

### Modified Capabilities
- `module-architecture`: add `persistence` to the recognized packages list and update the inter-package dependency direction matrix so `cli` and `web` may import `persistence`, `persistence` may import `domain` and `core`, and `hermes` continues to be forbidden from importing `persistence`.

## Impact

- Adds `src/persistence/{__init__.py,engine.py,session.py,errors.py}`.
- Adds `alembic/`, `alembic.ini`, `alembic/versions/0001_baseline.py`, `tools/scripts/db.sh`, `docs/persistence.md`.
- Updates `pyproject.toml` (`[project] dependencies` and the `dev` group) and `uv.lock`.
- Updates `tests/app/test_dependency_direction.py` to include `persistence` and its allowed edges, plus rejection scenarios for `hermes -> persistence` and `persistence -> web`.
- Adds `tests/app/test_persistence_engine.py`, `tests/app/test_persistence_session.py`, and a Postgres-marked `tests/app/test_alembic_migrations.py` driven by `TEST_DATABASE_URL`.
- Does NOT touch `src/core/state.py`, `src/core/queue.py`, the shard queue layout, the matrix/shard JSON formats, prompts, or the dashboard UI. The event SQLite store and the new PostgreSQL store coexist after this change.
- Operational impact: contributors must set `DATABASE_URL` and have network reachability to the shared dev Postgres host before `alembic upgrade head` succeeds. Postgres-marked tests skip cleanly when `TEST_DATABASE_URL` is unset, so the default `uv run pytest` stays green on machines without DB access.
