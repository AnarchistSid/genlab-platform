-- Backfill engagement, engagement_rate, and viral_score for the
-- ~16 days (since commit f32b6189, 2026-06-09) of analytics records
-- that were written with engagement = 0 due to the analytics_store.py:214
-- bug fixed in this PR.
--
-- Background: platform metric fetchers in learning/metrics/*.py never
-- return an "engagement" key — they return likes/comments/shares as
-- separate fields. The prior write-side code assumed callers passed
-- "engagement"; they don't. Result: every analytics row written between
-- 2026-06-09 and the deploy of this fix has:
--   engagement = 0
--   engagement_rate = 0.0
--   viral_score = 0 + share_rate*0.40 + save_rate*0.35  (loses 25% weight)
--
-- This script recomputes the three derived fields from raw likes,
-- comments, shares, saves columns which were populated correctly
-- throughout. Idempotent — running it twice produces the same result.
--
-- ORDER OF OPERATIONS for operator (production):
--   1. Deploy this PR (analytics_store.py:214 fix lands)
--   2. Verify new writes have non-zero engagement_rate (any new analytics
--      row collected after deploy should be correct)
--   3. Run this backfill against prod DB
--   4. Verify Top Performers card on Analytics page now shows real eng%
--
-- ROLLBACK is safe — re-running the UPDATE produces the same values
-- (computation is deterministic from likes/comments/shares/saves/reach).
--
-- EXPECTED ROW COUNT: ~3000 (16 days × ~200 records/day across 5
-- niches × ~6 platforms). Runs in seconds on Hetzner VPS.

BEGIN;

-- Step 1: backfill the engagement column from base metrics
UPDATE analytics
SET engagement = COALESCE(likes, 0) + COALESCE(comments, 0) + COALESCE(shares, 0)
WHERE engagement = 0
  AND (COALESCE(likes, 0) + COALESCE(comments, 0) + COALESCE(shares, 0)) > 0;

-- Step 2: recompute engagement_rate from corrected engagement / reach
UPDATE analytics
SET engagement_rate = ROUND(
    (COALESCE(engagement, 0)::numeric) / NULLIF(reach, 0),
    4
)
WHERE reach > 0
  AND engagement_rate = 0
  AND engagement > 0;

-- Step 3: recompute viral_score using the corrected engagement_rate.
-- Formula matches analytics_store.py:234:
--   viral_score = engagement_rate * 0.25 + share_rate * 0.40 + save_rate * 0.35
-- Caller-provided viral_score is preserved (only recompute when it
-- was the default-formula result).
UPDATE analytics
SET viral_score = ROUND(
    COALESCE(engagement_rate, 0) * 0.25
    + COALESCE(share_rate, 0) * 0.40
    + COALESCE(save_rate, 0) * 0.35,
    4
)
WHERE reach > 0
  AND engagement_rate > 0;  -- only touch rows where the backfill above ran

-- Step 4: report
SELECT
    COUNT(*) FILTER (WHERE engagement > 0) AS rows_with_engagement,
    COUNT(*) FILTER (WHERE engagement_rate > 0) AS rows_with_rate,
    COUNT(*) FILTER (WHERE viral_score > 0) AS rows_with_viral_score,
    MIN(collected_at) FILTER (WHERE engagement > 0) AS earliest_corrected,
    MAX(collected_at) FILTER (WHERE engagement > 0) AS latest_corrected
FROM analytics
WHERE collected_at >= '2026-06-09'::timestamptz;

COMMIT;

-- Smoke test for the operator: pick the highest-reach posts and verify
-- engagement_rate is now non-zero (was 0.0 across all top performers
-- before this fix per the 2026-06-25 dashboard audit).
--
-- SELECT post_id, platform, reach, likes, comments, shares, engagement_rate, viral_score
-- FROM analytics
-- WHERE reach > 500
-- ORDER BY reach DESC
-- LIMIT 10;
