-- Backfill engagement, engagement_rate, viral_score for rows where
-- analytics_store.py's pre-2026-06-26 bug zeroed them (see PR #588).
--
-- v2 (2026-06-26): SUPERSEDES the original which assumed a wide-column
-- analytics schema. Production analytics is long-narrow EAV with all
-- numeric fields inside `extra` jsonb. The original SQL fails with
-- `ERROR: column "engagement" does not exist` on production schema.
--
-- Scope: rows where extra->>'engagement' = 0 but raw
-- (likes + comments + shares) > 0 AND reach > 0. Confirmed ~33 rows on
-- prod when run 2026-06-26.
--
-- Idempotent: re-running on already-corrected rows is a no-op.
-- Side note: a follow-up PR clamps engagement_rate <= 1.0 at the
-- write site; until that lands, this backfill can produce
-- engagement_rate > 1.0 for tiny-reach posts. The clamp PR will
-- backfill those separately.

BEGIN;

WITH backfill_targets AS (
    SELECT
        id,
        (COALESCE((extra->>'likes')::numeric, 0)
         + COALESCE((extra->>'comments')::numeric, 0)
         + COALESCE((extra->>'shares')::numeric, 0)) AS new_engagement,
        COALESCE((extra->>'reach')::numeric, 0) AS reach,
        COALESCE((extra->>'shares')::numeric, 0) AS shares,
        COALESCE((extra->>'saved')::numeric, 0) AS saves
    FROM analytics
    WHERE metric_type = 'composite'
      AND (extra->>'engagement')::numeric = 0
      AND (COALESCE((extra->>'likes')::numeric, 0)
          + COALESCE((extra->>'comments')::numeric, 0)
          + COALESCE((extra->>'shares')::numeric, 0)) > 0
      AND COALESCE((extra->>'reach')::numeric, 0) > 0
      AND collected_at >= '2026-06-09'::timestamptz
)
UPDATE analytics a
SET extra = jsonb_set(
    jsonb_set(
        jsonb_set(
            a.extra,
            '{engagement}', to_jsonb(b.new_engagement::bigint), true
        ),
        '{engagement_rate}', to_jsonb(ROUND(b.new_engagement / b.reach, 4)), true
    ),
    '{viral_score}', to_jsonb(ROUND(
        ROUND(b.new_engagement / b.reach, 4) * 0.25
        + ROUND(b.shares / b.reach, 4) * 0.40
        + ROUND(b.saves / b.reach, 4) * 0.35,
    4)), true
)
FROM backfill_targets b
WHERE a.id = b.id;

-- Report
SELECT
    COUNT(*) AS rows_with_nonzero_engagement,
    COUNT(*) FILTER (WHERE (extra->>'engagement_rate')::numeric > 0) AS rows_with_nonzero_rate,
    COUNT(*) FILTER (WHERE (extra->>'viral_score')::numeric > 0) AS rows_with_nonzero_viral
FROM analytics
WHERE metric_type = 'composite'
  AND (extra->>'engagement')::numeric > 0
  AND collected_at >= '2026-06-09'::timestamptz;

COMMIT;
