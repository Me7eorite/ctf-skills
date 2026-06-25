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

RESEARCH_PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "prompts" / "research_prompt.md"


def render_validation_repair_prompt(
    *,
    attempt: int,
    max_attempts: int,
    validation_results: list[dict],
    prior_contract_errors: Sequence[str] = (),
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
                    "validation_elapsed",
                )
                if result.get(key) not in (None, "", [])
            }
        )
    rendered = json.dumps(diagnostics, ensure_ascii=False, indent=2)
    repair_plan = _render_repair_plan(diagnostics)
    non_regression = _render_non_regression_section(prior_contract_errors)
    return f"""You are repairing CTF challenge artifacts after authoritative host validation failed.

Repair attempt {attempt} of {max_attempts}. Work only inside the existing claimed challenge
directories under `./output/challenges`. Read `./input/shard.json` and inspect the current
source, Docker/Compose files, built artifact metadata, `validate.sh`, and `writenup/exp.py`.

Host validation diagnostics:
```json
{rendered}
```

Focused repair plan:
{repair_plan}

How to read `validation_error`:
- `"contract_failed"` + `validation_error` starting with `"build evidence incomplete: metadata.<FIELD> missing"`
  means the named field is absent from `metadata.json`. You MUST edit `metadata.json`
  directly to add or correct that field. Do NOT create or modify
  `build-evidence.json`, `evidence.json`, or any other side-car file — the host validator
  only reads `metadata.json` and the on-disk artifacts.
- `"build evidence incomplete: docker image '<NAME>' not present on host"` means the
  image is missing or differs from `metadata.docker_image`. Rebuild that exact image tag
  (`docker build -t <NAME> ...`) and do not rename the tag.
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
{_VALIDATION_CONTRACT_CHECKLIST}{non_regression}
Run `validate.sh` yourself and iterate until it exits 0 and its last recovered flag equals
`metadata.flag`. Do not hardcode or merely echo the expected flag in the exploit. The exploit
must recover it through the intended vulnerability. Do not write `validate/*` progress events;
the host runner will perform and record the authoritative validation again after you return.
Update documentation and metadata when the repaired implementation changes them.

Before you finish, self-check every challenge you touched with real file searches:
- Run a search equivalent to `grep -R "metadata.json\\|challenge.yml\\|docker-compose\\|flag{{" validate.sh writenup/exp.py`.
- For `re`, any `metadata.json` or `challenge.yml` hit in `validate.sh` or `writenup/exp.py`
  is still a failure. `validate.sh` must validate by running the solver and extracting a
  `flag{{...}}` token from solver output, not by reading organizer answers.
- For `re`, confirm `validate.sh` or `writenup/exp.py` passes/opens the artifact under
  `attachments/`.
A repair that fixes one diagnostic by violating a different rule above still fails host validation.
"""


def _render_repair_plan(diagnostics: Sequence[Mapping[str, object]]) -> str:
    if not diagnostics:
        return "- No failed challenge diagnostics were supplied; inspect `./input/shard.json` and exit without broad rewrites."
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
        lines.extend(
            f"  - {step}"
            for step in _repair_steps_for_status(
                status,
                error=error,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
        )
    return "\n".join(lines)


def _repair_steps_for_status(
    status: str,
    *,
    error: str,
    stdout_tail: str,
    stderr_tail: str,
) -> list[str]:
    lower_error = error.lower()
    lower_output = f"{stdout_tail}\n{stderr_tail}".lower()
    if status == "contract_failed":
        if "references 'metadata.json'" in lower_error or "references 'challenge.yml'" in lower_error:
            return [
                "Root cause: the Re validation path reads organizer files instead of solving the artifact.",
                "Remove all `metadata.json` / `challenge.yml` reads from both `validate.sh` and `writenup/exp.py`.",
                "Do not replace a hardcoded flag with metadata reads; that is the same cheat in another form.",
                "Make `validate.sh` run `writenup/exp.py ./attachments/<artifact>` and extract the final `flag{...}` from solver stdout; do not compare against metadata inside the script.",
                "Make `writenup/exp.py` parse/decrypt data from the artifact bytes or execute the artifact with a derived input.",
            ]
        if "embeds the literal metadata.flag" in lower_error:
            return [
                "Root cause: the validator or solver contains the literal flag.",
                "Edit `validate.sh` / `writenup/exp.py` so they recover the flag from the target or artifact at runtime.",
                "Do not replace the hardcoded flag with reads from `metadata.json`, `challenge.yml`, or `docker-compose*`; that is the same cheat in another form.",
            ]
        if "metadata.build_status is not passed" in lower_error:
            return [
                "Root cause: `metadata.json` still reports an incomplete build.",
                "Run the real build command, verify the artifact/image exists, then set `build_status` to `passed` only after success.",
                "Update artifact path, compiler/build command, and SHA-256 if the build output changed.",
            ]
        if "implement evidence incomplete" in lower_error:
            if "src missing" in lower_error or "src has no business source" in lower_error:
                return [
                    "Root cause: the implementation source tree is incomplete.",
                    "Create real challenge source under `src/` for Re or `deploy/src/` for Web/Pwn; do not leave only metadata/README/validate files.",
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
                "Create the missing `deploy/Dockerfile`, `deploy/docker-compose.yml`, and `deploy/src/` files using the existing design.",
                "For Compose, keep one service and a literal `- FLAG=<metadata.flag>` environment entry.",
            ]
        if "no compiled elf" in lower_error or "no compiled pe" in lower_error:
            return [
                "Root cause: the player-facing compiled artifact is missing from `attachments/`.",
                "Compile the declared target format/architecture and place the binary under `attachments/`.",
                "Update `metadata.artifact`, `metadata.artifact_sha256`, compiler, and build command.",
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
                "Change the target so it requires the intended exploit, license key, or reverse-engineered input before revealing the flag.",
                "Keep the solver deriving that input from the delivered artifact or live service.",
            ]
        return [
            "Root cause: the flag is reachable without the intended technique.",
            "For Re, remove plaintext flag bytes from `attachments/` and `dist/`; organizer-only `src/` may contain build-time material if not shipped.",
            "Encode/encrypt delivered flag material and update `writenup/exp.py` to recover it through the declared technique.",
        ]
    if status == "missing_validation":
        return [
            "Root cause: `validate.sh` is missing.",
            "Create `validate.sh` as the single reproducible entrypoint that runs the real solver.",
            "Its last recovered flag must be printed by the solver; do not read or echo `metadata.flag` inside validation.",
        ]
    if status == "flag_mismatch":
        return [
            "Root cause: `validate.sh` ran but printed a different final flag.",
            "Fix either the target flag injection/encryption or the solver extraction so they agree with `metadata.flag`.",
            "Do not change `metadata.flag` unless the generated target was rebuilt consistently with that new flag.",
        ]
    if status == "nonzero_exit":
        hint = "Use stderr/stdout traceback to fix the failing command."
        if "modulenotfounderror" in lower_output or "no module named" in lower_output:
            hint = "Remove the missing dependency, vendor it locally, or add it to the Docker/image build instead of installing during validation."
        elif "connection refused" in lower_output or "timed out" in lower_output:
            hint = "Fix service startup/readiness, host/port wiring, and cleanup traps before rerunning the exploit."
        elif "permission denied" in lower_output:
            hint = "Fix executable bits, ownership, or container user permissions for the artifact and scripts."
        return [
            "Root cause: `validate.sh` exited non-zero.",
            hint,
            "Keep validation offline-capable and deterministic; rerun `validate.sh` after each fix.",
        ]
    if status == "timeout":
        return [
            "Root cause: validation timed out.",
            "Add readiness checks, bounded retries, and shorter exploit loops; remove brute force or network dependency from validation.",
            "Make the solver deterministic enough to finish within the host timeout.",
        ]
    return [
        "Root cause: host validation failed.",
        "Use the status, error, stdout tail, and stderr tail above to make the smallest targeted fix.",
        "Rerun `validate.sh` before finishing and avoid changing challenge identity or delivery layout.",
    ]


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
- `validate.sh` and `writenup/exp.py` MUST NOT contain the literal `metadata.flag` value.
- `writenup/exp.py` MUST NOT read the flag from organizer files (`metadata.json`,
  `challenge.yml`, `docker-compose*`); it recovers the flag at runtime.

Web / Pwn:
- Keep `deploy/Dockerfile`, `deploy/docker-compose.yml`, and `deploy/src/`.
- `deploy/Dockerfile` MUST install `deploy/_files/start.sh` into the image as
  `/root/start.sh`; the Compose service or image entrypoint MUST start through
  `/root/start.sh`.
- The Compose service MUST define the literal environment list entry `- FLAG=flag{...}`
  equal to `metadata.flag`, and the service code MUST read `FLAG`.
- The exploit recovers the flag from the live service via `CHAL_HOST`/`CHAL_PORT`,
  never from the compose file that injects it.
- Rebuild the exact image named by `metadata.docker_image` whenever deploy source,
  Dockerfile, binary, or runtime dependencies change; keep `metadata.artifact_sha256` in sync.
- Web additionally requires `metadata.runtime` and `metadata.framework`.

Re / Pwn binary target:
- The compiled player-facing ELF or PE/EXE lives in `attachments/` (pwn may
  also ship ELF under `deploy/`), and its architecture MUST match
  `metadata.architecture` / `metadata.target_platform`.

Re:
- `validate.sh` / `writenup/exp.py` MUST reference the distributed artifact under
  `attachments/` and derive the flag from that binary — never from
  `metadata.json` or `challenge.yml`.
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
) -> str:
    if not repair_requested:
        return ""
    rendered = json.dumps(
        dict(repair_context or {}),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    return f"""
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
) -> str:
    # 中文注释：读取分片执行模板，并替换路径、worker、进度命令等运行上下文。
    prompt_text = paths.prompt_template.read_text(encoding="utf-8")
    progress_shard_name = original_shard_name or shard.name
    design_context_instruction = _design_context_instruction(shard)
    if workspace_relative:
        runtime_paths = {
            "{shard_path}": "./input/shard.json",
            "{challenge_dir}": "./output/challenges",
            "{report_path}": "./logs/report.json",
            "{generation_profile}": "./input/generation-profiles.json",
            "{design_skill}": "./references/design-challenges/SKILL.md",
            "{design_references}": "./references/design-challenges/references",
            "{progress_command}": "./bin/progress",
        }
    else:
        cli_script_path = Path(__file__).resolve().parents[1] / "cli.py"
        runtime_paths = {
            "{shard_path}": str(shard.resolve()),
            "{challenge_dir}": str(paths.challenges.resolve()),
            "{report_path}": report_runtime_path or str(report.resolve()),
            "{generation_profile}": str(paths.generation_profile.resolve()),
            "{design_skill}": str(paths.design_skill.resolve()),
            "{design_references}": str(paths.design_references.resolve()),
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
        "{repair_section}": _render_repair_section(repair_requested, repair_context),
        "{design_context_instruction}": design_context_instruction,
    }
    for placeholder, rendered_value in replacement_map.items():
        prompt_text = prompt_text.replace(placeholder, rendered_value)
    return prompt_text


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
        "{seed_urls}": _render_seed_urls(generation_request.seed_urls),
        "{technique_family_vocabulary}": render_family_vocabulary(category_code),
        "{worked_example}": _render_worked_example(category_code),
    }
    for placeholder, rendered_value in replacement_map.items():
        prompt_template = prompt_template.replace(placeholder, rendered_value)
    return prompt_template
