-- Idempotent clamp: existing analytics rows with extra->>'engagement_rate' > 1.0
-- get capped at 1.0. Companion to the analytics_store.py clamp in the same PR.
-- Re-running is a no-op once values are <= 1.0.

BEGIN;

UPDATE analytics a
SET extra = a.extra
    || jsonb_build_object(
        'engagement_rate', LEAST(1.0, COALESCE((a.extra->>'engagement_rate')::numeric, 0))::float8
    )
    || jsonb_build_object(
        'save_rate', LEAST(1.0, COALESCE((a.extra->>'save_rate')::numeric, 0))::float8
    )
    || jsonb_build_object(
        'share_rate', LEAST(1.0, COALESCE((a.extra->>'share_rate')::numeric, 0))::float8
    )
    || jsonb_build_object(
        'play_rate', LEAST(1.0, COALESCE((a.extra->>'play_rate')::numeric, 0))::float8
    )
WHERE metric_type = 'composite'
  AND (
       COALESCE((a.extra->>'engagement_rate')::numeric, 0) > 1.0
    OR COALESCE((a.extra->>'save_rate')::numeric, 0) > 1.0
    OR COALESCE((a.extra->>'share_rate')::numeric, 0) > 1.0
    OR COALESCE((a.extra->>'play_rate')::numeric, 0) > 1.0
  );

-- Recompute viral_score from clamped rates (viral_score derives from the rates
-- above, so an un-clamped viral_score can outpace its inputs).
UPDATE analytics a
SET extra = jsonb_set(
    a.extra,
    '{viral_score}',
    to_jsonb(ROUND(
        COALESCE((a.extra->>'engagement_rate')::numeric, 0) * 0.25
        + COALESCE((a.extra->>'share_rate')::numeric, 0) * 0.40
        + COALESCE((a.extra->>'save_rate')::numeric, 0) * 0.35,
    4)::float8),
    true
)
WHERE metric_type = 'composite';

SELECT
    COUNT(*) FILTER (WHERE (extra->>'engagement_rate')::numeric > 1.0) AS rate_above_one_remaining,
    COUNT(*) FILTER (WHERE (extra->>'engagement_rate')::numeric = 1.0) AS rate_at_one,
    MAX((extra->>'engagement_rate')::numeric) AS max_rate
FROM analytics
WHERE metric_type = 'composite';

COMMIT;
