-- Phase 0 hot-fix: restore wrongly-marked-lost build attempts on the dev server.
--
-- Context: Before the reconciler race fix (grace 60s → 300s + rescan retry +
-- removal of /api/state synchronous tick), `BuildReconciler` was incorrectly
-- marking active build_attempts as `lost`. Their shard files were still in
-- work/shards/pending/ or work/shards/failed/ — see the diagnostic in
-- worker-pool-split-plan.md and the GitHub issue (TBD).
--
-- This script:
--   (a) restores attempts whose shards are still pending → status=queued so
--       the next worker pick-up can resume normally
--   (b) reclassifies attempts whose shards completed in failed/ but were
--       prematurely marked lost → status=failed (the truthful outcome)
--   (c) realigns parent design_task.status so retry/build paths see correct
--       state
--
-- IDEMPOTENT: re-running has no effect because each UPDATE filters by current
-- status. Safe to re-run after a partial apply.
--
-- USAGE (on the dev server):
--   PGPASSWORD=postgres psql -h 192.168.6.193 -U postgres -d challenge_factory \
--     -f tools/scripts/restore_lost_build_attempts.sql
--
-- After running, verify with:
--   SELECT id, status, error FROM build_attempts WHERE id IN (...);

BEGIN;

-- (a) Pending shards still on disk → queued
UPDATE build_attempts
SET status = 'queued',
    finished_at = NULL,
    error = NULL,
    artifact_status = 'unknown'
WHERE id IN (
    '92de7b6a-e519-49a4-8114-e02a3c117168',
    'e32f71fe-0149-420c-8b0c-9c1d75843737',
    '05a859c3-ef53-48dc-8082-a71edce109fb'
)
  AND status = 'lost';

-- (b) Completed but mis-labelled lost → failed (truthful)
UPDATE build_attempts
SET status = 'failed',
    error = 'shard execution failed',
    artifact_status = 'missing'
WHERE id IN (
    '0925dc59-9f71-496a-947c-0d04796ba914',
    '09be7a35-87ca-4338-b0cd-e250d26d8813'
)
  AND status = 'lost';

-- (c) Realign parent design_tasks.
-- Pending-shard parents go back to building (reconciler will move them again
-- after the worker actually starts/finishes).
UPDATE design_tasks
SET status = 'building',
    updated_at = NOW()
WHERE id IN (
    'd857612d-a55c-43bb-8965-29041c5a8503',
    'c9c75cbf-92e7-4def-8a89-0ee66654c391',
    'ca4e0896-cb89-49ea-8c48-a47dc28248ca'
)
  AND status = 'build_failed'
  -- only if at least one related attempt was restored to queued by step (a)
  AND EXISTS (
      SELECT 1 FROM build_attempts ba
      WHERE ba.design_task_id = design_tasks.id
        AND ba.status = 'queued'
  );

COMMIT;
