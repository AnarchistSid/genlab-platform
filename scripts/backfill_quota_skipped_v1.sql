-- Backfill: re-classify historic quota-guard FAILED rows as SKIPPED.
--
-- PR D (2026-06-27) — paired with the parallel_publish.py taxonomy fix
-- that re-classifies QUOTA-class errors as SKIPPED at write-time (same
-- treatment that CREDENTIAL errors already get). This SQL corrects the
-- historical rows written before the code fix shipped.
--
-- Scope: rows where status='FAILED' and error_message mentions quota.
-- Prod evidence (2026-06-27): ~13 such rows over the last 60 days
-- on YouTube alone. Query covers ALL platforms in case other
-- platforms also hit quota-shaped errors.
--
-- One-time corrective migration; NOT idempotent in the strict sense
-- (running twice does nothing because rows are already SKIPPED after
-- the first run — the second run's UPDATE matches zero rows). That's
-- intended: there's no harm in re-running, but no work happens either.
--
-- The belt-and-suspenders ``NOT ILIKE '%credential%'`` clause is
-- defence against the (unlikely) error message that mentions both
-- "quota" and "credential" — we'd rather it stay FAILED than be
-- accidentally re-classified by a regex collision; an operator
-- inspecting the row can re-evaluate. The classifier's CREDENTIAL
-- patterns take precedence in the live code path so the same row
-- written today would already be SKIPPED via that branch.

BEGIN;

UPDATE publishing_analytics
SET status = 'SKIPPED'
WHERE status = 'FAILED'
  AND error_message ILIKE '%quota%'
  AND error_message NOT ILIKE '%credential%'  -- belt-and-suspenders
;

-- Verify the post-state. Expected: YouTube failure count drops by
-- ~13; SKIPPED count rises by the same amount; SUCCESS untouched.
SELECT
    platform,
    niche_id,
    COUNT(*) FILTER (WHERE status = 'SUCCESS') AS s,
    COUNT(*) FILTER (WHERE status = 'FAILED')  AS f,
    COUNT(*) FILTER (WHERE status = 'SKIPPED') AS sk
FROM publishing_analytics
WHERE created_at >= NOW() - INTERVAL '60 days'
GROUP BY platform, niche_id
ORDER BY platform, niche_id;

COMMIT;
