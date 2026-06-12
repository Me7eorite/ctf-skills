# postgres-persistence Specification

## Purpose
TBD - created by archiving change add-postgres-persistence. Update Purpose after archive.
## Requirements
### Requirement: PostgreSQL is the only supported persistence backend

The system SHALL connect to PostgreSQL using a `DATABASE_URL` of the form `postgresql+psycopg://<user>:<password>@<host>:<port>/<database>`. The system SHALL NOT construct an SQLite engine, in-memory engine, or any other backend as a fallback when `DATABASE_URL` is missing, malformed, or unreachable.

#### Scenario: Missing DATABASE_URL is fatal

- **WHEN** the environment has no `DATABASE_URL` value and `persistence.create_engine_from_env()` is called
- **THEN** it raises `PersistenceConfigurationError`
- **AND** no engine, session factory, or connection is created

#### Scenario: Non-Postgres scheme is rejected

- **WHEN** `DATABASE_URL=sqlite:///work/state.sqlite3` is set and `persistence.create_engine_from_env()` is called
- **THEN** it raises `PersistenceConfigurationError` whose message names the rejected scheme

#### Scenario: Unreachable database surfaces a connection error

- **WHEN** a syntactically valid `DATABASE_URL` points at a host that refuses connections and the application performs its first session checkout
- **THEN** the call raises `PersistenceConnectionError` with the underlying `psycopg` exception chained as `__cause__`

### Requirement: Session lifecycle is bounded by an explicit transaction

The system SHALL expose a `transaction()` context manager from `persistence.session` that yields a SQLAlchemy `Session`, commits on successful exit, and rolls back on any exception while re-raising the original exception unchanged.

#### Scenario: Successful block commits

- **WHEN** code runs to completion inside `with transaction() as session:`
- **THEN** the transaction is committed
- **AND** the session is closed

#### Scenario: Exception inside block rolls back

- **WHEN** code inside `with transaction() as session:` raises `ValueError`
- **THEN** the transaction is rolled back
- **AND** `ValueError` propagates to the caller unchanged
- **AND** the session is closed

### Requirement: Alembic drives all schema changes

The system SHALL manage every PostgreSQL schema change through Alembic revisions checked into `alembic/versions/`. A fresh, empty database SHALL upgrade to `head` and downgrade back to `base` without manual intervention. Schema changes SHALL NOT be applied via runtime `CREATE TABLE`, ORM metadata `create_all`, or ad-hoc SQL.

#### Scenario: Empty database upgrades to head

- **WHEN** `alembic upgrade head` runs against an empty PostgreSQL database
- **THEN** the command exits with status 0
- **AND** `alembic current` reports the baseline revision id

#### Scenario: Head downgrades back to base

- **WHEN** `alembic downgrade base` runs against a database currently at `head`
- **THEN** the command exits with status 0
- **AND** no application tables remain in the public schema

### Requirement: Persistence package boundary

The `persistence` package SHALL expose `create_engine_from_env`, `SessionFactory`, `transaction`, `PersistenceConfigurationError`, and `PersistenceConnectionError` through `persistence/__init__.py`. The package SHALL NOT import from `web`, `hermes`, `packing`, or `cli`. `hermes` SHALL NOT import from `persistence`.

#### Scenario: Public API import succeeds

- **WHEN** running `python -c "from persistence import create_engine_from_env, SessionFactory, transaction, PersistenceConfigurationError, PersistenceConnectionError"`
- **THEN** the command exits with status 0 and no import error

#### Scenario: Hermes cannot import persistence

- **WHEN** a hypothetical `src/hermes/runner.py` contains `from persistence import transaction` and the dependency direction test runs
- **THEN** the test fails and the diagnostic identifies `hermes -> persistence` as the forbidden edge

