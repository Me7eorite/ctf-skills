## ADDED Requirements

### Requirement: RE challenges declare a target platform

A Reverse Engineering challenge SHALL declare its target platform in the
matrix via the `target_platform` field (or the legacy `architecture` field
on `metadata.json`). The system SHALL recognize the following canonical
values when validating the produced ELF artifact:

| Declared value         | ELF machine that satisfies it |
| ---------------------- | ----------------------------- |
| `linux/amd64`, `amd64`, `x86_64` | `x86_64`                |
| `linux/arm64`, `arm64`, `aarch64` | `aarch64`              |
| `linux/arm`, `arm`, `armv7`       | `arm`                  |

Other values pass through without architectural enforcement (the existing
non-architectural contract checks still apply). When `target_platform` is
absent, `linux/amd64` SHALL be assumed.

#### Scenario: arm64-declared challenge with aarch64 ELF passes

- **WHEN** a re challenge declares `target_platform: linux/arm64` and the
  artifact under `dist/` is an aarch64 Linux ELF
- **THEN** `ChallengeValidator.contract_errors` returns no architecture
  error for that artifact

#### Scenario: arm64-declared challenge with x86_64 ELF fails

- **WHEN** a re challenge declares `target_platform: linux/arm64` and the
  artifact under `dist/` is an x86_64 Linux ELF
- **THEN** `ChallengeValidator.contract_errors` returns a
  `"ELF artifact architecture is not aarch64: ..."` error listing the
  offending artifact path

#### Scenario: amd64 path is unchanged

- **WHEN** a re challenge declares `target_platform: linux/amd64` and the
  artifact under `dist/` is an x86_64 Linux ELF
- **THEN** `ChallengeValidator.contract_errors` returns no architecture
  error for that artifact (existing amd64 behavior preserved)

#### Scenario: arm (32-bit) declared with aarch64 ELF fails

- **WHEN** a re challenge declares `target_platform: linux/arm` (i.e. 32-bit
  armv7) and the artifact under `dist/` is an aarch64 Linux ELF
- **THEN** `ChallengeValidator.contract_errors` returns a
  `"ELF artifact architecture is not arm: ..."` error

#### Scenario: unknown target platform is unenforced

- **WHEN** a re challenge declares `target_platform: linux/mips` and the
  artifact under `dist/` is any ELF
- **THEN** `ChallengeValidator.contract_errors` returns no architecture
  error (other contract checks still apply)

### Requirement: Shard prompt MUST instruct the agent on per-platform targets

The shard prompt SHALL tell the agent that the produced ELF must match the
declared `target_platform`, and SHALL list `linux/amd64`, `linux/arm64`,
and `linux/arm` as the canonical accepted values. The prompt MAY still
keep `linux/amd64` as the implicit default when `target_platform` is
absent, but MUST NOT declare any specific non-default architecture (e.g.
aarch64) to be inherently a failed build.

#### Scenario: agent sees the per-platform rule

- **WHEN** the shard prompt is rendered for any re challenge
- **THEN** the rendered prompt contains text instructing that the artifact
  architecture must match the matrix-declared `target_platform`, and lists
  arm64 as a valid value
