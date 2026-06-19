## Worker Pool Proposal Assessment

This assessment records twelve review rounds against the current implementation
and the observed Web-to-Pwn execution failure.

### Round 1: Scope accuracy

**Finding:** The original change implemented an agent registry but explicitly
deferred the supervisor, so it did not yet define a worker pool.

**Remediation:** Add a bounded single-host supervisor, slots, leadership, and
readiness while retaining cross-host scheduling as a non-goal.

### Round 2: Queue/category authorization

**Finding:** Worker/profile names are labels; current `ShardQueue.claim` selects
the first filename globally. Capabilities alone are ineffective if the final
claim path remains unconstrained.

**Remediation:** Require project capabilities plus exact build-attempt dispatch;
make constrained dispatch a hard prerequisite.

### Round 3: Conversation versus workspace isolation

**Finding:** The failing Hermes session had fresh conversation history, but its
persistent `task=default` Docker workspace was reused. Profile separation does
not prevent stale task files.

**Remediation:** Specify a unique ephemeral terminal sandbox per execution and
forbid shared default task workspaces.

### Round 4: Runtime path correctness

**Finding:** Prompts contain host absolute paths while the Docker runtime does
not mount the host cwd. The agent failed to read the leased shard.

**Remediation:** Materialize runtime input bundles, render container-visible
paths, and preflight those paths before model invocation.

### Round 5: Model behavior after missing input

**Finding:** After input failure, an autonomous model can search for nearby
files and continue an unrelated task. Prompt wording cannot safely prevent it.

**Remediation:** Fail before starting Hermes when required input is unavailable;
never let the model choose substitute shards.

### Round 6: Filesystem write boundary

**Finding:** Direct repository/output-root access allows cross-category or
source-tree mutation and makes rollback unreliable.

**Remediation:** Give each execution one staging output root, keep production
trees unreachable, and publish only after host-side allowlist validation.

### Round 7: Ownership and stale workers

**Finding:** Atomic file rename prevents simultaneous claim but cannot fence an
old process after timeout/recovery. A late process can still write or report
completion.

**Remediation:** Add DB leases, heartbeats, rotating fencing tokens, and token
checks on every terminal mutation and publication.

### Round 8: Concurrency accounting

**Finding:** Storing `max_concurrency` without slot reservation or atomic claim
allows oversubscription and gives the dashboard misleading controls.

**Remediation:** Model slots explicitly and reserve capacity transactionally
under both per-agent and global limits.

### Round 9: Lifecycle and health

**Finding:** Treating agents without heartbeats as offline makes a newly created
enabled agent ineligible to start. Draining and process health were underspecified.

**Remediation:** Add `stopped`; derive health separately from control state;
define drain, shutdown, backoff, and missed-heartbeat behavior.

### Round 10: Supervisor multiplicity

**Finding:** A local supervisor started independently by each dashboard process
can duplicate slots and claims.

**Remediation:** Require singleton supervisor leadership through PostgreSQL
coordination and expose standby/leader state.

### Round 11: Audit and diagnosis

**Finding:** Agent id/name/profile alone cannot explain which sandbox policy,
input, output, or token generation produced an artifact.

**Remediation:** Store immutable execution snapshots and manifest hashes with
structured terminal classifications and log locations.

### Round 12: Partial rollout safety

**Finding:** Enabling registry-backed pool actions before dispatch, isolation,
fencing, and publication are all ready recreates the current unsafe path under
new names.

**Remediation:** Add server-derived readiness and fail closed; use a phased
one-slot rollout before bounded concurrency.

## Recommendation

Adopt the revised single-host supervised pool with exact-attempt dispatch,
ephemeral per-execution sandboxing, database fencing, and staged atomic
publication. Do not adopt a profile-only pool or a pool that invokes the current
global `HermesRunner` directly from concurrent slots.
