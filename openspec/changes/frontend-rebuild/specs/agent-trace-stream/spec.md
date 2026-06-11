## ADDED Requirements

### Requirement: SSE endpoint contract

The FastAPI server SHALL expose `GET /api/trace/stream` as a Server-Sent
Events endpoint that streams structured per-worker agent activity. The
response MUST set `Content-Type: text/event-stream`, disable buffering,
and emit `keep-alive: ping` events at least every 15 seconds so
proxies do not close idle connections.

#### Scenario: endpoint responds with SSE

- **WHEN** a client issues `GET /api/trace/stream` with
  `Accept: text/event-stream`
- **THEN** the response status is 200, `Content-Type` is
  `text/event-stream`, and at least one `event: ping` is received
  within 15 seconds even if no workers are active

### Requirement: Trace event shape

Each emitted SSE data event SHALL be a JSON object on a single `data:`
line carrying the keys below. Clients MUST tolerate additional unknown
keys, so future changes MAY add fields without bumping the contract.

- `event_id` (number, required): the monotonically increasing
  `progress_events.id` of the source row, used by clients to dedupe
  across reconnects
- `worker` (string, required): the worker name the trace belongs to
- `shard` (string, required): the shard name the worker is processing,
  or empty string if idle
- `stage` (string, required): one of the stages from `core.state.STAGES`
- `status` (string, required): one of the statuses from
  `core.state.STATUSES`
- `message` (string, required): the progress message, truncated to 240
  characters; empty string when none was recorded
- `ts` (number, required): unix epoch seconds derived from the source
  row's `created_at` timestamp

#### Scenario: well-formed event

- **WHEN** the server emits a trace event sourced from a
  `progress_events` row
- **THEN** the event's `data:` payload parses as JSON containing at
  least `event_id`, `worker`, `shard`, `stage`, `status`, `message`,
  and `ts` with the documented types

### Requirement: Source of trace data

Trace events SHALL be derived solely by tailing the existing SQLite
`progress_events` rows written by `StateStore.record(...)`, using row
ids as the stream cursor. No in-memory enrichment, no observer hooks,
and no new persistent storage SHALL be introduced — SQLite is the
single observable source so that out-of-process workers, dashboard-
started worker subprocesses, and the demo replayer all surface the
same way. If the SQLite store is unreachable (temp-dir fallback mode
without persistence), the endpoint MUST still respond with 200 and
emit ping events, but data events MAY be empty.

When a client connects (or reconnects), the server SHALL start the
cursor at the current maximum `progress_events.id` and only emit rows
written after that point. Replaying historical events on reconnect is
explicitly out of scope; the SPA maintains rendered history in client
state.

#### Scenario: tracing without persistence

- **WHEN** the server is running in temp-dir fallback mode and a client
  connects to `/api/trace/stream`
- **THEN** the connection is accepted with status 200 and ping events
  are emitted at the documented interval

### Requirement: Frontend trace panel

The SPA SHALL include a dedicated Trace panel, accessible from the Live
Progress view, that consumes `/api/trace/stream` and displays a
chronological, auto-scrolling feed of trace events. Each row shows the
worker name, current stage, current status, and the latest message.
The feed MUST gracefully handle reconnect after a dropped SSE connection
without requiring a page refresh, and MUST dedupe by `event_id` so a
reconnect that arrives before the prior connection's last event is
acknowledged does not double-render.

#### Scenario: reconnect after disconnect

- **WHEN** the SSE connection drops while the Trace panel is mounted
- **THEN** the client automatically reconnects within 5 seconds and
  resumes appending new events without losing already-rendered history

### Requirement: Trace replay in Demo Mode

The trace stream SHALL surface demo trace events through the same
SQLite-tail path as real worker events when the server is started with
`--demo` (per the `demo-mode` capability). The event shape MUST be
identical to a real-worker event so the frontend has no awareness of
which mode it is consuming.

#### Scenario: demo mode produces same shape

- **WHEN** the server runs in demo mode and a client connects to
  `/api/trace/stream`
- **THEN** the events received conform to the same JSON shape defined
  in this spec, and `worker` values carry the `demo-` prefix assigned
  by the fake replayer
