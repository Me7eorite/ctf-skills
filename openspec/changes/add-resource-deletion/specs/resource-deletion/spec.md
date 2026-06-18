## ADDED Requirements

### Requirement: Generation requests, design tasks, and build attempts are deletable

The system SHALL provide operator-initiated deletion for a generation request,
a design task, and an individual build attempt. Deleting a generation request
SHALL delete its relational research runs, sources, findings, design tasks,
design attempts, challenge designs, and build attempts. Deleting a design task
SHALL delete its relational design attempts, challenge designs, and build
attempts. Deleting one build attempt SHALL leave its parent design task and
sibling attempts intact.

#### Scenario: Delete a completed generation request

- **WHEN** an operator deletes a generation request whose cascade scope has no active execution
- **THEN** the request and all of its relational child rows are deleted
- **AND** unrelated requests and their children remain unchanged

#### Scenario: Delete one design task

- **WHEN** an operator deletes an inactive design task
- **THEN** that task, its design history, and its build attempts are deleted
- **AND** its parent generation request and sibling design tasks remain

#### Scenario: Delete one terminal build attempt

- **WHEN** an operator deletes a failed, lost, or succeeded build attempt
- **THEN** only that build-attempt row and its attempt-scoped operational state are deleted
- **AND** its parent design task and sibling attempts remain

### Requirement: Active execution prevents deletion

The system SHALL reject deletion with a conflict when the deletion scope
contains a `research_runs.status = running`, a
`design_attempts.status = running`, or a `build_attempts.status = running`.
Parent request/task status fields are diagnostic projections and SHALL NOT be
the sole active-work test. A task projected as `designing` conflicts unless all
of its design attempts are terminal. A task projected as `building` with only
queued build attempts MAY be deleted by safely withdrawing those attempts.
The system SHALL NOT terminate a process implicitly and SHALL leave database
and filesystem state unchanged after the conflict.

#### Scenario: Request contains running research

- **WHEN** an operator deletes a generation request with a running research run
- **THEN** the endpoint returns `409 Conflict`
- **AND** the request, its children, queue files, and artifacts remain unchanged

#### Scenario: Design task has an active design attempt

- **WHEN** an operator deletes a design task with a running design attempt
- **THEN** the endpoint returns `409 Conflict`
- **AND** no part of the task cascade is deleted

#### Scenario: Building projection contains only queued work

- **WHEN** an operator deletes a request or design task projected as `building`
- **AND** every affected build attempt is queued rather than running
- **THEN** the queued shards are safely withdrawn and deletion may proceed

#### Scenario: Build attempt is running

- **WHEN** an operator deletes a running build attempt
- **THEN** the endpoint returns `409 Conflict`
- **AND** its shard, progress, artifact, and database row remain unchanged

#### Scenario: Another sibling attempt is active

- **WHEN** an operator deletes one build attempt while a different sibling attempt is queued or running
- **THEN** the endpoint returns `409 Conflict`
- **AND** the target's shard/progress remains available as a possible resume source
- **AND** the shared challenge directory remains available to the active sibling

### Requirement: Queued builds are withdrawn before deletion

The system SHALL permit deletion of a queued build attempt only when its
attributed staging or pending shard can be atomically withdrawn from
worker-visible queue locations. It SHALL check for a matching attributed
running shard before and after withdrawal. If the shard was claimed, deletion
SHALL abort as an active-execution conflict and any withdrawn file SHALL be
restored.

For direct Build Attempt deletion, the queued target MUST be the only active
attempt for its Design Task. For request/task cascade deletion, all queued
attempts inside the deletion scope MAY be withdrawn together.

#### Scenario: Queued attempt is cancelled safely

- **WHEN** an operator deletes a queued build attempt whose shard is still in staging or pending
- **THEN** the system atomically removes the shard from worker-visible locations before committing the row deletion
- **AND** no worker can subsequently claim that shard

#### Scenario: Worker wins the claim race

- **WHEN** a matching attributed shard reaches `running/` during deletion
- **THEN** deletion returns `409 Conflict`
- **AND** the database row remains and any service-withdrawn file is restored

### Requirement: Attempt operational state is always cleaned

Deleting a build attempt directly or through a parent cascade SHALL remove its
attributed staging, pending, done, and failed shard files, claim sidecars, and
all `progress_events` and `progress_snapshots` rows keyed by its
`shard_basename`. Progress cleanup SHALL use the transaction-aware
`ProgressStore.purge_shards` operation and join the same PostgreSQL transaction
as relational deletion. Operational cleanup SHALL occur regardless of the
`delete_artifacts` option.

#### Scenario: Deleting a terminal attempt cleans progress

- **WHEN** a terminal build attempt with progress events and a snapshot is deleted with `delete_artifacts=false`
- **THEN** its progress events, snapshot, attributed shard, and claim sidecar are removed
- **AND** its generated challenge directory is retained

#### Scenario: Parent cascade cleans every affected attempt

- **WHEN** a design task containing multiple inactive build attempts is deleted
- **THEN** operational state for every affected shard basename is removed
- **AND** operational state for build attempts outside the task remains

### Requirement: Artifacts are retained by default

Every deletion operation SHALL default to `delete_artifacts=false`. In that
mode the system SHALL preserve affected challenge directories and referenced
research source, research log, design prompt, and design log files, while still
deleting database rows and operational state.

#### Scenario: Request deletion preserves artifacts by default

- **WHEN** an operator deletes a generation request without specifying `delete_artifacts`
- **THEN** the request cascade and operational state are deleted
- **AND** all non-operational files in its artifact scope remain on disk

#### Scenario: Design task deletion preserves its challenge

- **WHEN** an operator deletes a design task with `delete_artifacts=false`
- **THEN** its generated challenge directory remains on disk
- **AND** the response identifies it as retained

### Requirement: Artifact deletion is explicit and path-contained

When and only when `delete_artifacts=true`, the system SHALL delete
non-operational files owned exclusively by the deletion scope. Every candidate
path MUST resolve beneath `work/challenges`, `work/research`, or `work/design`.
The system SHALL NOT delete an unsafe path, an escaping symlink target, or a
path referenced by a surviving row; it SHALL report each such path as skipped.
Directory-name inference from category/challenge id SHALL NOT establish
ownership. An artifact without a direct persisted path reference SHALL be
retained and reported as skipped/unowned. The service SHALL prevent concurrent
updates to reference-bearing rows during the final shared-reference check and
candidate quarantine.

#### Scenario: Explicit option removes owned artifacts

- **WHEN** an operator deletes a design task with `delete_artifacts=true`
- **AND** its challenge directory is under `work/challenges` and is not referenced outside the deletion scope
- **THEN** the challenge directory is removed with the task
- **AND** the response lists the directory as deleted

#### Scenario: Traversal path is refused

- **WHEN** a persisted artifact path resolves outside all approved work roots
- **AND** deletion is requested with `delete_artifacts=true`
- **THEN** the external path remains unchanged
- **AND** the response lists it as skipped with an unsafe-path reason

#### Scenario: Shared artifact is retained

- **WHEN** an artifact candidate is still referenced by a row outside the deletion scope
- **AND** deletion is requested with `delete_artifacts=true`
- **THEN** the shared path remains unchanged
- **AND** the response lists it as skipped with a shared-reference reason

#### Scenario: Untracked matching directory is retained

- **WHEN** a directory matches an affected challenge id by name but no deleted row directly references its path
- **THEN** explicit artifact deletion leaves that directory unchanged
- **AND** the response reports it as skipped/unowned rather than inferring ownership

### Requirement: Database and queue cleanup fail safely

The deletion service SHALL quarantine filesystem entries by same-filesystem
atomic rename before database commit. A validation or commit failure SHALL roll
back database changes and restore quarantined entries. A failure to remove a
quarantine after a successful commit SHALL be returned as a cleanup warning,
classified as `quarantined`, and the leftover SHALL remain outside
worker-scanned queue directories. Each quarantine operation SHALL write an
atomic manifest containing the root resource identity and source/destination
paths before mutation. Startup and before-delete recovery SHALL restore entries
when the root still exists, purge entries when the root deletion committed, and
leave ambiguous entries quarantined with a warning rather than guessing.

#### Scenario: Database commit fails after queue withdrawal

- **WHEN** a queued shard has been quarantined and the database commit fails
- **THEN** the database row remains
- **AND** the shard is restored to its original location

#### Scenario: Final filesystem removal fails

- **WHEN** the database deletion commits but a quarantined entry cannot be removed
- **THEN** the API still reports the resource as deleted
- **AND** it reports the entry under `quarantined`, not `deleted`
- **AND** it returns a cleanup warning naming the quarantined entry
- **AND** no worker-visible queue file is recreated

#### Scenario: Process exits before database commit

- **WHEN** the process exits after quarantine but before committing root deletion
- **THEN** PostgreSQL rolls back and the root resource remains
- **AND** recovery uses the manifest to restore each quarantined entry

#### Scenario: Process exits after database commit

- **WHEN** the process exits after root deletion commits but before quarantine purge
- **THEN** recovery observes the root is absent and purges its quarantined entries
- **AND** no worker-visible queue file is restored

### Requirement: Parent design-task status is recomputed after attempt deletion

After an individual build attempt is deleted, the system SHALL derive the
parent design task status from the highest remaining `attempt_no` in the same
transaction: `queued` or `running` maps to `building`, `succeeded` maps to
`built`, `failed` or `lost` maps to `build_failed`, and no remaining attempt
maps to `designed`.

#### Scenario: Delete the only failed attempt

- **WHEN** the only build attempt of a `build_failed` design task is deleted
- **THEN** the design task returns to `designed`

#### Scenario: Delete an older sibling

- **WHEN** an older failed build attempt is deleted while the latest remaining attempt is succeeded
- **THEN** the design task remains `built`

### Requirement: Restrictive structured-design references are deleted in order

The service SHALL delete `challenge_designs` before the referenced
`design_attempts` when deleting a Design Task directly or through a request cascade, then
delete the task/root. It SHALL perform this ordering in the same PostgreSQL
transaction as the remaining relational and progress cleanup.

#### Scenario: Design with restrictive attempt reference is deleted

- **GIVEN** a Design Task has a Challenge Design whose `design_attempt_id` foreign key uses `ON DELETE RESTRICT`
- **WHEN** the task is deleted
- **THEN** its Challenge Design is deleted before its Design Attempt
- **AND** the transaction completes without a foreign-key violation

### Requirement: Delete endpoints expose consistent contracts

The system SHALL expose:

- `DELETE /api/research/requests/{id}`;
- `DELETE /api/design-tasks/{id}`; and
- `DELETE /api/build-attempts/{id}`.

Each endpoint SHALL accept the boolean query parameter
`delete_artifacts`, defaulting to `false`. A successful response SHALL identify
the deleted resource and report artifact paths as `deleted`, `retained`,
`skipped`, or `quarantined`, plus cleanup warnings. Unknown or malformed identifiers SHALL return
`404`; active-state conflicts SHALL return `409` with an actionable detail.

#### Scenario: Default API call retains artifacts

- **WHEN** a client sends `DELETE /api/design-tasks/{id}` without a query parameter
- **THEN** the server behaves as `delete_artifacts=false`
- **AND** the success payload reports retained artifacts

#### Scenario: Explicit API call deletes artifacts

- **WHEN** a client sends `DELETE /api/research/requests/{id}?delete_artifacts=true`
- **THEN** the server applies explicit artifact deletion to the full request cascade
- **AND** reports each deletion or skip outcome

#### Scenario: Unknown resource is not found

- **WHEN** a client deletes a well-formed identifier that does not exist
- **THEN** the endpoint returns `404 Not Found`
- **AND** no filesystem cleanup occurs

### Requirement: Dashboard deletion requires explicit confirmation

The request, Design Task, and Build Attempt list and detail views SHALL expose a
Delete action. Activating it SHALL open a confirmation dialog that names the
resource, describes cascading child deletion, and contains an unchecked
`同时删除产物` checkbox. Confirm SHALL call the matching endpoint with the
checkbox value; cancel SHALL perform no deletion request.

#### Scenario: Default dashboard confirmation preserves artifacts

- **WHEN** an operator opens the dialog, leaves `同时删除产物` unchecked, and confirms
- **THEN** the dashboard sends `delete_artifacts=false`
- **AND** after success it refreshes the list or returns from detail to the list

#### Scenario: Operator explicitly deletes artifacts

- **WHEN** an operator checks `同时删除产物` and confirms
- **THEN** the dashboard sends `delete_artifacts=true`
- **AND** displays any skipped paths or cleanup warnings from the response

#### Scenario: Operator cancels deletion

- **WHEN** an operator cancels the confirmation dialog
- **THEN** the dashboard sends no DELETE request
- **AND** the current view and resource remain unchanged
