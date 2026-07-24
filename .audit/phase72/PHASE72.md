# Phase 7.2 — Verify, Re-rank, Fail-Open

**Findings:** 66 (7C / 20H / 25M / 11I / 3L). New: **F-0064 CRITICAL** (PF write-gap), **F-0065 HIGH** (RLS fail-open), **F-0066 MEDIUM** (post_id format mismatch).

## A — Join validated: `(platform, regexp_replace(post_id, '^[a-z_]+:', ''))`

`pending_feedback` has no `blueprint_id`. `pf.post_id` stores mixed formats (bare `HL2UiUr3gzg`, prefixed `instagram:17888...`); `pa.post_id` is consistently `platform:raw_id`. Exact-match caught 126/737 PA rows (17%); normalized catches 202 (27%). PF has 400 rewards in window; normalized matches 196 (49%). **F-0066 filed.**

**Per-post closure:** gaming 33.6% > sports 28.8% > ai_creators 26.8% > movies 21.9% > anime 21.7%.
**Per-reel closure:** gaming 61.0% > sports 54.9% > ai_creators 44.2% > anime 43.5% > movies 40.0%. **Aggregate 48.9%.**

**Decomposition:** F-0050 missing IG post_id = 24–39% of non-closure. `no_pf_row` for 44–53% of publishes across ALL platforms = the bigger burn. **F-0064 CRITICAL filed.**

## B — Re-ranking and DECISION.md revision

**B.1** ai_creators is **third** on closure, not first. Revised case for raising it: cleanest creative (0/43 vs gaming 51.2% F-0054) + lowest measured copyright. DECISION.md relied on velocity — that argument is dead.

**B.2** Sports ranks #2 on both closure measures. Per-channel 4-platform survival: ai_creators **20%** (worst), sports 25%, movies 25%, gaming 29%, anime 38%. **On survival evidence, pause ai_creators before sports.** DECISION.md's pause list named the wrong channel.

**B.3** 1×30d×48.9% = 14.7 rewards/channel/month ÷ 20 arms = 0.73/arm/month → 41 months to n=30. Fixing F-0064 → per-reel ~90%, ~2× velocity **without pausing anything**. 1 channel × 4/day = 4× multiplier but pauses 4. **Closure fix wins on total value** — same order of magnitude, zero brand loss, addresses a real defect.

DECISION.md re-issued with a Phase 7.2 revision banner citing F-0062 + F-0064.

## C — Fail-open RLS (F-0065 HIGH)

24 policies use `... OR current_setting('app.niche_id', true) IS NULL`. Unset GUC returns **all rows** — looks normal, not zero. `tenant_context.py:5` documents 34 `psycopg.connect` bypass sites that route around the six GUC-setting paths.

**Cutover to `genlab_app` is safe but insufficient** — stops rolbypassrls masking but fail-open OR-clause still lets unset paths cross tenants. Remediation order: (1) enumerate 34 bypass sites, (2) route through pg_connect or set_config, (3) drop `IS NULL` clause from all 24 policies, (4) cut role over. Steps 1–3 are the real F-0048 fix. Effort M–L.

## D — INTELLIGENCE re-read: HOLD 5

Corrected per-reel closure 48.9% (was phantom 66.3%). Loop closes on half of reels, arms move 29–52/niche/7d, verified trace stands. Score 5 defensible on that basis; citation corrected. F-0064 is a live ceiling — if fixed, INTELLIGENCE credibly moves to 6.

**F-0062 added to "What this audit got wrong":** eighth methodology error, second originating in a prompt (like F-0061); unlike F-0061, this one fed the decision, not the scorecard.

## E — Operator actions

**E.1 Anthropic:** account still `exhausted`. `console.anthropic.com` → billing → payment + auto-reload ($20/$50). Verify monitor reads non-zero.

**E.2 Port 5432:** compose bind `127.0.0.1:5432:5432` alone closes exposure. One-line YAML + `docker compose up -d postgres`; verify off-box `nc -zv 46.224.237.56 5432` refuses; next 12:05 IST publisher run confirms via new `publishing_analytics` row. Iptables backstop = defence-in-depth against future `-p` mistakes.

All shells exited. Read-only against prod.
