# demo-mode Specification

## Purpose
TBD - synced from change frontend-rebuild. Update Purpose after archive.
## Requirements
### Requirement: --demo flag on serve

The `challenge-factory serve` CLI subcommand SHALL accept a `--demo`
boolean flag. When the flag is present, the server starts in Demo Mode
and SHALL NOT depend on the real `hermes` binary, Docker, or any
network resource. When the flag is absent, existing endpoint shapes,
queue semantics, and Hermes subprocess invocation behavior SHALL remain
unchanged.

#### Scenario: demo flag enables mode

- **WHEN** a user runs `uv run challenge-factory serve --demo` on a
  machine with no `hermes` binary on `$PATH` and no Docker daemon
- **THEN** the server starts successfully, binds the documented host
  and port, and the dashboard becomes reachable

#### Scenario: default mode unchanged

- **WHEN** a user runs `uv run challenge-factory serve` (no `--demo`)
- **THEN** existing endpoint shapes, queue semantics, and Hermes
  subprocess invocation behavior match the pre-change `serve` command

### Requirement: Built-in matrix and replay

Demo Mode SHALL ship with a built-in JSONL matrix of at least three
challenges spanning at least two categories (e.g., one web, one re).
On server start in `--demo`, the fake replayer SHALL split this matrix
into shards, create demo challenge directories with `metadata.json`
records, and drive each shard through every stage in `core.state.STAGES`
from `queued` to `complete`, with the entire batch finishing within 5
seconds of wall-clock time under default settings.

Each demo `metadata.json` record SHALL include at least `id`, `title`,
`category`, `difficulty`, `build_status`, `solve_status`, `flag`, and
one runtime descriptor such as `runtime`, `framework`, `language`, or
`target_format`, so the Challenges card grid has realistic data without
frontend demo branches.

#### Scenario: full replay completes in 5 seconds

- **WHEN** a user starts the server in demo mode and waits without
  interacting
- **THEN** within 5 seconds of process start, every challenge in the
  built-in matrix has reached the `complete` stage as observed via
  `GET /api/state`

### Requirement: Reuses real state plane

The Demo Mode replayer SHALL write through the same `core.state`
event recording path that the real Hermes runner uses, so the SPA
sees demo activity through the same `GET /api/state` and trace endpoints
with no demo-specific code paths in the frontend or read-model layer.

#### Scenario: snapshot indistinguishable in shape

- **WHEN** the SPA is connected to a server running in demo mode
- **THEN** the JSON returned by `GET /api/state` has the same field
  shape as a real run, including populated `challenges`, `shards`, and
  `progress` fields, and the SPA renders all views without conditional
  demo-mode rendering paths

### Requirement: Idempotent and resettable

If the server is restarted in `--demo` mode, the replayer SHALL clear
or partition any prior demo state so the replay can run again from
`queued`. Demo state MUST NOT persist across runs in a way that causes
the second start to appear "already complete" with stale timestamps.

#### Scenario: second demo run replays from scratch

- **WHEN** a user runs the server in demo mode, lets it complete, kills
  it, and runs `serve --demo` again
- **THEN** the snapshot at second start shows challenges in early
  stages (not `complete`) and progresses through the same 5-second
  replay

### Requirement: Demo mode is visually labeled

The SPA SHALL display a persistent, unambiguous "DEMO" badge in the
header whenever the connected server reports demo mode active. Because
the dashboard is a static export, the header SHALL reserve badge space
on first paint and SHALL show the actual "DEMO" label within 500ms
after `GET /api/mode` returns `{"demo": true}`.

#### Scenario: mode endpoint reports demo state

- **WHEN** a client issues `GET /api/mode` against a server started with
  `--demo`
- **THEN** the response status is 200 and the JSON body is
  `{"demo": true}`

#### Scenario: mode endpoint reports normal state

- **WHEN** a client issues `GET /api/mode` against a server started
  without `--demo`
- **THEN** the response status is 200 and the JSON body is
  `{"demo": false}`

#### Scenario: badge is present in demo mode

- **WHEN** the SPA loads against a server started with `--demo`
- **THEN** a header badge placeholder is present on first paint, and a
  header element with the text "DEMO" is rendered using the theme's
  accent color within 500ms after `/api/mode` returns

### Requirement: Read-only enforcement of mutating endpoints

The server in demo mode SHALL refuse every mutating HTTP request with
status `409 Conflict` and a JSON body equal to
`{"ok": false, "message": "Demo mode is read-only"}`, and MUST keep
all read endpoints fully functional. The 409 status is chosen for
consistency with the existing "current state does not permit this
action" responses in the shard requeue path, so the SPA's existing
error-handling code naturally surfaces the demo-mode message.

The mutating endpoints in scope for v1 are:

- `POST /api/actions/worker`
- `POST /api/actions/validate`
- `POST /api/seeds`
- `DELETE /api/seeds/{challenge_id}`
- `POST /api/seeds/enqueue`
- `POST /api/shards/{state}/{name}/requeue`

The read endpoints that MUST remain functional in demo mode include
`GET /api/state`, `GET /api/mode`, `GET /api/trace/stream`, and
`GET /api/logs/{name}`.

#### Scenario: mutating endpoint refused in demo mode

- **WHEN** the server is started with `--demo` and a client issues
  any request listed in the in-scope endpoint set above
- **THEN** the response status is `409`, `Content-Type` is
  `application/json`, and the body parses as JSON equal to
  `{"ok": false, "message": "Demo mode is read-only"}`

#### Scenario: read endpoints unaffected in demo mode

- **WHEN** the server is started with `--demo` and a client issues
  `GET /api/state`, `GET /api/mode`, `GET /api/trace/stream`, or
  `GET /api/logs/{name}`
- **THEN** the response is served normally with no demo-specific
  status code or body wrapping

#### Scenario: mutating endpoints unchanged without --demo

- **WHEN** the server is started without `--demo` and a client issues
  any of the in-scope mutating endpoints
- **THEN** the response status and body match the pre-change
  behavior of those endpoints — the demo-mode interceptor MUST NOT
  fire when demo mode is off
