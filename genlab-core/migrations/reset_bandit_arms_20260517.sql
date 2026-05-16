-- Reset bandit_arms Thompson posteriors to uniform priors.
--
-- Context (from 2026-05-16 learning-loop audit):
--   Bandit posteriors became pathologically pessimistic because the
--   PerformanceLearner pipeline stage was replaying historical analytics
--   with a hardcoded threshold of 0.5 against a reward distribution
--   whose 98.75th percentile sat below 0.5.  Every well-explored arm
--   converged on α/(α+β) ≈ 0.3% — Thompson Sampling cannot
--   discriminate between arms in that regime.
--
--   The math bugs are fixed in the accompanying commit.  This script
--   resets the polluted Thompson state so future updates start from a
--   clean Beta(1,1) prior.
--
-- What's preserved:
--   * linucb_state JSONB — the A_matrix and b_vector reflect real
--     contextual observations (50+ for well-explored arms) and are
--     useful warm-start data for future LinUCB-driven decisions.
--   * Row identities (id, niche_id, arm_id) — no rows deleted.
--   * created_at — arm provenance is intact.
--
-- What's reset:
--   * alpha, beta back to 1.0 (uniform Beta prior)
--   * n_plays to 0
--   * extra.LastUpdated stamp refreshed
--
-- Safety:
--   * Creates a timestamped backup table before mutation.
--   * Idempotent: running twice produces the same result.
--   * Backup table is preserved (DROP it manually after verification).
--
-- Usage on production (Hetzner):
--   docker exec -i genlab-postgres psql -U genlab -d genlab \
--     < genlab-core/migrations/reset_bandit_arms_20260517.sql

\set ON_ERROR_STOP on

BEGIN;

-- 1. Backup current state into a timestamped table.
CREATE TABLE IF NOT EXISTS bandit_arms_pre_reset_20260517 AS
SELECT * FROM bandit_arms;

-- 2. Report what's about to change.
\echo === Pre-reset snapshot ===
SELECT
    niche_id,
    COUNT(*) AS arms,
    ROUND(MIN(alpha)::numeric, 2) AS min_alpha,
    ROUND(MAX(alpha)::numeric, 2) AS max_alpha,
    ROUND(MIN(beta)::numeric, 2) AS min_beta,
    ROUND(MAX(beta)::numeric, 2) AS max_beta,
    ROUND(MIN(alpha / NULLIF(alpha + beta, 0))::numeric, 4) AS min_post_mean,
    ROUND(MAX(alpha / NULLIF(alpha + beta, 0))::numeric, 4) AS max_post_mean
FROM bandit_arms
GROUP BY niche_id
ORDER BY niche_id;

-- 3. Reset Thompson posteriors.  Preserves linucb_state.
UPDATE bandit_arms
SET
    alpha = 1.0,
    beta = 1.0,
    n_plays = 0,
    updated_at = NOW(),
    extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
        'LastUpdated', NOW()::text,
        'reset_at', NOW()::text,
        'reset_reason', 'learning-loop-audit-20260516'
    );

-- 4. Verify.
\echo === Post-reset snapshot ===
SELECT
    niche_id,
    COUNT(*) AS arms,
    ROUND(MIN(alpha)::numeric, 2) AS min_alpha,
    ROUND(MAX(beta)::numeric, 2) AS max_beta,
    COUNT(*) FILTER (WHERE linucb_state IS NOT NULL AND linucb_state != '{}'::jsonb) AS arms_with_linucb
FROM bandit_arms
GROUP BY niche_id
ORDER BY niche_id;

COMMIT;

\echo Reset complete.  Backup retained at bandit_arms_pre_reset_20260517.
\echo Drop it manually after validating one full feedback-collector cycle:
\echo   DROP TABLE bandit_arms_pre_reset_20260517;
