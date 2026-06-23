## ADDED Requirements

### Requirement: Difficulty is driven by distinct 考点 and novelty, not solve-step count

Difficulty alignment SHALL grade a design by the number of **distinct
techniques (考点)** plus, for expert, the presence of `novelty` — never by the
number of mechanical solve steps. The per-tier technique bounds
(`techniques_min`/`techniques_max`) and the expert novelty requirement remain
the difficulty drivers.

Mechanical transforms SHALL be folded **order-free**, with no dependency on
solve order or runtime key state: a declared technique is *mechanical* iff its
normalized `sub_technique` is in the mechanical-transform set in
`src/domain/design/mechanical_transforms.py` (Layer 2) — for example the
canonical keys `base64`, `base32`, `hex`, `url`, `rot`, `caesar`, `xor`,
`gzip`, `zlib`, or `strings`. Mechanical-ness is a property of the technique
label/classification; the system SHALL NOT attempt to infer it from the order of
steps or from whether a key was recovered earlier, so no ordered chain source is
required.

When the difficulty layer gathers plain string labels from `techniques[]`,
`primary_technique`, and `secondary_technique`, it SHALL adapt each string to the
existing taxonomy API by passing it as a label-bearing value, for example
`resolve_sub_technique({"label": value})`. It SHALL NOT pass raw strings if the
taxonomy API would read them as missing-label values.

The counted technique total SHALL be the number of distinct **non-mechanical**
`sub_technique`s; when every declared technique is mechanical the total SHALL be
exactly `1` (a pure decode/unwrap chain is a single `encoding` 考点). Mechanical
transforms SHALL NOT add to the count when at least one non-mechanical technique
is present. The folded class is named `mechanical_class` (value `encoding`);
this name is deliberately distinct from the research-lane `family` used by
`add-design-technique-diversity`, which is an unrelated taxonomy.

A genuine technique that resembles a mechanical transform SHALL be kept by
labelling it as the analysis it is (e.g. `xor key recovery` rather than bare
`xor`); such a label normalizes to a non-mechanical `sub_technique` and counts.
The design prompts teach this distinction.

`intended_path` length SHALL be treated as an **upper-bound sanity check only**:
there SHALL be no per-tier minimum on `intended_path` step count, so a short,
low-考点 solve is never forced to pad steps to qualify, and a longer mechanical
walkthrough is never promoted to a higher tier on step count alone. The
`intended_path` maximum per tier is retained to catch runaway walkthroughs.
Rendered design prompts SHALL describe this as an upper-bound budget rather than
a min-max difficulty range.

Historical designs created before this change SHALL be grandfathered via the
existing `legacy_grandfather` path and SHALL NOT be re-validated; the new
counting applies only to new design attempts.

#### Scenario: Two-layer and three-layer decode solves are both easy

- **GIVEN** a `re` design A solved by `strings → base64-decode → flag`
- **AND** a `re` design B solved by `IDA → xor-decrypt → base64-decode → flag`
- **AND** both are slotted `difficulty = "easy"`
- **WHEN** difficulty alignment runs
- **THEN** `_count_techniques` returns `1` for each (all techniques are mechanical → one `encoding` 考点)
- **AND** both designs validate as `easy`
- **AND** design B is NOT promoted to a higher tier because it has one more step

#### Scenario: A mechanical transform is free alongside a real technique

- **GIVEN** a design whose techniques are `sqli` (non-mechanical) and `base64` (mechanical)
- **WHEN** `_count_techniques` runs
- **THEN** it returns `1` (the mechanical `base64` adds nothing to the `sqli` count)

#### Scenario: Step count alone cannot satisfy a higher tier

- **GIVEN** a design with a single distinct 考点 but a long mechanical solve walkthrough
- **AND** it is slotted `difficulty = "hard"`
- **WHEN** difficulty alignment runs
- **THEN** the design is rejected for failing `techniques_min`
- **AND** the rejection is not avoided by the length of `intended_path`

#### Scenario: A genuine analysis step still counts via its label

- **GIVEN** a design whose techniques are `xor key recovery` (an analysis step) and a separate `logic flaw`
- **WHEN** `_count_techniques` runs
- **THEN** both normalize to non-mechanical `sub_technique`s and count as two distinct techniques
- **AND** they are not folded into the single `encoding` `mechanical_class`

#### Scenario: Removing the intended_path floor does not reject on short paths

- **GIVEN** a design that satisfies the technique and novelty requirements for its tier
- **AND** its `intended_path` is shorter than the old tier-specific floor
- **WHEN** difficulty alignment runs under the relaxed rubric
- **THEN** the design is not rejected for having too few `intended_path` steps

#### Scenario: Historical designs are grandfathered, not re-graded

- **GIVEN** a stored design created before this change that relied on mechanical-step padding to meet a technique floor
- **WHEN** the system runs with the new counting
- **THEN** that historical design is grandfathered (`legacy_grandfather`) and is not re-validated
- **AND** only new design attempts are graded by the order-free mechanical fold
