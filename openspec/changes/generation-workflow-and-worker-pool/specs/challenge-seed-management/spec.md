## MODIFIED Requirements

### Requirement: Seeds can enter the existing queue

The dashboard SHALL split seed batches by category using the requested shard
size and SHALL write the existing `{"challenges": [...]}` pending shard format.
It SHALL NOT overwrite an existing pending shard with the same filename.

The primary browser run-creation path SHALL split only the seed batch submitted
with that request. Previously saved seeds MAY be persisted and listed for
reuse, but MUST NOT be silently included in a new run unless the request
explicitly submits them.

#### Scenario: Enqueue submitted batch only

- **GIVEN** `work/challenge_seeds.json` already contains a saved Web seed
- **WHEN** a user creates a run by submitting a batch containing only one Pwn
  seed
- **THEN** the system writes only Pwn pending shards for that run
- **AND** no Web shard is created from the unrelated saved seed

#### Scenario: Save submitted seeds for reuse

- **WHEN** a run-creation request includes `save_seeds: true`
- **THEN** every submitted seed is validated and persisted through the same
  matrix-compatible seed store used by `POST /api/seeds`
- **AND** the shard files for the run are created from the submitted seed
  values after validation

#### Scenario: Do not save submitted seeds

- **WHEN** a run-creation request includes `save_seeds: false`
- **THEN** submitted seeds are validated and may be split into shards
- **AND** `work/challenge_seeds.json` is not modified by that request

Run creation atomicity, run manifests, and worker launch behavior are covered
by the `generation-run-orchestration` capability. This capability owns only
seed validation, persistence, and matrix-compatible shard input semantics.
