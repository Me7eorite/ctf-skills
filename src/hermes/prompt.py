"""Hermes 提示词渲染。"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.jsonio import read_json
from core.paths import ProjectPaths
from domain.design.technique_taxonomy import render_family_vocabulary
from domain.research import GenerationRequest
from domain.resume import ShardResumePlan
from hermes.process import sanitize_prompt_text

RESEARCH_PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "prompts" / "research_prompt.md"
SHARED_GENERATION_STRATEGY_PATH = Path(__file__).resolve().parents[2] / "prompts" / "shared_generation_strategy.md"


def render_validation_repair_prompt(
    *,
    attempt: int,
    max_attempts: int,
    validation_results: list[dict],
    prior_contract_errors: Sequence[str] = (),
    debug_context: Mapping[str, object] | None = None,
) -> str:
    """Render a focused prompt for repairing host-observed validation failures.

    ``prior_contract_errors`` carries the union of every contract violation seen
    in earlier repair attempts for this shard. Surfacing them as an explicit
    non-regression list stops the agent from trading one host-enforced rule for
    another across rounds (the classic "fix the hardcoded flag by reading it from
    metadata.json instead" whack-a-mole).
    """
    diagnostics = []
    for result in validation_results:
        if result.get("solve_status") == "passed":
            continue
        diagnostics.append(
            {
                key: result[key]
                for key in (
                    "challenge_id",
                    "validation_status",
                    "validation_error",
                    "validation_returncode",
                    "validation_stdout_tail",
                    "validation_stderr_tail",
                    "validation_contract_errors",
                    "validation_failure_details",
                    "validation_failure_class",
                    "validation_failure_signature",
                    "validation_elapsed",
                    "validation_command",
                    "validation_final_flag_candidate",
                    "validation_diagnostic_unavailable",
                    "missing_solver_output",
                    "classification_conflicts",
                    "batch_degraded",
                    "pause_pwn_lane",
                    "pwn_failure_stage",
                    "pwn_debug_failure_stage",
                    "pwn_debug_result_path",
                    "pwn_debug_result_sha256",
                    "pwn_debug_actionable_summary",
                    "pwn_debug_tcp_probe_status",
                    "pwn_debug_tcp_probe_matched_token",
                    "pwn_debug_tcp_probe_raw_output_tail",
                    "pwn_source_protocol_token",
                    "pwn_source_protocol_token_source",
                    "pwn_debug_status",
                    "pwn_debug_error",
                    "failure_kind",
                    "failure_hint",
                    "failed_step",
                )
                if result.get(key) not in (None, "", [])
            }
        )
    rendered = json.dumps(diagnostics, ensure_ascii=False, indent=2)
    repair_plan = _render_repair_plan(diagnostics)
    non_regression = _render_non_regression_section(prior_contract_errors)
    context_section = _render_validation_debug_context(debug_context)
    governed_context = _render_governed_repair_context(diagnostics, debug_context)
    prompt = f"""You are continuing CTF challenge implementation after authoritative host validation failed.

Validation debug round {attempt} of {max_attempts}. Work only inside the existing claimed challenge
directories under `./output/challenges`. Read `./input/shard.json` and inspect the current
source, Docker/Compose files, built artifact metadata, `validate.sh`, and `writenup/exp.py`.

Inherited build context:
{context_section}

Governed repair context:
```json
{governed_context}
```

Host validation diagnostics:
```json
{rendered}
```

Focused debug plan:
{repair_plan}

How to read `validation_error`:
- `"contract_failed"` + `validation_error` starting with `"build evidence incomplete: metadata.<FIELD> missing"`
  means the named field is absent from `metadata.json`. You MUST edit `metadata.json`
  directly to add or correct that field. Do NOT create or modify
  `build-evidence.json`, `evidence.json`, or any other side-car file — the host validator
  only reads `metadata.json` and the on-disk artifacts.
- `"build evidence incomplete: docker image '<NAME>' not present on host"` means the
  image is missing or differs from `metadata.docker_image`. Do NOT run Docker yourself;
  fix `metadata.docker_image`, `deploy/Dockerfile`, and `deploy/docker-compose.yml`
  so the host runner can rebuild that exact image tag. For Pwn, the host runner
  owns the final tag and rewrites it to
  `pwn-{{workspace_id[:6]}}-{{challenge_slug}}:latest`; do not change it back to a
  generic value such as `pwn-canary:latest` or `pwn-demo:latest`.
- `"build evidence incomplete: metadata.artifact_sha256 does not match artifact contents"`
  means the file at `metadata.artifact` was rebuilt without updating its `artifact_sha256`.
  Recompute the SHA-256 and write it back to `metadata.json`.
- `"unnecessary_intended_path"` means the host found a shortcut that recovers the
  flag without the intended technique. Use `validation_error` to identify the shortcut.
  For `re`, remove plaintext flag bytes from delivered files in `attachments/` and
  `dist/`; local organizer source under `src/` may contain the flag if it is not
  shipped. Do not make the binary print the flag when run with no input. Store only
  encoded/encrypted material in the player artifact and update `writenup/exp.py` so
  it derives the flag through the declared reversing technique.
  For `web`/`pwn`, keep the required literal Compose shape
  `environment:` then `- FLAG=<metadata.flag>` in `deploy/docker-compose.yml`;
  that organizer deployment file may contain the plaintext flag. Remove
  plaintext only from player-visible artifacts such as `attachments/`, and
  never make `validate.sh` or `writenup/exp.py` read the flag from
  compose/metadata/challenge files.

Pwn repair evidence rules:
- For Pwn failures, first locate the failing `validate.sh` stage:
  `start_compose`, `wait_container`, `derive_protocol_token`,
  `wait_app_ready`, `run_solver`, `check_flag`, or `diagnostics`. Do not start
  by guessing payload bytes.
- Readiness tokens come from visible challenge evidence, not black-box guessing.
  Prefer `pwn_source_protocol_token` when diagnostics include it, or inspect
  `deploy/src/*.c`, `src/*`, README, writeup, and design files. Exact
  full-token matching is not mandatory: if source says `Perfect Menu:`,
  waiting for a stable substring such as `Perfect` is enough to prove
  application response.
- `nc` is allowed for connectivity testing, but `nc -z` is only port-open
  evidence. It is not application readiness. `head -c`, `dd count=`, and
  EOF-based TCP reads are validate-wrapper bugs for interactive services.
- `pwn_debug` is auxiliary failure evidence. Use it when diagnostics conflict
  (for example validate.sh says not ready but pwn_debug read a token), but do
  not make it the main validation path.
- Solver repairs must prove that service readiness has already been established
  or fix the wrapper first; then show leak values are stable, canary/libc/PIE/
  gadget sources match the current `metadata.artifact`, payload offsets come
  from disassembly or dynamic verification, and `writenup/exp.py` emits a flag
  candidate.
- Structured diagnostics include `service_ready`, `exploit_started`,
  `exploit_exit_code`, solver stdout/stderr tails, `pwn_debug_failure_stage`,
  `validation_failure_class`, and `classification_conflicts` when available.
  If service is ready and the exploit started, do not keep repairing readiness
  unless a managed pwn-debug run proves the service is unavailable.
- If service readiness read the token, later EOF, BrokenPipe, missing flag,
  failed leak, or payload failure is solver/protocol work. The service is
  already ready; do not repair startup.
- If the wrapper probe fails but the container is Up and the port connects, do
  not stop with only a readiness claim. Prefer to run `exp.py` anyway, capture
  solver stdout/stderr/exit code, and then decide whether evidence points to
  wrapper, service startup, binary path, or solver protocol.
- If `classification_conflicts` says validate.sh reported readiness failure but
  canonical pwn-debug TCP probe read a prompt/menu token, repair `validate.sh`
  readiness/capture first. The service is reachable; do not spend the round on
  xinetd, chroot, container startup, or exploit constants until the wrapper
  reports that evidence correctly.
- If `pwn_failure_stage` is `readiness`, fix service protocol, host/port,
  banner/menu probing, logs, and readiness diagnostics first. Do not turn this
  into a SHA-only or metadata-only repair.
- If `pwn_failure_stage` is `payload_control_flow`, assume readiness/leak work
  is already past the first hurdle unless pwn-debug explicitly shows unstable
  leaks; focus on final control flow, stack alignment, payload layout, and flag
  read path.
- If the declared design is SROP, ORW, ret2libc, or GOT overwrite, preserve that
  technique. Do not replace it with `read_flag()`, `win()`, or ret2win shortcuts.
{_VALIDATION_CONTRACT_CHECKLIST}{non_regression}
This is a validation-debug continuation, not a broad redesign. First understand
the inherited context above and the current files before editing. The host runner
has already performed the controlled Docker build. You may run `./validate.sh`
from the specific challenge root to reproduce runtime/solver failures and iterate
on `writenup/exp.py`, readiness checks, and challenge files. Do not run `docker build`,
`docker-compose build`, network-fetching dependency installs, Docker prune commands,
or volume-destructive cleanup. If Docker Compose is needed, use it only through this
challenge's `validate.sh` or bounded diagnostics for this challenge. The host runner
will still perform the authoritative validation after you return. Do not hardcode or
merely echo the expected flag in the exploit. The exploit must recover it through the
intended vulnerability. Do not write `validate/*` progress events.
Update documentation and metadata when the repaired implementation changes them.

Directory discipline:
- At the start of every terminal command that changes directories, anchor the
  execution workspace first: `WORKSPACE_ROOT="$(pwd)"` when `./input/shard.json`
  exists, or walk upward until `input/shard.json` is found.
- This validation-debug continuation must not call `./bin/progress` at all.
  The runner owns all validation-debug and complete progress events. A bare
  `./bin/progress` call is always wrong in this prompt.
- If the current directory already contains `metadata.json`, `validate.sh`, and
  `writenup/exp.py`, it is the challenge root; do not run
  `cd ./output/challenges/...` from there.
- To enter a challenge from the workspace root, use the exact path reported in
  `logs/report.json` or discover it with `find ./output/challenges -name metadata.json`;
  never concatenate `./output/challenges/...` onto an already-entered challenge root.
- The same path rule applies to file tools (`read_file`, `write_file`, patch):
  if you are already in a challenge root, read `deploy/Dockerfile`, not
  `./output/challenges/<category>/<id>-<slug>/deploy/Dockerfile`; if you need a
  workspace-root path, first verify `./input/shard.json` exists from that root.
- Do not use absolute synthetic paths such as `/output/...`, `/attachments/...`,
  or `/writenup/...` in write tools. A real absolute path printed by `pwd -P`
  or `find` is allowed and preferred for file tools when the tool cwd may have
  drifted; do not invent or concatenate absolute paths from memory.
- Before every `read_file`, `write_file`, or patch on a challenge file, decide
  which root the path is relative to. If the path starts with
  `./output/challenges/`, first verify the current tool cwd is the workspace
  root; otherwise use the real absolute challenge path or a path relative to the
  entered challenge root such as `writenup/wp.md`.
- Before reading optional files such as `deploy/src/Makefile`, `attachments/*`,
  or `writenup/pwn_debug_report.json`, list the containing directory first.
  If an optional file is missing, create or adapt the expected file instead of
  retrying the same nonexistent path.

Terminal tool usage:
- Do not use `eval`, ad-hoc quoted command strings, or chained
  `cd ./output/challenges/...` guesses. The terminal may still be in a prior
  challenge root from an earlier command.
- Do not run any terminal command that contains `cd ./output/challenges/...`
  unless it first proves it is at the workspace root with
  `test -f ./input/shard.json`. The common failure mode is being in
  `./output/challenges/<category>/<id>-<slug>/deploy/src` and then running
  `cd ./output/challenges/...`, which looks for a nested `output/` directory
  that does not exist.
- For terminal commands that inspect or edit challenge files, use this shape
  and replace only the category and challenge id:
  ```bash
  WORKSPACE_ROOT="$PWD"
  while [ ! -f "$WORKSPACE_ROOT/input/shard.json" ] && [ "$WORKSPACE_ROOT" != "/" ]; do
    WORKSPACE_ROOT="$(dirname "$WORKSPACE_ROOT")"
  done
  test -f "$WORKSPACE_ROOT/input/shard.json" || exit 1
  CHAL_ROOT="$(find "$WORKSPACE_ROOT/output/challenges/<category>" \
    -mindepth 1 -maxdepth 1 -type d -name '<challenge-id>-*' | head -n 1)"
  test -n "$CHAL_ROOT" || exit 1
  cd "$CHAL_ROOT" || exit 1
  ```
- Do not call `$WORKSPACE_ROOT/bin/progress` in this validation-debug
  continuation. Focus on editing files and bounded diagnostics; the host runner
  records validation progress after you return.
- If a command needs complex JSON, sed replacement, or long quoted text, prefer
  the file write/patch tool instead of terminal. Unbalanced quotes in terminal
  commands waste repair budget and do not count as progress.

Pwn exploit debugging acceleration:
- Prefer pwntools for Pwn solvers. Use
  `context(os='linux', arch='amd64', log_level=os.environ.get('PWNLIB_LOG_LEVEL', 'info'))`
  for amd64 Linux targets, and temporarily run with `PWNLIB_LOG_LEVEL=debug` when
  diagnosing EOF, prompt mismatches, leaks, offsets, or payload bytes.
- For Pwn xinetd/chroot services, first confirm the service itself reaches its
  menu or banner. `validate.sh` readiness must open a fresh TCP connection and
  read an application prompt such as `Choice:`; a bare `nc -z` port check is too
  weak and can race xinetd startup, causing the exploit to receive EOF.
  Do not use fixed byte reads such as `head -c 200 < /dev/tcp/...`; interactive
  services often keep the connection open, so that pattern waits for EOF and
  causes false readiness timeouts. Use a short socket timeout and succeed as
  soon as a stable prompt/banner token is observed. On failure, print `$COMPOSE ps`,
  `$COMPOSE logs --no-color --tail=120`, CHAL_HOST, CHAL_PORT, compose project,
  container/image identity, and the raw TCP probe tail to stderr.
  Do not put `nc "$CHAL_HOST" "$CHAL_PORT"` behind `bash -c` unless both
  variables are exported first. Prefer a bounded socket read probe that treats
  received banner/prompt bytes as evidence even if the interactive connection
  remains open; do not clear captured output merely because a timeout wrapper
  exits non-zero.
- If the service uses `chroot /home/ctf` and startup writes the host-container
  file `/home/ctf/flag`, the vulnerable program must open `/flag` from inside
  the chroot. A source path like `/home/ctf/flag` resolves to
  `/home/ctf/home/ctf/flag` after chroot and will make ret2win appear broken.
- In Pwn assembly sources, every path or command string passed to syscall/libc
  (`open`, `openat`, `execve`, `system`, etc.) must be NUL terminated: use
  `.asciz`, `.string`, or an explicit `\0`. Never emit `.ascii "/flag"`.
- Load the local binary only from the delivered artifact by resolving the
  challenge root from the script location, for example
  `challenge_root = Path(__file__).resolve().parents[1]` and
  `binary_path = challenge_root / metadata_artifact`, so symbols, PLT/GOT,
  architecture assumptions, gadgets, and offsets come from the final host-built
  ELF. After host build synchronizes the image's `/home/ctf/<binary>` into
  `attachments/<binary>`, do not trust `deploy/src/<binary>`, local rebuilds,
  old report offsets, old sha values, or source-level symbol addresses. Prefer
  dynamic symbol discovery from `metadata.artifact` plus
  `readelf`/`objdump` evidence over hardcoded old constants. Record the
  artifact sha used by `writenup/exp.py` exactly as `BINARY_SHA256`; it must
  equal both `metadata.artifact_sha256` and the SHA-256 of the file named by
  `metadata.artifact`.
  If `validate.sh` runs from `writenup`, paths like `./attachments/<binary>` or
  `attachments/<binary>` inside `exp.py` are wrong because they resolve under
  `writenup/`; do not use bare `ELF('./attachments/<binary>', checksec=False)`
  in that case. Anchor through `Path(__file__).resolve().parents[1]` instead.
  If `MAIN_OFFSET`, `WIN_OFFSET`, or ROP gadget constants conflict with
  `readelf`/`objdump` output from `metadata.artifact`, treat the exploit as
  stale and regenerate it instead of patching around the mismatch.
- Add or preserve a local mode such as `LOCAL=1 python3 writenup/exp.py` that
  uses `process([binary_path])` for quick menu/offset smoke tests. The default
  validation path must still connect with
  `remote(os.environ['CHAL_HOST'], int(os.environ['CHAL_PORT']))`. If pwntools
  is unavailable, keep a standard-library `socket.create_connection` fallback
  that can complete the same remote menu interaction. Track whether the banner
  or menu bytes were already consumed so the exploit does not call `recvuntil`
  for the same prompt twice or blindly send after a recv timeout.
- Before trusting any local `process()`/gdb smoke test, align it to the same
  runtime as the service: use the final `metadata.artifact` binary under
  `attachments/`, prefer the shipped `ld-*`/`ld-linux*` loader and `libc.so*`
  from `attachments/` or the container, and record those paths in the solver
  comments or debug report. If a matching loader/libc cannot be found, mark the
  local result as only a smoke test and continue to remote validation rather
  than calling the challenge solved. These are debug aids only; they must not
  replace the default remote validation path or become the source of truth for
  the delivered exploit.
- Every local binary, pwntools `process()`, subprocess, and gdb run must be
  bounded and non-interactive. Never run bare `./<binary>` or a menu-driven
  ELF without input in headless mode. Use patterns like
  `timeout 5s ./<binary> < input.txt`, pwntools recv/send calls with short
  timeouts, or `subprocess.run([...], input=..., timeout=5)`. Headless gdb
  runs must use `timeout`, `-batch` or explicit `-ex quit`, and deterministic
  input. If a local smoke test cannot be bounded, skip it and explain why in
  `logs/report.json` instead of risking a hung worker.
- When leaking stack canaries through `%n$p`, scan a broad bounded range and
  identify canary-like values by stability across multiple fresh connections
  and low byte `0x00`. Exclude NULL/small integers and obvious stack/libc/PIE
  addresses such as `0x7fff...`, `0x7f...`, and `0x55...`; a leaked pointer
  whose low byte is 00 is not a canary. Do not reject values merely because they
  are greater than `2^48`; amd64 canaries commonly use the upper seven bytes
  and will often exceed that threshold.
- Never fall back to guessed stack addresses such as `0x7fffffffxxxx` without a
  live leak that proves the address for the current process. Establish a
  reliable stack/PIE/libc leak first or switch to a provably valid exploit chain.
- For fork-per-connection canary brute force, print byte-level progress
  diagnostics (`byte_index`, candidate byte, attempt count), set short
  connect/recv timeouts for every attempt, fail before the validation budget is
  exhausted with the current position, and improve the crash oracle instead of
  blindly waiting on slow sockets.
- For ROP/ret2libc/PIE Pwn tasks, follow a structured debug loop before
  finishing the exploit: identify mitigations with `checksec`/`file`, compute
  the exact overflow offset with cyclic/core/headless gdb, discover gadgets from
  the actual ELF/libc, leak a GOT/libc or PIE pointer when needed, verify
  computed bases are plausible and page-aligned, add an amd64 `ret` stack
  alignment gadget when libc calls crash around `movaps`, and retest against
  the container service path. Do not ship guessed gadget, libc, PIE, or offset
  constants.
- Write `writenup/pwn_debug_report.json` for Pwn challenges when the solve path
  needed debugging or when the exploit is non-trivial. Keep it bounded and
  organizer-facing; include keys such as `checksec`, `binary`, `libc`,
  `prompt_probe`, `offset`, `leaks`, `bases`, `gadgets`, `local_result`,
  `remote_result`, `failure_code`, and `notes`. This report is used by later
  validation-debug/repair rounds so they inherit context instead of starting
  from scratch.
- A Pwn debug report is trustworthy only when
  `writenup/pwn_debug_report.json.binary.sha256` equals
  `metadata.artifact_sha256`. If the inherited context marks the report stale,
  ignore all offsets/gadgets in it and rerun `readelf`, `objdump`, and
  `checksec` against the file named by `metadata.artifact` before editing
  `writenup/exp.py`.
- Before writing files or calling `./bin/progress`, verify the current directory
  is the execution workspace or the exact challenge root. Do not write absolute
  paths such as `/output/...`, `/attachments/...`, or `/writenup/exp.py`;
  recover with `pwd` and `cd` back to the workspace/challenge root when a debug
  command changes directories.
- Do not repeatedly `chmod` restored files under `attachments/` just to make
  local debug work. Compile or copy player binaries with the intended executable
  bit, and prefer `python3 writenup/exp.py` or bounded tooling from the challenge
  root when diagnosing solver logic.
- Discover local Pwn tooling before guessing: use bounded probes such as
  `command -v gdb checksec readelf objdump ROPgadget ropper one_gadget`. If
  present, use `checksec --file <binary>`, `readelf -sW`, `objdump -d`, gadget
  tools, and headless `gdb -q <binary> -ex 'set pagination off' -ex 'run' -ex 'bt' -ex 'info registers' -ex 'quit'`
  or a short gdb command file to confirm crashes, offsets, stack layout, canary
  behavior, and addresses. If pwndbg/gef is installed, use its helpers such as
  checksec, cyclic, telescope, and vmmap. Do not make `validate.sh` depend on
  interactive gdb.

Before you finish, self-check every challenge you touched with real file searches:
- Run a search equivalent to
  `grep -R "metadata.json\\|challenge.yml\\|docker-compose\\|flag{{" validate.sh writenup/exp.py`.
- For `re`, any `metadata.json` or `challenge.yml` hit in `validate.sh` or `writenup/exp.py`
  is still a failure. `validate.sh` must validate by running the solver and extracting a
  `flag{{...}}` token from solver output, not by reading organizer answers.
- For `re`, confirm `validate.sh` or `writenup/exp.py` passes/opens the artifact under
  `attachments/`.
A repair that fixes one diagnostic by violating a different rule above still fails host validation.
"""
    return sanitize_prompt_text(prompt)


def _render_validation_debug_context(context: Mapping[str, object] | None) -> str:
    if not context:
        return (
            "- No inherited context was supplied; read `./input/shard.json`, "
            "`./logs/report.json`, and the claimed challenge directory before editing."
        )
    evidence_blocks: list[str] = []
    pwn_evidence = context.get("pwn_final_artifact_evidence")
    if isinstance(pwn_evidence, Mapping):
        for item in pwn_evidence.values():
            if isinstance(item, Mapping) and isinstance(item.get("instruction"), str):
                evidence_blocks.append(item["instruction"])
    rendered_json = "```json\n" + json.dumps(
        _compact_prompt_value(context),
        ensure_ascii=False,
        indent=2,
    ) + "\n```"
    if evidence_blocks:
        return "\n\n".join([*evidence_blocks, rendered_json])
    return rendered_json


def _compact_prompt_value(value: object, *, depth: int = 0) -> object:
    if depth > 5:
        return "..."
    if isinstance(value, str):
        return value if len(value) <= 1600 else value[:1600] + "...[truncated]"
    if isinstance(value, Mapping):
        return {
            str(key): _compact_prompt_value(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        compacted = [
            _compact_prompt_value(item, depth=depth + 1)
            for item in list(value)[:40]
        ]
        if len(value) > 40:
            compacted.append(f"...[{len(value) - 40} more]")
        return compacted
    return value


def _render_governed_repair_context(
    diagnostics: Sequence[Mapping[str, object]],
    debug_context: Mapping[str, object] | None,
) -> str:
    current = next((item for item in diagnostics if item), {})
    first_failure: dict[str, object] = {}
    history_tail: object = []
    pwn_debug_results: object = {}
    if isinstance(debug_context, Mapping):
        first_failure = _summarize_round(debug_context.get("first_validation_failure"))
        history_tail = debug_context.get("validation_history_tail") or []
        pwn_debug_results = debug_context.get("pwn_debug_results") or {}
    payload = {
        "root_validation_failure": first_failure or _summarize_diagnostic(current),
        "current_blocker": _summarize_diagnostic(current),
        "classification_conflicts": current.get("classification_conflicts") or [],
        "evidence_bundle": {
            "validate_stdout_tail": current.get("validation_stdout_tail") or "(unavailable)",
            "validate_stderr_tail": current.get("validation_stderr_tail") or "(unavailable)",
            "solver_stdout_tail": current.get("solver_stdout_tail") or "(unavailable)",
            "solver_stderr_tail": current.get("solver_stderr_tail") or "(unavailable)",
            "pwn_debug_tcp_probe": {
                "status": current.get("pwn_debug_tcp_probe_status") or "(unavailable)",
                "matched_token": current.get("pwn_debug_tcp_probe_matched_token") or "(unavailable)",
                "raw_output_tail": current.get("pwn_debug_tcp_probe_raw_output_tail") or "(unavailable)",
            },
            "pwn_source_protocol_token": current.get("pwn_source_protocol_token") or "(unavailable)",
            "pwn_source_protocol_token_source": current.get("pwn_source_protocol_token_source")
            or "(unavailable)",
            "final_flag_candidate": current.get("validation_final_flag_candidate") or "(unavailable)",
            "validation_diagnostic_unavailable": current.get("validation_diagnostic_unavailable") or [],
            "pwn_debug_results": pwn_debug_results,
        },
        "validation_history_tail": history_tail,
    }
    return json.dumps(_compact_prompt_value(payload), ensure_ascii=False, indent=2)


def _summarize_round(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    results = value.get("results")
    if isinstance(results, Sequence) and not isinstance(results, (str, bytes, bytearray)):
        for result in results:
            if isinstance(result, Mapping) and result.get("solve_status") == "failed":
                return _summarize_diagnostic(result)
    return _summarize_diagnostic(value)


def _summarize_diagnostic(value: Mapping[str, object]) -> dict[str, object]:
    details = value.get("validation_failure_details")
    code = None
    if isinstance(details, Sequence) and not isinstance(details, (str, bytes, bytearray)):
        for detail in details:
            if isinstance(detail, Mapping) and detail.get("code"):
                code = detail.get("code")
                break
    return {
        key: item
        for key, item in {
            "challenge_id": value.get("challenge_id"),
            "validation_status": value.get("validation_status"),
            "validation_failure_class": value.get("validation_failure_class"),
            "validation_failure_signature": value.get("validation_failure_signature"),
            "pwn_failure_stage": value.get("pwn_failure_stage"),
            "diagnostic_code": code,
            "validation_error": value.get("validation_error"),
            "repair_result": value.get("repair_result"),
            "blocked_reason": value.get("blocked_reason"),
            "expected_next_action": value.get("expected_next_action"),
        }.items()
        if item not in (None, "", [])
    }


def _render_repair_plan(diagnostics: Sequence[Mapping[str, object]]) -> str:
    if not diagnostics:
        return (
            "- No failed challenge diagnostics were supplied; inspect "
            "`./input/shard.json` and exit without broad rewrites."
        )
    lines: list[str] = []
    for item in diagnostics:
        challenge_id = str(item.get("challenge_id") or "unknown")
        status = str(item.get("validation_status") or "failed")
        error = str(item.get("validation_error") or "").strip()
        contract_errors = item.get("validation_contract_errors")
        if isinstance(contract_errors, list) and contract_errors:
            error = "; ".join(str(entry) for entry in contract_errors if entry) or error
        stdout_tail = str(item.get("validation_stdout_tail") or "")
        stderr_tail = str(item.get("validation_stderr_tail") or "")
        lines.append(f"- `{challenge_id}`: status=`{status}`")
        failure_kind = str(item.get("failure_kind") or "").strip()
        failure_hint = str(item.get("failure_hint") or "").strip()
        failed_step = str(item.get("failed_step") or "").strip()
        failure_details = item.get("validation_failure_details")
        lines.extend(
            f"  - {step}"
            for step in _repair_steps_for_status(
                status,
                error=error,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
                failure_kind=failure_kind,
                failure_hint=failure_hint,
                failed_step=failed_step,
                failure_details=failure_details if isinstance(failure_details, list) else [],
            )
        )
    return "\n".join(lines)


def _repair_steps_for_status(
    status: str,
    *,
    error: str,
    stdout_tail: str,
    stderr_tail: str,
    failure_kind: str,
    failure_hint: str,
    failed_step: str,
    failure_details: Sequence[object] = (),
) -> list[str]:
    lower_error = error.lower()
    lower_output = f"{stdout_tail}\n{stderr_tail}".lower()
    semantic_steps = _semantic_repair_steps_from_failure_details(failure_details)
    if semantic_steps:
        return semantic_steps
    pwn_steps = _pwn_repair_steps_from_failure_details(failure_details)
    if pwn_steps:
        return pwn_steps
    if status == "contract_failed":
        if failure_kind or failed_step or failure_hint:
            return [
                "Root cause: the host Docker build failed before validation could start.",
                (
                    f"Failure kind: `{failure_kind or 'docker_exit_nonzero'}`; "
                    f"failed step: `{failed_step or '(unknown)'}`"
                ),
                (
                    "Use the provided failure hint to fix the exact Dockerfile, "
                    "Compose file, or scaffold file that triggered the build error."
                ),
                (
                    "For the pwn xinetd scaffold, keep `ubuntu:20.04`, the fixed "
                    "binary at `/home/ctf/<binary>`, and UID/GID `1000:1000`."
                ),
                (
                    "If the error came from the scaffold, edit "
                    "the pwn scaffold reference directory visible from the active "
                    "execution workspace. Check `pwd` before reading it; "
                    "`references/scaffolds/...` paths are workspace-relative, not "
                    "repository-root paths."
                ),
            ] + ([f"Host hint: {failure_hint}"] if failure_hint else [])
        if (
            "references 'metadata.json'" in lower_error
            or "references 'challenge.yml'" in lower_error
        ):
            return [
                "Root cause: the Re validation path reads organizer files instead of solving the artifact.",
                "Remove all `metadata.json` / `challenge.yml` reads from both `validate.sh` and `writenup/exp.py`.",
                "Do not replace a hardcoded flag with metadata reads; that is the same cheat in another form.",
                (
                    "Make `validate.sh` run `writenup/exp.py ./attachments/<artifact>` "
                    "and extract the final `flag{...}` from solver stdout; do not "
                    "compare against metadata inside the script."
                ),
                (
                    "Make `writenup/exp.py` parse/decrypt data from the artifact "
                    "bytes or execute the artifact with a derived input."
                ),
            ]
        if "embeds the literal metadata.flag" in lower_error:
            return [
                "Root cause: the validator or solver contains the literal flag.",
                (
                    "Edit `validate.sh` / `writenup/exp.py` so they recover the "
                    "flag from the target or artifact at runtime."
                ),
                (
                    "Do not replace the hardcoded flag with reads from "
                    "`metadata.json`, `challenge.yml`, or `docker-compose*`; "
                    "that is the same cheat in another form."
                ),
            ]
        if "metadata.build_status is not passed" in lower_error:
            return [
                "Root cause: `metadata.json` still reports an incomplete build.",
                (
                    "For Web/Pwn, fix the buildable `deploy/` files and "
                    "`metadata.docker_image`; the host runner will run the "
                    "controlled Docker build and set `build_status` after success."
                ),
                (
                    "For Re artifacts, update artifact path, compiler/build "
                    "command, and SHA-256 if the build output changed."
                ),
            ]
        if "implement evidence incomplete" in lower_error:
            if "src missing" in lower_error or "src has no business source" in lower_error:
                return [
                    "Root cause: the implementation source tree is incomplete.",
                    (
                        "Create real challenge source under `src/` for Re or "
                        "`deploy/src/` for Web/Pwn; do not leave only "
                        "metadata/README/validate files."
                    ),
                    "Then rebuild the player artifact and update metadata build fields.",
                ]
            return [
                "Root cause: implementation evidence is incomplete.",
                "Create the missing source/deploy file named in the diagnostic, then rebuild and update metadata.",
                "Do not mark `build_status` passed until the source and artifact are both present.",
            ]
        if "missing deploy/" in lower_error:
            return [
                "Root cause: required Web/Pwn deployment files are missing.",
                (
                    "Create the missing `deploy/Dockerfile`, "
                    "`deploy/docker-compose.yml`, and `deploy/src/` files using "
                    "the existing design."
                ),
                "For Compose, keep one service with `environment:` and a literal `- FLAG=<metadata.flag>` entry.",
            ]
        if "no compiled elf" in lower_error or "no compiled pe" in lower_error:
            return [
                "Root cause: the player-facing compiled artifact is missing from `attachments/`.",
                "Compile the declared target format/architecture and place the binary under `attachments/`.",
                "Update `metadata.artifact`, `metadata.artifact_sha256`, compiler, and build command.",
            ]
        if "metadata.artifact missing" in lower_error:
            return [
                "Root cause: the Re build metadata does not name the player-facing artifact.",
                "Set `metadata.artifact` to the primary delivered binary under `attachments/`, "
                "for example `attachments/crackme`.",
                "Recompute and set `metadata.artifact_sha256` from that exact file, then rerun `validate.sh`.",
            ]
        if "metadata.artifact_sha256 missing" in lower_error:
            return [
                "Root cause: the Re build metadata does not record the delivered artifact hash.",
                "Compute SHA-256 from the file named by `metadata.artifact` under `attachments/`.",
                "Write the digest to `metadata.artifact_sha256`, then rerun `validate.sh`.",
            ]
        if "architecture is not" in lower_error or "wrong architecture" in lower_error:
            return [
                "Root cause: the delivered artifact format or architecture does not match metadata.",
                "Rebuild using the declared toolchain/target instead of changing the challenge category or ID.",
                "Use `file attachments/<artifact>` and update metadata only to match the verified artifact.",
            ]
        return [
            "Root cause: a host contract check failed before the exploit ran.",
            "Fix the named contract error exactly; avoid redesigning unrelated files.",
            "After the fix, run `validate.sh` and re-check the host contract checklist.",
        ]
    if status == "unnecessary_intended_path":
        if "running " in lower_error and "no input prints the flag" in lower_error:
            return [
                "Root cause: the delivered binary prints the flag without the intended input/path.",
                (
                    "Change the target so it requires the intended exploit, license "
                    "key, or reverse-engineered input before revealing the flag."
                ),
                "Keep the solver deriving that input from the delivered artifact or live service.",
            ]
        return [
            "Root cause: the flag is reachable without the intended technique.",
            (
                "For Re, remove plaintext flag bytes from `attachments/` and "
                "`dist/`; organizer-only `src/` may contain build-time material "
                "if not shipped."
            ),
            (
                "For Web/Pwn, do not remove the required literal FLAG entry from "
                "`deploy/docker-compose.yml`; that organizer deployment file may "
                "contain plaintext. Remove plaintext only from player-facing "
                "`attachments/` or from solver hardcoding."
            ),
            (
                "Encode/encrypt delivered flag material and update `writenup/exp.py` "
                "to recover it through the declared technique."
            ),
        ]
    if status == "missing_validation":
        return [
            "Root cause: `validate.sh` is missing.",
            "Create `validate.sh` as the single reproducible entrypoint that runs the real solver.",
            (
                "Its last recovered flag must be printed by the solver; do not "
                "read or echo `metadata.flag` inside validation."
            ),
        ]
    if status == "flag_mismatch":
        return [
            "Root cause: `validate.sh` ran but printed a different final flag.",
            (
                "Fix either the target flag injection/encryption or the solver "
                "extraction so they agree with `metadata.flag`."
            ),
            "Do not change `metadata.flag` unless the generated target was rebuilt consistently with that new flag.",
        ]
    if status == "nonzero_exit":
        hint = "Use stderr/stdout traceback to fix the failing command."
        if "modulenotfounderror" in lower_output or "no module named" in lower_output:
            hint = (
                "Remove the missing dependency, rewrite the solver with the Python "
                "standard library/system tools already present on the host, or vendor "
                "the helper module under `writenup/`; do not install packages during "
                "validation."
            )
        elif "connection refused" in lower_output or "timed out" in lower_output:
            hint = (
                "Fix service startup/readiness, host/port wiring, and cleanup "
                "traps before rerunning the exploit."
            )
        elif "eoferror" in lower_output:
            hint = (
                "For Pwn EOFs, enable pwntools debug logging, reproduce against "
                "the local ELF with `ELF()` + `process()` to fix menu synchronization "
                "and payload layout, then retest the default `remote(CHAL_HOST, CHAL_PORT)` path."
            )
        elif "permission denied" in lower_output:
            hint = "Fix executable bits, ownership, or container user permissions for the artifact and scripts."
        return [
            "Root cause: `validate.sh` exited non-zero.",
            hint,
            (
                "For Pwn, write or update `writenup/pwn_debug_report.json` with "
                "checksec, offset/leak/base/gadget evidence, local-vs-container "
                "results, and the final failing stage before editing blindly."
            ),
            "Keep validation offline-capable and deterministic; rerun `validate.sh` after each fix.",
        ]
    if status == "timeout":
        return [
            "Root cause: validation timed out.",
            (
                "Add readiness checks, bounded retries, and shorter exploit loops; "
                "remove brute force or network dependency from validation."
            ),
            "Make the solver deterministic enough to finish within the host timeout.",
        ]
    return [
        "Root cause: host validation failed.",
        "Use the status, error, stdout tail, and stderr tail above to make the smallest targeted fix.",
        "Rerun `validate.sh` before finishing and avoid changing challenge identity or delivery layout.",
    ]


def _semantic_repair_steps_from_failure_details(
    failure_details: Sequence[object],
) -> list[str]:
    details = [
        item
        for item in failure_details
        if isinstance(item, Mapping)
        and str(item.get("code") or "").startswith("semantic_")
    ]
    if not details:
        return []

    families = sorted(
        {
            str(item.get("declared_family") or "")
            for item in details
            if item.get("declared_family")
        }
    )
    categories = sorted(
        {
            str(item.get("semantic_category") or "")
            for item in details
            if item.get("semantic_category")
        }
    )
    sources = list(
        dict.fromkeys(
            str(item.get("source") or item.get("path") or "")
            for item in details
            if item.get("source") or item.get("path")
        )
    )
    tokens = list(
        dict.fromkeys(
            str(item.get("conflict_token") or "")
            for item in details
            if item.get("conflict_token")
        )
    )
    actions = list(
        dict.fromkeys(
            str(item.get("repair_action") or item.get("hint") or "")
            for item in details
            if item.get("repair_action") or item.get("hint")
        )
    )
    steps = [
        "Root cause: final artifact semantics conflict with the declared technique contract.",
        (
            "Keep the declared technique family intact"
            + (f" (`{', '.join(families)}`)" if families else "")
            + " unless metadata itself is genuinely wrong."
        ),
    ]
    if categories:
        steps.append(f"Category policy: `{', '.join(categories)}`.")
    if sources:
        steps.append(
            "Inspect and edit the cited final evidence files: "
            + ", ".join(f"`{source}`" for source in sources[:8])
            + "."
        )
    if tokens:
        steps.append(
            "Remove or rewrite the conflicting semantic markers: "
            + ", ".join(f"`{token}`" for token in tokens[:10])
            + "."
        )
    if actions:
        steps.extend(actions[:6])
    steps.append(
        "After editing, rerun `./validate.sh`; the final flag, writeup, report, "
        "repair summary, source, and exploit must all describe the same technique family."
    )
    steps.append(
        "Do not silence the validator by deleting evidence or changing metadata to a "
        "different technique unless the actual challenge design has intentionally changed."
    )
    return steps


def _pwn_repair_steps_from_failure_details(
    failure_details: Sequence[object],
) -> list[str]:
    details = [item for item in failure_details if isinstance(item, Mapping)]
    codes = {str(item.get("code") or "") for item in details}
    if not any(code.startswith("pwn_") or code == "compose_cross_talk" for code in codes):
        return []
    hints = [
        str(item.get("hint"))
        for item in details
        if isinstance(item.get("hint"), str) and item.get("hint")
    ]
    steps = [
        "Root cause: Pwn validation failed in the exploit/runtime path.",
        (
            "Start with the current `validate.sh` stage and the source-derived "
            "protocol token. Only use `pwn_debug` as auxiliary evidence when "
            "diagnostics conflict or exploit evidence is missing."
        ),
    ]
    if "pwn_prompt_eof" in codes:
        steps.append(
            "Fix service readiness and menu synchronization: wait for the real "
            "banner/menu prompt on a fresh connection, then align recv/send calls."
        )
        steps.append(
            "If validate.sh is involved, print bounded diagnostics for service ready state, "
            "the first banner line, the last recv position, and the failing phase before exit."
        )
    if "pwn_solver_prompt_desync" in codes:
        steps.append(
            "Fix solver protocol synchronization in `writenup/exp.py`: if target "
            "source reads a fixed payload and then consumes a newline or delimiter, "
            "send `payload + b\"\\n\"` or use pwntools `sendlineafter`/`sendafter` "
            "with the exact menu token."
        )
    if "pwn_service_readiness_failed" in codes or "pwn_bad_readiness_probe" in codes:
        steps.append(
            "Fix the relevant readiness layer before exploit tuning: service-readiness "
            "means the container/app did not connect or emit the source-derived "
            "token; validate-wrapper means `validate.sh` used port-only, fixed-byte, "
            "EOF-based, unexported, or token-mismatched probing."
        )
    if "pwn_bad_readiness_probe" in codes:
        steps.append(
            "`pwn_bad_readiness_probe` means the validation wrapper is suspect: "
            "replace fixed-byte `head -c`/`dd`/EOF-based TCP reads with a bounded "
            "socket loop that succeeds on Choice:/menu/banner and preserves the "
            "raw output tail even when the timeout expires."
        )
    if "compose_cross_talk" in codes:
        steps.append(
            "Fix Docker Compose isolation before exploit tuning: derive a unique "
            "`COMPOSE_PROJECT_NAME` from the challenge root and use the same "
            "`docker-compose -p \"$COMPOSE_PROJECT_NAME\" -f \"$CHAL_ROOT/deploy/docker-compose.yml\"` "
            "wrapper for up, ps, logs, and down diagnostics."
        )
    if "pwn_bad_binary_path" in codes:
        steps.append(
            "Fix the container launcher before exploit tuning: align Makefile TARGET, "
            "Dockerfile copy target, xinetd `server_args`, and the binary shipped "
            "inside `/home/ctf` so chroot can execute the intended ELF."
        )
    if "pwn_canary_leak_failed" in codes:
        steps.append(
            "Rescan stack leaks across a broad bounded `%n$p` range; choose "
            "the same stable leak position across multiple fresh runs, require "
            "low byte 0x00, and exclude NULL/small integers plus stack/libc/PIE "
            "addresses such as `0x7fff...`, `0x7f...`, and `0x55...`; remove "
            "any `2^48` filter."
        )
    if "pwn_chroot_flag_path" in codes:
        steps.append(
            "For xinetd chroot, keep startup writing `/home/ctf/flag` but make "
            "challenge code read `/flag` inside the chroot."
        )
    if "pwn_bad_offset" in codes:
        steps.append(
            "Recompute the overflow offset with cyclic/core/headless gdb against "
            "the actual shipped ELF, then update padding and saved frame layout."
        )
    if "pwn_payload_no_flag" in codes:
        steps.append(
            "The service was reachable but the final exploit did not recover a flag. "
            "Recheck prompt synchronization, payload layout, leak math, and the final "
            "`flag{...}` extraction path instead of relaxing validation."
        )
    if "pwn_exploit_model_unproven" in codes:
        steps.append(
            "Do not tune heap/ROP/canary constants blindly. First add or refresh "
            "`writenup/pwn_debug_report.json` with chunk layout, size class, "
            "overlap reachability, leak samples, base calculations, and gadget "
            "evidence from the shipped artifact/container; if the evidence "
            "contradicts the exploit model, rederive the solve path."
        )
    if "pwn_bruteforce_timeout" in codes:
        steps.append(
            "Replace timing-dependent brute force with bounded local/container evidence: "
            "cap attempts, record the attempted range in `pwn_debug_report.json`, and "
            "fail fast when the primitive is not deterministic."
        )
    if "pwn_rop_missing_gadget" in codes:
        steps.append(
            "Rediscover ROP gadgets from the actual ELF/libc with pwntools ROP, "
            "ROPgadget, ropper, or objdump; do not reuse guessed addresses."
        )
    if "pwn_rop_stack_alignment" in codes:
        steps.append(
            "Add or remove a `ret` alignment gadget before libc calls so amd64 stack alignment is correct."
        )
    if "pwn_libc_leak_failed" in codes:
        steps.append(
            "Repair the first-stage leak: verify GOT/PLT symbols, parse the full "
            "leaked pointer, and rerun stage two only after the leak is plausible."
        )
    if "pwn_bad_libc_base" in codes:
        steps.append(
            "Use the matching libc/ld from the container or attachments; check "
            "that computed libc base is plausible and page-aligned."
        )
    if "pwn_pie_base_failed" in codes:
        steps.append(
            "Leak a code pointer first, compute PIE base, and derive all binary symbols/gadgets from that base."
        )
    if "pwn_shell_no_flag" in codes:
        steps.append(
            "After control-flow success, explicitly run the intended flag read "
            "command or function and verify a `flag{...}` token reaches stdout."
        )
    if "pwn_prompt_eof" in codes or "pwn_service_readiness_failed" in codes:
        steps.append(
            "Bound every `recvuntil` / `recvline` wait with short timeouts, and "
            "cap brute-force or leak loops so a single bad stage fails fast."
        )
    if "pwn_remote_local_mismatch" in codes:
        steps.append(
            "Compare local process and container remote environment: libc/ld, PIE, "
            "ASLR assumptions, newline timing, chroot paths, and prompt text."
        )
    steps.extend(f"Host hint: {hint}" for hint in hints[:3])
    steps.append(
        "Rerun `validate.sh` after each targeted fix; repair is not complete "
        "until the real exploit prints the metadata flag."
    )
    return steps


# Host contract checklist replayed into every repair prompt. The host validator
# enforces each of these per category (see ``domain.validation.contract_errors``
# and ``_solver_integrity_errors``); fixing one without honouring the rest just
# produces a different ``contract_failed``. Keep this in sync with those checks
# and with ``prompts/shard_prompt.md``.
_VALIDATION_CONTRACT_CHECKLIST = """
Host contract checklist — every rule below is host-enforced. Re-check ALL rules that
apply to each challenge's `metadata.category` before finishing; satisfy them
simultaneously rather than trading one for another.

Common (web, pwn, re):
- `metadata.json` MUST keep `id`, `title`, `difficulty`, `build_status: passed`, and `flag`.
- `metadata.json`, `README.md`, `validate.sh`, `writenup/`, `src/`/`deploy/`,
  and `attachments/` MUST be direct children of the canonical challenge root.
  Do not leave a generated `output/challenges/...` tree under `src/`,
  `attachments/`, `deploy/`, or the challenge root.
- `validate.sh` and `writenup/exp.py` MUST NOT contain the literal `metadata.flag` value.
- `writenup/exp.py` MUST NOT read the flag from organizer files (`metadata.json`,
  `challenge.yml`, `docker-compose*`); it recovers the flag at runtime.
- `deploy/src/*.c` may be read to understand the bug, protocol, or menu flow,
  but it is never authoritative for offsets, gadgets, symbols, libc bases,
  checksec, or the SHA used by `writenup/exp.py`.

Web / Pwn:
- Keep `deploy/Dockerfile`, `deploy/docker-compose.yml`, and `deploy/src/`.
- `deploy/Dockerfile` MUST install `deploy/_files/start.sh` into the image as
  `/root/start.sh`; the Compose service or image entrypoint MUST start through
  `/root/start.sh`.
- The Compose service MUST define `environment:` (singular) with the literal
  list entry `- FLAG=flag{...}` equal to `metadata.flag`, and the service code
  MUST read `FLAG`.
- The exploit recovers the flag from the live service via `CHAL_HOST`/`CHAL_PORT`,
  never from the compose file that injects it.
- `validate.sh` MUST isolate Docker Compose state for concurrent validation:
  derive a stable project from the challenge root, export `COMPOSE_PROJECT_NAME`,
  and run all `up` / `ps` / `logs` / `down` operations through
  `docker-compose -p "$COMPOSE_PROJECT_NAME" -f "$CHAL_ROOT/deploy/docker-compose.yml"`.
  Do not rely on Compose's default project name such as `deploy`.
- Pwn solvers may use pwntools and should keep a local `ELF()`/`process()` debug
  path plus a default `remote(CHAL_HOST, CHAL_PORT)` validation path.
- Pwn solver offsets and ROP chains MUST be derived from the current
  `attachments/<binary>` ELF. Do not use `deploy/src/<binary>` as the authority
  for exploit offsets after host build, and do not reuse
  `writenup/pwn_debug_report.json` unless its `binary.sha256` exactly matches
  `metadata.artifact_sha256`.
- `deploy/src/` is allowed only as a source-reading aid for the bug design and
  protocol shape. Do not compile it, do not read runtime offsets from it, and
  do not let it become the source of `BINARY_SHA256`, gadgets, libc evidence,
  or the report binary path.
- Pwn `writenup/exp.py` MUST declare `BINARY_SHA256 = metadata.artifact_sha256`.
  The host gate rejects solvers that only declare aliases such as
  `ARTIFACT_SHA256`, or whose debug report/solver SHA was copied from
  `deploy/src/<metadata.artifact basename>` or an old build.
- Pwn source, metadata, writeup, and `writenup/exp.py` MUST match the declared
  primary technique. If the design says SROP/ORW/ret2libc/GOT overwrite, do not
  add hidden `read_flag()` / `win()` / ret2win shortcuts and do not solve by
  returning directly to such a helper unless ret2win is explicitly declared.
  SROP writeups and solvers must actually use sigreturn/SigreturnFrame/syscall
  evidence.
- Do not run Docker from Hermes. The host runner rebuilds the exact image named by
  `metadata.docker_image` with `docker build -t <metadata.docker_image> -f deploy/Dockerfile .`
  after deploy source, Dockerfile, binary, or runtime dependencies change.
- For Pwn, the host runner normalizes image identity before the build:
  `metadata.docker_image`, Compose `image`, and Compose `container_name` are
  synchronized to `pwn-{workspace_id[:6]}-{challenge_slug}:latest`.
  If existing files use generic names like `pwn-canary:latest`,
  `pwn-demo:latest`, or `pwn-<slug>:latest`, replace them with the
  workspace-scoped pattern instead of preserving them.
- The host runner labels managed images with `ctf-factory.*` metadata and
  prunes workspace-scoped dangling managed images after successful Docker
  builds. Do not run broad `docker image prune` or `docker builder prune`
  commands from Hermes.
- `validate.sh` MUST print bounded service diagnostics to stderr on failure:
  isolated-project `docker-compose ps`, recent `docker-compose logs --no-color --tail=120`,
  compose project name, service/container state, first banner or last probe output,
  and solver stdout/stderr tails. The host runner forwards those tails into repair prompts.
- Web additionally requires `metadata.runtime` and `metadata.framework`.

Pwn container launcher:
- Prefer the fixed xinetd + chroot + TCP socket scaffold reference visible from
  the active execution workspace. Check `pwd` first, then use the scaffold path
  from the prompt's `Pwn scaffold reference:` line; do not try
  `references/scaffolds/...` from the repository root or from a skill directory.
  Copy its `deploy/` tree into the challenge and
  replace placeholders such as `{{BINARY_NAME}}` and `{{SERVICE_PORT}}`; keep the
  scaffold's fixed `ctf` user with uid/gid `1000:1000`. This scaffold is the
  factory-normalized form of `ctf-docker-template/pwn-ubuntu_20.04`; do not invent a fresh Docker/chroot layout.
  The scaffold installs `xinetd`, copies an xinetd service file into
  `/etc/xinetd.d/ctf`, exposes the assigned service port, and has
  `/root/start.sh` start xinetd then stay foreground.
- Preserve the pwn xinetd/chroot scaffold's apt mirror fallback loop and mirror
  order. Do not replace it with one hardcoded mirror or remove fallback entries.
  If package fetch fails, keep the fallback loop and adjust the mirror list only
  deliberately.
- Do not add `chroot` to `apt-get install`; Ubuntu/Debian provide the `chroot`
  command via `coreutils`. Remove that package name or use `coreutils`.
- The xinetd service may run as root only to accept the socket and execute
  `/usr/sbin/chroot`; it should run the vulnerable binary with
  `server_args = --userspec=1000:1000 /home/ctf ./<binary>` by default,
  matching the fixed `ctf` user/group created in the image.
- Build `/home/ctf` as the chroot root with the vulnerable binary, required
  runtime libraries, minimal `/dev/null`, `zero`, `random`, `urandom`, and only
  helper binaries needed by the intended exploit. This construction is
  Dockerfile-only: commands such as `cp -R /lib* /home/ctf`,
  `cp -R /usr/lib* /home/ctf`, `mknod /home/ctf/dev/null ...`, and
  `cp /bin/ls /home/ctf/bin` MUST appear only as `RUN` steps in
  `deploy/Dockerfile`. In that Dockerfile they copy from the Docker build
  container into the image's chroot, not from the host. They MUST NOT appear in
  `validate.sh`,
  `metadata.build_command`, `deploy/_files/start.sh`, or xinetd config files,
  and MUST NOT be executed directly on the host. Write `/home/ctf/flag` at
  startup from `DASFLAG`, `FLAG`, or `GZCTF_FLAG` in that priority order, not
  in the Docker image layer.
- Harden xinetd with bounded settings such as `per_source`, `rlimit_cpu`, and
  compatible memory limits. Keep chroot contents owned by `root:ctf` and not
  writable by the `ctf` runtime user.

Re / Pwn binary target:
- The compiled player-facing ELF or PE/EXE lives in `attachments/` (pwn may
  also ship ELF under `deploy/`), and its architecture MUST match
  `metadata.architecture` / `metadata.target_platform`.

Re:
- `validate.sh` / `writenup/exp.py` MUST reference the distributed artifact under
  `attachments/` and derive the flag from that binary — never from
  `metadata.json` or `challenge.yml`.
- `metadata.artifact` MUST point at the primary player-facing artifact under
  `attachments/`, and `metadata.artifact_sha256` MUST match that exact file.
- `metadata.build_command` MUST describe the command used to create that artifact;
  after every rebuild, strip, or copy, recompute the hash from the final file in
  `attachments/`.
- `writenup/exp.py` MUST be offline-capable on the host: use the standard
  library, existing system tools such as `openssl`, or helper modules vendored
  under `writenup/`; do not depend on undeclared packages like `Crypto`
  / pycryptodome or missing local modules such as `aes_256`.
- The delivered artifact MUST NOT expose the plaintext flag through ordinary `strings`
  unless `primary_technique` declares strings as the intended solve; otherwise embed or
  encode the flag so recovery requires the intended technique.
"""


def _render_non_regression_section(prior_contract_errors: Sequence[str]) -> str:
    # 中文注释：把历轮已经报过的合约违规汇总成"禁止回归"清单，避免 Agent 修一条破一条。
    unique = list(dict.fromkeys(str(item) for item in prior_contract_errors if item))
    if not unique:
        return "\n"
    rendered = json.dumps(unique, ensure_ascii=False, indent=2)
    return f"""
Already-flagged contract violations from earlier repair attempts — each one MUST stay
fixed. Do NOT reintroduce any of these while addressing the diagnostics above:
```json
{rendered}
```
"""


def _render_resume_plan_section(
    resume_plan: ShardResumePlan | None,
    resume_output_targets: Mapping[str, str] | None = None,
) -> str:
    # 中文注释：把断点续跑计划整理成提示词片段，帮助 Agent 判断每道题的下一步。
    if resume_plan is None or not resume_plan.challenges:
        return (
            "No prior progress events for this shard; treat every challenge as a "
            "first-time run and start each one at stage `design`."
        )
    section_lines: list[str] = []
    targets = resume_output_targets or {}
    for challenge in resume_plan.challenges:
        if challenge.lookup_status == "missing_challenge":
            section_lines.append(
                f"- {challenge.challenge_id}: directory not found; start at `design` and create the challenge."
            )
            continue
        if challenge.lookup_status == "ambiguous_challenge":
            section_lines.append(
                f"- {challenge.challenge_id}: multiple matching directories; "
                "the runner will report validate/failed. Skip authoring."
            )
            continue
        skipped_stage_text = ", ".join(challenge.skipped_stages) if challenge.skipped_stages else "(none)"
        next_stage_name = challenge.first_pending_stage or "(all stages already complete)"
        target = targets.get(challenge.challenge_id)
        target_instruction = (
            f"; edit_exact_path={target}; do not create or rename another directory for {challenge.challenge_id}"
            if target
            else ""
        )
        section_lines.append(
            f"- {challenge.challenge_id}: skip_stages={skipped_stage_text}; "
            f"next_stage={next_stage_name}{target_instruction}"
        )
    return "\n".join(section_lines)


def _render_repair_section(
    repair_requested: bool,
    repair_context: Mapping[str, object] | None = None,
    retry_context: Mapping[str, object] | None = None,
) -> str:
    sections: list[str] = []
    if repair_requested:
        rendered = json.dumps(
            dict(repair_context or {}),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        sections.append(
            f"""
Repair mode is enabled.
- Treat the current workspace and the failure diagnostics below as the source of truth.
- Do not infer skipped design/implement/build/document work from historical progress events.
- Do not use carry-forward instructions in this mode.
- Make the smallest fix that resolves the current failure.

Repair context:
```json
{rendered}
```
"""
        )
    elif retry_context:
        rendered = json.dumps(
            dict(retry_context),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        sections.append(
            f"""
Retry carry-forward is enabled.
- The previous failed execution below is authoritative host-validation context.
- Treat the listed failure kinds, hints, and failed steps as non-regression constraints.
- Reuse valid work, but do not recreate the prior Dockerfile, build-context, or runtime mistakes.

Retry context:
```json
{rendered}
```
"""
        )
    return "".join(sections)


def render_prompt(
    paths: ProjectPaths,
    shard: Path,
    report: Path,
    worker: str,
    *,
    report_runtime_path: str | None = None,
    workspace_relative: bool = False,
    original_shard_name: str | None = None,
    resume_plan: ShardResumePlan | None = None,
    resume_output_targets: Mapping[str, str] | None = None,
    repair_requested: bool = False,
    repair_context: Mapping[str, object] | None = None,
    retry_context: Mapping[str, object] | None = None,
    references_prefix: str = "./references",
) -> str:
    # 中文注释：读取分片执行模板，并替换路径、worker、进度命令等运行上下文。
    prompt_text = paths.prompt_template.read_text(encoding="utf-8")
    progress_shard_name = original_shard_name or shard.name
    design_context_instruction = _design_context_instruction(shard)
    build_contract_section = _render_build_contract_section(shard)
    if workspace_relative:
        references_prefix = references_prefix.rstrip("/")
        runtime_paths = {
            "{shard_path}": "./input/shard.json",
            "{challenge_dir}": "./output/challenges",
            "{report_path}": "./logs/report.json",
            "{generation_profile}": "./input/generation-profiles.json",
            "{design_skill}": f"{references_prefix}/design-challenges/SKILL.md",
            "{design_references}": f"{references_prefix}/design-challenges/references",
            "{pwn_scaffold_reference}": f"{references_prefix}/scaffolds/pwn/xinetd-chroot/",
            "{progress_command}": "./bin/progress",
        }
    else:
        cli_script_path = Path(__file__).resolve().parents[1] / "cli.py"
        repository = getattr(paths, "repository", paths.root)
        runtime_paths = {
            "{shard_path}": str(shard.resolve()),
            "{challenge_dir}": str(paths.challenges.resolve()),
            "{report_path}": report_runtime_path or str(report.resolve()),
            "{generation_profile}": str(paths.generation_profile.resolve()),
            "{design_skill}": str(paths.design_skill.resolve()),
            "{design_references}": str(paths.design_references.resolve()),
            "{pwn_scaffold_reference}": str(
                (repository / "scaffolds" / "pwn" / "xinetd-chroot").resolve()
            ),
            "{progress_command}": (
                f'"{sys.executable}" "{cli_script_path}" progress '
                f'--shard "{progress_shard_name}" --worker "{worker}" --best-effort'
            ),
        }
    replacement_map = {
        **runtime_paths,
        "{worker}": worker,
        "{shard_name}": progress_shard_name,
        "{resume_plan}": _render_resume_plan_section(resume_plan, resume_output_targets),
        "{repair_section}": _render_repair_section(
            repair_requested,
            repair_context,
            retry_context,
        ),
        "{design_context_instruction}": design_context_instruction,
        "{build_contract_section}": build_contract_section,
    }
    for placeholder, rendered_value in replacement_map.items():
        prompt_text = prompt_text.replace(placeholder, rendered_value)
    return sanitize_prompt_text(prompt_text)


def _design_context_instruction(shard: Path) -> str:
    payload = read_json(shard, {})
    challenges = payload.get("challenges") if isinstance(payload, dict) else None
    if not isinstance(challenges, list) or not challenges:
        return ""
    if not all(isinstance(item, dict) and isinstance(item.get("design"), dict) for item in challenges):
        return ""
    return (
        "When each challenge carries a `design` sub-object, use it as "
        "authoritative for deployment, artifacts, flag location, validation "
        "steps, hints, and operator-facing prompt copy."
    )


# Governed fields the Build agent must implement exactly. They originate from
# the design/matrix row (see
# ``services.build_orchestration_service._matrix_values``) and are the usual
# drift surface: Build silently falling back to C/ELF/x86_64 instead of the
# declared language, format, architecture, or technique.
GOVERNED_CONTRACT_FIELDS: tuple[str, ...] = (
    "category",
    "difficulty",
    "deployment",
    "primary_technique",
    "runtime",
    "framework",
    "language",
    "compiler",
    "target_format",
    "architecture",
    "target_platform",
    "mitigations",
    "strip",
    "port",
)

# Values the matrix row fills in when the design left a field blank. Surfacing
# them as "governed" would be misleading, so they are skipped.
_CONTRACT_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {"", "unspecified", "none", "null"}
)


def _format_contract_value(value: object) -> str | None:
    # 中文注释：把单个 governed 字段值规整成可读文本；占位/空值返回 None 以便跳过。
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping):
        if not value:
            return None
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).strip()
    if text.lower() in _CONTRACT_PLACEHOLDER_VALUES:
        return None
    return text


def _render_build_contract_section(shard: Path) -> str:
    """Render the per-challenge governed fields as a locked, do-not-deviate block.

    中文注释：Build 阶段最常见的偏移是把 design 声明的 language/target_format/
    architecture/technique 偷偷回落成通用默认（C/ELF/x86_64）。把这些字段显式列成
    "锁死字段"，并要求做不出就 fail 而非替换，可以收敛偏移概率。
    """
    payload = read_json(shard, {})
    challenges = payload.get("challenges") if isinstance(payload, dict) else None
    if not isinstance(challenges, list) or not challenges:
        return ""
    entries: list[str] = []
    for challenge in challenges:
        if not isinstance(challenge, Mapping):
            continue
        challenge_id = _format_contract_value(challenge.get("id")) or "unknown"
        fields = [
            f"{name}={rendered}"
            for name in GOVERNED_CONTRACT_FIELDS
            if (rendered := _format_contract_value(challenge.get(name))) is not None
        ]
        if not fields:
            continue
        entries.append(f"- `{challenge_id}`: " + ", ".join(fields))
    if not entries:
        return ""
    listing = "\n".join(entries)
    return f"""
# Authoritative Build Contract

The GOVERNED fields below come from the committed design for each challenge.
They are locked: implement them exactly. Do NOT substitute or silently fall
back to a generic language, compiler, target format, architecture, or technique
(for example building a C/ELF/x86_64 artifact when another value is declared).

{listing}

If a GOVERNED field cannot be implemented as specified in this environment, STOP
that challenge and report its `build_status` as `failed` with a concrete reason
(such as the missing toolchain or unsupported target). Do not build a generic
substitute and do not change the challenge's declared identity to make it
buildable. You MAY freely choose only the implementation details that are NOT
listed above.
"""


def _render_seed_urls(seed_urls: tuple[str, ...]) -> str:
    # 中文注释：把持久化的种子 URL 渲染成列表；为空时给出明确占位说明。
    if not seed_urls:
        return "  (no seed URLs provided)"
    return "\n".join(f"  - {url}" for url in seed_urls)


def _render_difficulty_distribution(difficulty_distribution) -> str:
    # 中文注释：把难度分布映射压缩成易读的一行文本，方便 Agent 快速理解目标配比。
    if not difficulty_distribution:
        return "(unspecified)"
    return ", ".join(f"{label}={count}" for label, count in difficulty_distribution.items())


def _render_runtime_constraints(runtime_constraints) -> str:
    # 中文注释：运行约束以稳定 JSON 字符串输出，避免字典顺序导致提示词抖动。
    if not runtime_constraints:
        return "{}"
    return json.dumps(dict(runtime_constraints), ensure_ascii=False, sort_keys=True)


def _render_search_keywords(runtime_constraints) -> str:
    if not runtime_constraints:
        return "(none supplied)"
    raw = dict(runtime_constraints).get("search_keywords")
    if not raw:
        return "(none supplied)"
    if isinstance(raw, str):
        keywords = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        keywords = [str(item).strip() for item in raw if str(item).strip()]
    else:
        keywords = [str(raw).strip()]
    if not keywords:
        return "(none supplied)"
    return "\n".join(f"- {keyword}" for keyword in keywords)


def _render_generation_policy(runtime_constraints) -> str:
    if not runtime_constraints:
        return "- (none supplied)"
    raw = dict(runtime_constraints).get("generation_policy")
    if not isinstance(raw, str) or not raw.strip():
        return "- (none supplied)"
    shared = SHARED_GENERATION_STRATEGY_PATH.read_text(encoding="utf-8").strip()
    return "\n\n".join([raw.strip(), shared]).strip()


def _render_worked_example(category: str) -> str:
    # 中文注释：生成一个随 category 变化的示例，证明提示词不硬编码初始分类集合。
    example_payload = {
        "sources": [
            {
                "url": "https://example.com/reference-1",
                "title": f"Example {category} reference",
                "summary": "Brief 1-3 sentence summary of what this source covers.",
                "content_hash": "0" * 64,
            }
        ],
        "findings": [
            {
                "kind": "technique",
                "label": f"Sample technique within {category}",
                "technique_family": "other",
                "summary": "Brief 1-3 sentence summary of the technique itself.",
                "source_indices": [0],
            }
        ],
    }
    return json.dumps(example_payload, indent=2, ensure_ascii=False)


def render_research_prompt(generation_request: GenerationRequest) -> str:
    """为单个 generation request 渲染 `prompts/research_prompt.md`。

    category 会出现在提示词正文顶部，保证 Agent 在其他信息之前先读到范围约束。
    seed URLs 来自提交时持久化的 `generation_request.seed_urls`，不依赖 CLI 临时状态。
    """
    # 中文注释：从已持久化的 generation_request 渲染 Research Agent 的完整提示词。
    prompt_template = RESEARCH_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    category_code = generation_request.category
    replacement_map = {
        "{category}": category_code,
        "{topic}": generation_request.topic,
        "{target_count}": str(generation_request.target_count),
        "{difficulty_distribution}": _render_difficulty_distribution(generation_request.difficulty_distribution),
        "{runtime_constraints}": _render_runtime_constraints(generation_request.runtime_constraints),
        "{search_keywords}": _render_search_keywords(generation_request.runtime_constraints),
        "{generation_policy}": _render_generation_policy(generation_request.runtime_constraints),
        "{seed_urls}": _render_seed_urls(generation_request.seed_urls),
        "{technique_family_vocabulary}": render_family_vocabulary(category_code),
        "{worked_example}": _render_worked_example(category_code),
    }
    for placeholder, rendered_value in replacement_map.items():
        prompt_template = prompt_template.replace(placeholder, rendered_value)
    return sanitize_prompt_text(prompt_template)
