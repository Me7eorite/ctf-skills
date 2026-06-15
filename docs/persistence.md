# Persistence

Challenge Factory uses PostgreSQL for structured metadata that grows over time
(research, planning, evaluation, versioned challenge specs). The legacy event
log at `work/state.sqlite3` is unchanged and out of scope for the persistence
layer described here.

## Required environment variable

The application reads exactly one variable:

```
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/<database>
```

Only the `postgresql+psycopg://` scheme is accepted. Any other scheme (for
example `sqlite://`, plain `postgresql://` without `+psycopg`) is rejected at
startup with `PersistenceConfigurationError`. The runtime never falls back to
SQLite or in-memory storage.

## Dev database

A shared PostgreSQL instance is available on the lab network. Point
`DATABASE_URL` at it and create a database named `challenge_factory` (or pick
your own name and put it in the URL).

```
DATABASE_URL=postgresql+psycopg://<user>:<password>@<dev-postgres-host>:5432/challenge_factory
```

The host is documented in the team's onboarding notes; the password is
distributed out-of-band and **MUST NOT** be committed to this repo or to any
checked-in env file. Treat it as a secret even though it is dev-tier.

If you need an isolated local instance (for example, to test a destructive
migration without coordinating), spin one up ad hoc:

```
docker run --rm -p 5432:5432 -e POSTGRES_PASSWORD=devdev postgres:16
```

Then point `DATABASE_URL` at `127.0.0.1:5432` instead.

## Bootstrap

First-time setup against a fresh database:

```
createdb challenge_factory          # or: CREATE DATABASE challenge_factory;
export DATABASE_URL=postgresql+psycopg://...
tools/scripts/db.sh up              # alembic upgrade head
tools/scripts/db.sh current         # confirms baseline revision
```

A new database starts empty. The initial Alembic revision (`0001_baseline`) is
a no-op so the migration chain is well-formed; real tables arrive in follow-up
changes.

## Day-to-day commands

```
tools/scripts/db.sh up              # alembic upgrade head
tools/scripts/db.sh down            # alembic downgrade -1
tools/scripts/db.sh new "add foo"   # alembic revision --autogenerate -m "add foo"
tools/scripts/db.sh current         # alembic current
```

Every command requires `DATABASE_URL`. The wrapper exits with status 2 and a
clear message if it is unset.

## Tests

Tests that need a real database are marked `@pytest.mark.postgres` and read
`TEST_DATABASE_URL`. When `TEST_DATABASE_URL` is not set, those tests skip with
the reason `"TEST_DATABASE_URL not set"` so the default `uv run pytest` stays
green on machines without database access.

```
# Default — Postgres-marked tests skip:
uv run pytest

# Run only the Postgres-marked tests against the dev database:
export TEST_DATABASE_URL=postgresql+psycopg://<user>:<password>@<dev-postgres-host>:5432/challenge_factory_test
uv run pytest -m postgres
```

Use a separate database for tests (for example `challenge_factory_test`) so
the dev database is not affected by destructive migration tests.
