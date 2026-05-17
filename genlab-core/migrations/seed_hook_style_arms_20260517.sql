-- Seed bandit_arms with hook style:{niche}:{name} arms per niche.
--
-- Each niche gets 5 style arms (question, bold_claim, controversy,
-- revelation, comparison) at Beta(1,1) prior. The LLM hook generator
-- (genlab_core.writing.llm_hook_generator.pick_hook_style) reads
-- these and Thompson-samples a style each time a hook is generated;
-- the chosen style becomes a one-line hint in the LLM system prompt.
--
-- Naming: arm_id has format "style:{niche}:{name}" because the
-- bandit_arms table has UNIQUE(arm_id), not UNIQUE(niche_id, arm_id).
-- The niche segment in the arm_id is redundant with the niche_id
-- column but required to avoid collisions across niches. Tracked as
-- a follow-up schema migration.
--
-- Idempotent: ON CONFLICT (arm_id) DO NOTHING means re-running is a
-- no-op. Doesn't reset alpha/beta on existing rows.
--
-- Usage on production (Hetzner):
--   docker exec -i genlab-postgres psql -U genlab -d genlab \
--     < genlab-core/migrations/seed_hook_style_arms_20260517.sql

\set ON_ERROR_STOP on

BEGIN;

INSERT INTO bandit_arms (niche_id, arm_id, alpha, beta, n_plays, extra)
VALUES
    ('ai_creators', 'style:ai_creators:question',    1.0, 1.0, 0, '{}'::jsonb),
    ('ai_creators', 'style:ai_creators:bold_claim',  1.0, 1.0, 0, '{}'::jsonb),
    ('ai_creators', 'style:ai_creators:controversy', 1.0, 1.0, 0, '{}'::jsonb),
    ('ai_creators', 'style:ai_creators:revelation',  1.0, 1.0, 0, '{}'::jsonb),
    ('ai_creators', 'style:ai_creators:comparison',  1.0, 1.0, 0, '{}'::jsonb),
    ('gaming',      'style:gaming:question',         1.0, 1.0, 0, '{}'::jsonb),
    ('gaming',      'style:gaming:bold_claim',       1.0, 1.0, 0, '{}'::jsonb),
    ('gaming',      'style:gaming:controversy',      1.0, 1.0, 0, '{}'::jsonb),
    ('gaming',      'style:gaming:revelation',       1.0, 1.0, 0, '{}'::jsonb),
    ('gaming',      'style:gaming:comparison',       1.0, 1.0, 0, '{}'::jsonb),
    ('sports',      'style:sports:question',         1.0, 1.0, 0, '{}'::jsonb),
    ('sports',      'style:sports:bold_claim',       1.0, 1.0, 0, '{}'::jsonb),
    ('sports',      'style:sports:controversy',      1.0, 1.0, 0, '{}'::jsonb),
    ('sports',      'style:sports:revelation',       1.0, 1.0, 0, '{}'::jsonb),
    ('sports',      'style:sports:comparison',       1.0, 1.0, 0, '{}'::jsonb),
    ('movies',      'style:movies:question',         1.0, 1.0, 0, '{}'::jsonb),
    ('movies',      'style:movies:bold_claim',       1.0, 1.0, 0, '{}'::jsonb),
    ('movies',      'style:movies:controversy',      1.0, 1.0, 0, '{}'::jsonb),
    ('movies',      'style:movies:revelation',       1.0, 1.0, 0, '{}'::jsonb),
    ('movies',      'style:movies:comparison',       1.0, 1.0, 0, '{}'::jsonb),
    ('anime',       'style:anime:question',          1.0, 1.0, 0, '{}'::jsonb),
    ('anime',       'style:anime:bold_claim',        1.0, 1.0, 0, '{}'::jsonb),
    ('anime',       'style:anime:controversy',       1.0, 1.0, 0, '{}'::jsonb),
    ('anime',       'style:anime:revelation',        1.0, 1.0, 0, '{}'::jsonb),
    ('anime',       'style:anime:comparison',        1.0, 1.0, 0, '{}'::jsonb)
ON CONFLICT (arm_id) DO NOTHING;

\echo === Seeded style arms per niche ===
SELECT niche_id, COUNT(*) FILTER (WHERE arm_id LIKE 'style:%') AS style_arms
FROM bandit_arms GROUP BY niche_id ORDER BY niche_id;

COMMIT;
