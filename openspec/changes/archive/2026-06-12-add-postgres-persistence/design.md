## Context

Challenge Factory today is file-backed: shards on disk, an append-only event log in SQLite at `work/state.sqlite3`, and JSON artifacts. The event SQLite is high-write, append-only, and intentionally narrow — it is not the place to grow relational tables for research findings, plan evaluations, or versioned challenge specs.

Upcoming work needs schema evolution, foreign keys, and a clear transactional boundary that fits naturally on PostgreSQL. Rather than try to retrofit those concerns onto the event store, this change introduces a separate PostgreSQL persistence layer with **no business tables**. Each follow-up change (`add-research-planning-core`, `add-plan-evaluation-and-approval`, `add-research-planning-ui`) lands its tables on this stable substrate.

A shared dev PostgreSQL instance already exists on the lab network. Contributors point `DATABASE_URL` at that host; passwords are distributed out-of-band. The default database name is `challenge_factory`.

## Goals / Non-Goals

**Goals:**

- Provide one canonical way to obtain a SQLAlchemy 2 `Engine` and `Session` from environment configuration.
- Provide an Alembic-driven migration workflow that runs against a fresh empty database and rolls back cleanly.
- Make connection misconfiguration loud and obvious at startup; no silent degraded mode.
- Add the `persistence` package to the layered source layout with explicit dependency edges enforced by the existing direction guardrail.
- Keep the existing event SQLite store, shard queue, matrix format, and all prompts untouched.

**Non-Goals:**

- Business tables (`generation_requests`, `research_runs`, `research_sources`, `research_findings`, `challenge_plans`, `plan_evaluations`, `challenge_specs`, etc.) — those land in `add-research-planning-core`.
- Replacing or migrating the event SQLite store.
- Multi-database, read-replica, or async-session (`asyncpg`) support.
- Connection pool tuning beyond `pool_pre_ping=True`.
- Any UI changes, CLI subcommands beyond `tools/scripts/db.sh`, or prompt changes.

## Decisions

### DEC-1: SQLAlchemy 2.x with `psycopg` driver, no other DB drivers

Use `sqlalchemy>=2.0` with the modern declarative + typed-mapping API and `psycopg[binary]>=3` as the only driver. URLs must be of the form `postgresql+psycopg://<user>:<password>@<host>:<port>/<database>`. Anything else is a configuration error.

**Why over alternatives:** pinning the driver removes a class of "works in dev, breaks in prod" surprises and removes the temptation of a hidden SQLite shortcut. `asyncpg` was considered for future async paths but rejected here because the existing stack is fully synchronous; adopting async would be a separate change with its own session-lifecycle implications.

### DEC-2: No silent SQLite fallback, ever

`persistence.create_engine_from_env()` raises `PersistenceConfigurationError` if `DATABASE_URL` is missing or has a non-Postgres scheme. The application SHALL NOT construct an SQLite engine, in-memory engine, or any other degraded backend.

**Why:** the whole point of this layer is to centralize relational state. A silent fallback would let half the system run on Postgres and the other half on SQLite without anyone noticing — exactly the failure mode this change is meant to prevent. Enforced by explicit unit tests on the raised error type for missing and wrong-scheme cases.

### DEC-3: `pytest-postgresql` for the test DB, skip cleanly when unset

Tests that need a real database use `pytest-postgresql` configured via the `TEST_DATABASE_URL` environment variable so CI and local dev can point at the shared dev instance or a managed instance. When `TEST_DATABASE_URL` is unset, those tests skip with a clear reason (`"TEST_DATABASE_URL not set"`) rather than spinning up an embedded database or falling back to SQLite.

**Why over alternatives:** `testcontainers[postgresql]` was considered and rejected — it would make `pytest` depend on a running Docker daemon and socket access, which no other test in the suite needs today. Bringing Docker into the unit-test path raises the bar for new contributors and complicates CI for marginal benefit.

### DEC-4: Alembic at the repo root, not under `src/`

Alembic's defaults expect `alembic.ini` and the `alembic/` directory at the repo root. Keeping them there avoids fighting the tool and makes the migration workflow obvious. `alembic/env.py` imports the engine from `persistence.engine` so there is exactly one URL-resolution path.

### DEC-5: Module ownership and dependency direction

The new `persistence` package sits at the same architectural altitude as `packing`. Allowed edges:

| Importer | Allowed targets |
| --- | --- |
| `cli` | `web`, `hermes`, `packing`, `persistence`, `domain`, `core` |
| `web` | `persistence`, `domain`, `core` |
| `hermes` | `domain`, `core` (unchanged — Hermes never touches the DB) |
| `packing` | `core` (unchanged) |
| `persistence` | `domain`, `core` |
| `domain` | `core` (unchanged) |
| `core` | (stdlib and third-party only) |

`hermes` deliberately does NOT gain a `persistence` edge. Hermes remains a pure prompt + subprocess subsystem; orchestration of research and planning runs against the database lives in `web` (or a future application-services layer introduced by `add-research-planning-core`). Encoded in `tests/app/test_dependency_direction.py` so the guardrail can't drift.

### DEC-6: No local compose for Postgres — use the shared dev instance

An earlier draft proposed a `docker-compose.dev.yml` running `postgres:16` locally per contributor. Removed because a shared dev PostgreSQL instance already exists on the lab network. Contributors set `DATABASE_URL` pointing at that host; tests requiring a database read `TEST_DATABASE_URL` the same way.

**Why:** avoids per-contributor data drift, keeps Docker out of the `pytest` path, and matches how the rest of the lab tooling is already wired. If a future contributor needs an isolated local instance (for example, to test a destructive migration without coordinating), they can run `docker run --rm -p 5432:5432 -e POSTGRES_PASSWORD=... postgres:16` ad hoc — no committed compose file required.

The concrete host is documented in `docs/persistence.md`. The password is distributed out-of-band and MUST NOT appear in committed proposal docs, fixtures, env files, or test code.

## Risks / Trade-offs

- **Hard Postgres dependency raises the bar for new contributors.** → Mitigated by clear skip behavior on Postgres-marked tests (no `TEST_DATABASE_URL` → skip with reason) and a documented bootstrap in `docs/persistence.md`. The default `uv run pytest` stays green without DB access.
- **Two stores coexist (event SQLite and PostgreSQL).** → Accepted. The event log is high-write and works fine where it is. A future change could consolidate; not this one. Documented as an explicit non-goal.
- **Alembic autogenerate diffs are noisy against the empty baseline until the first business tables land.** → Acceptable for one change cycle; the next change in the sequence introduces real tables.
- **Shared dev DB risks cross-contributor data drift.** → Accepted for now. If it becomes painful, `docker run` is a one-line escape hatch (DEC-6); a per-contributor schema-prefix convention is a future option.
- **Password-handling discipline depends on contributors.** → Mitigated by documenting the URL shape (without password) in `docs/persistence.md` and a CI lint that fails if a committed file matches a credential pattern is a worthwhile follow-up but is out of scope here.

## Migration Plan

No data migration. First deployment of this change requires, per environment:

1. Network reachability to the shared dev PostgreSQL host (PostgreSQL 14+; the lab runs 16).
2. `DATABASE_URL` set in the environment, with the password supplied out-of-band.
3. A pre-existing target database. Default name `challenge_factory`; create with `CREATE DATABASE challenge_factory;` before first use.
4. `alembic upgrade head` run once to stamp the baseline revision.

**Rollback:** `alembic downgrade base` followed by reverting the commit. No production data exists in the new database yet, so rollback is non-destructive.

## Open Questions

- None blocking. The default DB name `challenge_factory` is open to change before merge if a different convention is preferred; specs and code reference only the URL/env-var, not the name.
