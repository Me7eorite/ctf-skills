# Build execution workspaces

Build Hermes invocations run from an isolated project-owned directory under
`work/executions/<workspace_id>/`. The prompt only references `./input`,
`./references`, `./output`, `./logs`, and `./bin/progress`.

## Validation and publication boundary

Host validation is bound to the exact claimed directories under the active
execution's `output/challenges/<category>/` tree. Build execution never looks up
the candidate by challenge id in canonical `work/challenges/` storage.

The lifecycle is:

1. Hermes creates or repairs files in the active execution workspace.
2. The publisher allowlist resolves exactly one directory per claimed id without
   changing canonical storage.
3. Host contract and solver validation run against those resolved paths.
4. Failed rounds append diagnostics under `state/validation-history.json`; repair
   continues in the same active workspace.
5. The runner rechecks the validated output hash and publishes once, only after
   every challenge passes.

`attempts/iter-NNN` is created only when a new execution replaces `current`; a
validation-repair round does not create another directory.

## One-time profile setup

```bash
hermes profile create cf-web
hermes profile create cf-pwn
hermes profile create cf-re
hermes -p cf-web config set terminal.cwd "."
hermes -p cf-pwn config set terminal.cwd "."
hermes -p cf-re config set terminal.cwd "."
```

The runner does not mutate operator-owned profile configuration.

## Pwn backend safety

Pwn build agents are not allowed to run on the local terminal backend by
default. They must use an isolated Docker or VM terminal backend because pwn
generation naturally runs compilers, patchers, shells, exploit scripts, and
debuggers; a mistaken local command can damage host system tools such as
`/bin/ls` or `/bin/bash`.

The workspace preflight fails closed for pwn when the backend is `local` or
cannot be determined. `ALLOW_UNSAFE_LOCAL_PWN=1` bypasses this only for a
disposable host or throwaway VM.

The safety check is profile-aware: a Docker backend configured on `cf-pwn`
is enough even when the project/default backend remains local.

## Docker terminal backend

The local backend can read the workspace directly. A Docker terminal backend
must mount the host project's `work/executions/` at the same in-container path
with read/write access, equivalent to:

```text
./work/executions:/work/executions:rw
```

The mount must preserve the path because the subprocess starts with its `cwd`
set to the host workspace. Workspace references are copied, not symlinked, so
no repository-wide mount is required. The image must provide `python3` for
`./bin/progress`.

## Timeouts

| Shard | Timeout |
| --- | ---: |
| Re | 1800s |
| Web | 2700s |
| Pwn | 3600s |
| Pwn containing any expert challenge | 5400s |

Operational precedence is `--timeout` > `HERMES_TIMEOUT` > shard policy.

## Controlled rollout smoke test

Before bulk execution, submit one queued Web attempt from the Web UI. Confirm:

1. The start response displays `2700s (shard_policy)` unless overridden.
2. `work/executions/<build_attempt_id>/input/shard.json` is readable.
3. `logs/hermes.log` shows workspace `cwd` and argv containing `-p cf-web`.
4. Hermes reads `./input/shard.json`, reports through `./bin/progress`, and
   writes below `./output/challenges/web/`.
5. Failed validation leaves `work/challenges/web/` unchanged; only claimed,
   validated output reaches it once, and the legacy report
   appears under `work/reports/`.

Stop rollout on any failure. Host preflight cannot prove Docker mount
visibility; this controlled execution is the required visibility check.
