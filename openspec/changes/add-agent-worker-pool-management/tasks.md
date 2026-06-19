## 1. Schema and domain model

- [ ] 1.1 Add `agents` with id, name, description, hermes_profile_name,
  control_state, max_concurrency, lease_seconds, heartbeat_at, last_error,
  deleted_at, timestamps, and enabled/disabled/draining semantics.
- [ ] 1.2 Add capability code storage/validation separate from `agents` for
  `research`, `design`, `build:web`, `build:pwn`, and `build:re`.
- [ ] 1.3 Add repository/domain DTOs and validators for names, profile names,
  control-state transitions, and capability sets.
- [ ] 1.4 Leave existing `agent_roles` and `hermes_profile_bindings` untouched;
  do not seed or migrate research/design agents in this change.
- [ ] 1.5 Add nullable build-attempt audit fields for agent id, agent name used,
  and Hermes profile name used by agent-owned executions.
- [ ] 1.6 Enforce the shared conservative name shape for agent names and Hermes
  profile names before persistence or subprocess invocation.

## 2. Hermes profile wrapper

- [ ] 2.1 Add a service wrapper for Hermes profile list/show/create/delete with
  timeouts and structured errors; keep profile creation separate from agent row
  creation.
- [ ] 2.2 Validate profile names before passing them to subprocess arguments.
- [ ] 2.3 Do not persist profile config, `.env`, memory, sessions, or secrets.
- [ ] 2.4 Reject profile deletion while any non-deleted agent references that
  profile.
- [ ] 2.5 Reject profile deletion while existing `hermes_profile_bindings`
  reference that profile.
- [ ] 2.6 Reuse the project's configured Hermes executable resolution instead
  of hardcoding a binary path or installing Hermes as part of this change.

## 3. Agent API

- [ ] 3.1 Add `GET /api/agents`.
- [ ] 3.2 Add `POST /api/agents`.
- [ ] 3.3 Add `GET /api/agents/{id}` and `PATCH /api/agents/{id}`.
- [ ] 3.4 Add `DELETE /api/agents/{id}` as soft delete with active-worker
  conflict handling and historical audit preservation.
- [ ] 3.5 Add `POST /api/agents/{id}/enable`,
  `POST /api/agents/{id}/disable`, and `POST /api/agents/{id}/drain`.
- [ ] 3.6 Add profile helper endpoints under `/api/hermes/profiles`.

## 4. Dashboard

- [ ] 4.1 Add an Agents navigation entry and list view.
- [ ] 4.2 Add create/edit forms with profile binding, capability checkboxes,
  max concurrency, and lease settings.
- [ ] 4.3 Add enable/disable/drain/delete actions with conflict handling,
  editable control-state display, and read-only health display.
- [ ] 4.4 Keep destructive profile deletion in the profile-helper UI only;
  agent deletion must never delete a Hermes profile.
- [ ] 4.5 Label idle/running/offline/error as derived health, not directly
  editable lifecycle states.

## 5. Worker-pool integration

- [ ] 5.0 Confirm `add-category-safe-build-dispatch` is implemented before
  enabling agent-owned build claim paths; otherwise leave this section blocked.
- [ ] 5.1 Make future worker-pool claim APIs accept `agent_id` and validate
  agent control state, derived health, and capabilities before claiming.
- [ ] 5.2 For build work, require `build:<category>` capability before calling
  constrained build dispatch.
- [ ] 5.3 Record both project agent identity and Hermes profile name used on
  execution-attempt audit rows.
- [ ] 5.4 Ensure draining agents finish active work but do not claim new work.
- [ ] 5.5 Keep existing non-agent `challenge-factory run` execution paths valid
  with nullable agent audit fields.
- [ ] 5.6 Do not add a multi-process supervisor in this change; reserve
  automatic process spawning and max-concurrency enforcement for a later
  worker-pool implementation.

## 6. Verification

- [ ] 6.1 Add migration/repository tests for agent and capability persistence.
- [ ] 6.2 Add service tests for control-state transitions and profile wrapper
  subprocess failures.
- [ ] 6.3 Add API tests for CRUD, conflict, and profile-helper endpoints.
- [ ] 6.4 Add dashboard interaction coverage or document manual smoke steps.
- [ ] 6.5 Add worker claim tests proving an agent without `build:pwn` cannot
  claim Pwn work.
- [ ] 6.6 Add tests proving profile deletion rejects both agent references and
  existing Hermes role/profile bindings.
- [ ] 6.7 Run `openspec validate add-agent-worker-pool-management --strict`.
