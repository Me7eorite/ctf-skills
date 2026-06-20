## MODIFIED Requirements

### Requirement: ChallengeValidator supports single-challenge validation

`ChallengeValidator` SHALL keep its batch validation interface and SHALL add
`validate_challenge(challenge_id) -> dict`. The single-challenge interface MUST
match exactly one `work/challenges/<challenge_id>-<slug>` directory. Zero
matches MUST return a failed `missing_challenge` status, and multiple matches
MUST return a failed `ambiguous_challenge` status without selecting or executing
any directory.

When `validate.sh` exits `0`, the validator SHALL extract the recovered flag by
scanning the captured stdout for independent flag tokens matching
`(?<![A-Za-z0-9_])flag\{[^\r\n{}]+\}(?![A-Za-z0-9_])` and using the LAST match
as `printed_flag`. If no match
exists, the result is `flag_mismatch` with `printed_flag = ""`. This replaces
the previous "last non-empty stdout line" rule, which mis-classified successful
runs as `flag_mismatch` whenever the shell's EXIT trap printed cleanup messages
after the flag was echoed.

The selected `printed_flag` is compared to `metadata.flag` with exact string
equality. On mismatch the result status is `flag_mismatch`. On equality the
result status is `passed`.

#### Scenario: Ambiguous challenge id is failed safely

- **WHEN** two challenge directories match the same challenge id prefix
- **THEN** `validate_challenge` returns `ambiguous_challenge` and the runner
  records validate/failed

#### Scenario: Cleanup trap output does not mask a passing flag

- **GIVEN** `validate.sh` exits `0` and stdout ends with
  ```
  [+] Validation PASSED
  flag{whitespace_bypass_master}
  [*] Cleaning up...
  ```
- **AND** `metadata.flag = "flag{whitespace_bypass_master}"`
- **WHEN** `validate_challenge` runs
- **THEN** the result status is `passed`
- **AND** `printed_flag == "flag{whitespace_bypass_master}"`

#### Scenario: No flag pattern in stdout yields flag_mismatch

- **GIVEN** `validate.sh` exits `0` but stdout contains no `flag{...}`
  substring
- **WHEN** `validate_challenge` runs
- **THEN** the result status is `flag_mismatch` with `printed_flag = ""`

### Requirement: validate.sh prompt contract reuses existing images

The prompt SHALL instruct Web/Pwn validate scripts to inspect the expected image
before building it. Generated `validate.sh` scripts MUST use the pattern
`docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" .`
before `docker compose up`. Force rebuild is performed manually by deleting the
image outside the script.

Generated `validate.sh` scripts MUST also satisfy two stdout-hygiene rules so
the host validator's flag extraction is not contaminated:

1. The `cleanup` function (and any other shell function fired from
   `trap ... EXIT` / `trap ... ERR`) MUST redirect ALL of its output to stderr
   (`>&2`). This includes `echo` lines, `docker stop`, `docker rm`, and any
   diagnostic messages. The recovered flag MUST be the last text written to
   stdout in the success path.
2. The script MUST perform a pre-run cleanup of any stale container name
   before `docker run --name "$CONTAINER_NAME"`, e.g.
   `docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true`. This prevents
   `nonzero_exit` failures caused by leftover containers from a previous
   killed run.

#### Scenario: Prompt includes image inspect fallback

- **WHEN** a dry-run prompt is rendered for Web/Pwn validation
- **THEN** it contains the literal `docker image inspect "$IMAGE" >/dev/null
  2>&1 || docker build` pattern

#### Scenario: Prompt mandates stderr-only cleanup output

- **WHEN** a dry-run prompt is rendered for Web/Pwn validation
- **THEN** the prompt instructs cleanup-function output to be redirected to
  stderr (`>&2`) and instructs a pre-run `docker rm -f "$CONTAINER_NAME"`
  before `docker run --name`
