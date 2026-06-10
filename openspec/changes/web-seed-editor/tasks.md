## 1. Seed model and storage

- [x] 1.1 Persist matrix-compatible seeds under `work/`.
- [x] 1.2 Validate IDs, categories, difficulty, points, and container ports.
- [x] 1.3 Reuse shard grouping without overwriting pending files.

## 2. API and dashboard

- [x] 2.1 Add create/update, delete, list, and enqueue APIs.
- [x] 2.2 Add common field editing plus advanced JSON.
- [x] 2.3 Add configurable shard size and direct queue creation.

## 3. Verification

- [x] 3.1 Add unit and API tests.
- [x] 3.2 Run Ruff and backend regression tests.
- [x] 3.3 Validate save, reload/list, enqueue, and delete against the running
  dashboard HTTP service. In-app browser startup was blocked by the local
  Windows sandbox, so visual verification used static DOM/JavaScript checks.
- [x] 3.4 Run strict OpenSpec validation.
