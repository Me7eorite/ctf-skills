# Build execution workspaces

Build Hermes invocations run from an isolated project-owned directory under
`work/executions/<workspace_id>/`. The prompt only references `./input`,
`./references`, `./output`, `./logs`, and `./bin/progress`.

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
5. Only claimed output reaches `work/challenges/web/`, and the legacy report
   appears under `work/reports/`.

Stop rollout on any failure. Host preflight cannot prove Docker mount
visibility; this controlled execution is the required visibility check.
