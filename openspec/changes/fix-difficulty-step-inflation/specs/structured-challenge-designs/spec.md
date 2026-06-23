## ADDED Requirements

### Requirement: Difficulty is driven by distinct 考点 and novelty, not solve-step count

Difficulty alignment SHALL grade a design by the number of **distinct
techniques (考点)** plus, for expert, the presence of `novelty` — never by the
number of mechanical solve steps. The per-tier technique bounds
(`techniques_min`/`techniques_max`) and the expert novelty requirement remain
the difficulty drivers.

A **linear mechanical decode/unwrap chain SHALL collapse to a single counted
technique** of family `encoding`. A transformation is mechanical when it applies
a standard decoder with already-available inputs — for example `base64`,
`base32`, `hex`, `url`, `rot/caesar`, `xor` with a key already recovered earlier
in the path, `gzip`/`zlib` unwrap, or `strings` extraction. Stacking additional
mechanical layers SHALL NOT increase the counted technique total. A technique
that requires genuine analysis (a vulnerability, an algorithm to reverse, or a
key-recovery step that must itself be derived) is not mechanical and SHALL
continue to count individually.

`intended_path` length SHALL be treated as an **upper-bound sanity check only**:
there SHALL be no per-tier minimum on `intended_path` step count, so a short,
low-考点 solve is never forced to pad steps to qualify, and a longer mechanical
walkthrough is never promoted to a higher tier on step count alone. The
`intended_path` maximum per tier is retained to catch runaway walkthroughs.

#### Scenario: Two-layer and three-layer decode solves are both easy

- **GIVEN** a `re` design A solved by `strings → base64-decode → flag`
- **AND** a `re` design B solved by `IDA → xor-decrypt (recovered key) → base64-decode → flag`
- **AND** both are slotted `difficulty = "easy"`
- **WHEN** difficulty alignment runs
- **THEN** `_count_techniques` returns `1` for each (the decode chain is one 考点)
- **AND** both designs validate as `easy`
- **AND** design B is NOT promoted to a higher tier because it has one more step

#### Scenario: Step count alone cannot satisfy a higher tier

- **GIVEN** a design with a single distinct 考点 but a long mechanical solve walkthrough
- **AND** it is slotted `difficulty = "hard"`
- **WHEN** difficulty alignment runs
- **THEN** the design is rejected for failing `techniques_min`
- **AND** the rejection is not avoided by the length of `intended_path`

#### Scenario: A genuine analysis step still counts

- **GIVEN** a design whose solve requires deriving an unknown xor key through analysis and then exploiting a separate logic flaw
- **WHEN** `_count_techniques` runs
- **THEN** the key-derivation technique and the logic flaw count as two distinct techniques
- **AND** they are not collapsed into a single `encoding` technique

#### Scenario: No previously valid design becomes invalid

- **GIVEN** a design that validated under the prior rubric with an `intended_path` step floor
- **WHEN** difficulty alignment runs under the relaxed rubric
- **THEN** the design still validates (the change only removes a lower bound and refines technique counting downward)
