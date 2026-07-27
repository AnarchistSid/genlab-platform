-- F-0080 tripwire verification for the 12:05 IST 2026-07-28 publisher run.
-- Run after 12:05 to distinguish three outcomes:
--   * passthrough=0, real_hooks>0  -> WIN. Guard works, writer works.
--   * passthrough=0, published=0   -> FREEZE. Guard works, empty balance starves it.
--   * passthrough>0                -> The :208 guard has a sibling site; investigate.
--
-- The changelog's original verification SQL counted passthrough only; it could
-- not distinguish "guard prevented terminated-format hooks" from "nothing
-- published at all." This query adds the denominator.

SET default_transaction_read_only = on;
SET statement_timeout = '30s';
\pset pager off

SELECT b.niche_id,
       count(*)                                              AS published,
       count(*) FILTER (WHERE b.hook_text = s.title)         AS passthrough,
       count(*) FILTER (WHERE b.hook_text <> s.title)        AS real_hooks
FROM publishing_analytics p
JOIN blueprints b ON b.id = p.blueprint_id
JOIN stories    s ON s.story_id = b.story_id
WHERE p.published_at::date = '2026-07-28'
  AND p.post_id IS NOT NULL
GROUP BY 1 ORDER BY 1;

-- Also count blueprints CREATED today per niche vs blueprints PUBLISHED — if
-- creation happened but publish is 0, F-0080 dropped everything (freeze).
-- If creation is 0, the upstream pipeline never ran (different problem).
SELECT b.niche_id,
       count(DISTINCT b.id) AS created_today,
       count(DISTINCT b.id) FILTER (
         WHERE b.status IN ('PUBLISHED')
      OR b.id IN (SELECT blueprint_id FROM publishing_analytics
                  WHERE published_at::date = '2026-07-28' AND post_id IS NOT NULL)
       ) AS published_today
FROM blueprints b
WHERE b.created_at::date = '2026-07-28'
GROUP BY 1 ORDER BY 1;
