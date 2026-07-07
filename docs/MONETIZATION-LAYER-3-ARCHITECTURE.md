# Monetization Layer 3 — architecture

**Sprint**: 2026-07-07 (single-session, 19 commits)
**Scope**: end-to-end affiliate product bandit + real-revenue attribution
**Status**: machinery complete; L3 PR 12b (auto-flip enforcement)
deferred pending ≥7 days of divergence data

This doc is the map. Every referenced PR is on `main` and
fast-forward-merged. Every referenced module has pin tests.

---

## The problem this solves

Before this sprint, the audit ([[monetization-gap-analysis-2026-07-07]])
found:

* 30 days of clicks with **zero** attribution back to blueprints
* 4 of 5 niches with `affiliate_enabled: false` (intentional per an
  earlier audit that found 12-19× worse CTR on mismatched niches)
* Writer generates captions BEFORE the affiliate matcher runs
  (product-blind)
* The selector optimized commission % alone, missing the
  price × conversion joint (PS5 at ₹40K × 3% vs popcorn maker at
  ₹2500 × 3% = 30× value gap)

The proximate cause: no shared JOIN key between `bandit_arms`,
`blueprints`, and `affiliate_clicks`. All three stored the product
identity as a DIFFERENT string ("NVIDIA RTX 4090" vs
"nvidia-rtx-4090"), making attribution architecturally impossible.

---

## The data flow (complete loop)

```
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  │  PIPELINE (per-blueprint, at match time)                     │
  │                                                              │
  │  ┌──────────────┐   ┌──────────────────┐  ┌──────────────┐   │
  │  │  affiliate_  │→→→│   keyword_hits    │→→│ observation- │   │
  │  │   matcher    │   │  candidates: N    │  │ only wire    │   │
  │  └──────┬───────┘   └──────────────────┘  │ (L3 PR 5)    │   │
  │         │                   │              └──────┬───────┘   │
  │         ↓                   ↓                     ↓           │
  │  ┌──────────────┐   ┌──────────────────┐  ┌──────────────┐   │
  │  │  product_    │   │ ProductSelector   │  │  divergence  │   │
  │  │  slug on     │   │ Thompson × value  │  │  → journalctl│   │
  │  │  blueprint   │   │ weight (L3 PR 4)  │  │  → DB (12a)  │   │
  │  │  (L3 PR 2)   │   │                   │  │              │   │
  │  └──────┬───────┘   └──────────────────┘  └──────────────┘   │
  │         │                                                    │
  └─────────┼────────────────────────────────────────────────────┘
            │
            ↓
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  │  PUBLISHING (caption emission)                               │
  │                                                              │
  │  ┌─────────────────────────────────────┐                     │
  │  │ cta_engine._tracked_url             │                     │
  │  │ https://review.aspirehub.ai/links/  │                     │
  │  │   go/<slug>?bp=<blueprint_uuid>     │                     │
  │  │ (embedded in caption)               │                     │
  │  └──────────────┬──────────────────────┘                     │
  └─────────────────┼────────────────────────────────────────────┘
                    │
                    ↓ user clicks
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  │  BIO-LINK HUB (dashboard, redirect + tracking)              │
  │                                                              │
  │  ┌─────────────────────────────────────┐                     │
  │  │ /links/go/<slug>?bp=<uuid>          │                     │
  │  │   ↓                                 │                     │
  │  │ log_click() → affiliate_clicks      │                     │
  │  │   - blueprint_id: UUID (guarded)    │                     │
  │  │   - commission_pct: snapshot        │                     │
  │  │   - product_id: <slug>              │                     │
  │  │ (L3 PR 7)                           │                     │
  │  │   ↓                                 │                     │
  │  │ 302 → amazon.com/dp/X?tag=<utm>     │                     │
  │  └──────────────┬──────────────────────┘                     │
  └─────────────────┼────────────────────────────────────────────┘
                    │
                    ↓ user purchases (T+1 to T+30)
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  │  AMAZON DASHBOARD (external)                                 │
  │                                                              │
  │  Operator downloads daily CSV from associates.amazon.com     │
  │  Drops into $GENLAB_TMP/amazon-reports/                      │
  │                                                              │
  │  ┌─────────────────────────────────────┐                     │
  │  │ genlab-import-amazon-conversions    │  daily 05:45 UTC    │
  │  │ .timer                              │                     │
  │  │   ↓                                 │                     │
  │  │ INSERT ... ON CONFLICT DO NOTHING   │                     │
  │  │ (source_csv_hash, csv_line_no)      │                     │
  │  │ (L3 PR 10)                          │                     │
  │  └──────────────┬──────────────────────┘                     │
  │                 │                                            │
  │                 → affiliate_conversions                      │
  └─────────────────┼────────────────────────────────────────────┘
                    │
                    ↓
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  │  REWARD LAYER (daily)                                        │
  │                                                              │
  │  ┌─────────────────────────────────────┐                     │
  │  │ genlab-register-conversion-rewards  │  daily 06:15 UTC    │
  │  │ (L3 PR 11)                          │                     │
  │  │                                     │                     │
  │  │ UPDATE bandit_arms SET              │                     │
  │  │   alpha = 1 + COALESCE(purchases,   │                     │
  │  │                        clicks)      │                     │
  │  │   beta = 1 + max(0, clicks -        │                     │
  │  │                    purchases) OR 1  │                     │
  │  │ WHERE arm_type='product'            │                     │
  │  │   AND arm_id = 'product__' ||       │                     │
  │  │                product_id           │                     │
  │  └──────────────┬──────────────────────┘                     │
  └─────────────────┼────────────────────────────────────────────┘
                    │
                    ↓
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  │  CROSS-NICHE TRANSFER (weekly, Mon 05:30 UTC)                │
  │                                                              │
  │  ┌─────────────────────────────────────┐                     │
  │  │ scripts/refit_cross_niche_priors    │                     │
  │  │                                     │                     │
  │  │ extract_prior_key(arm_id) routes    │                     │
  │  │ 'product__<slug>' → 'product:<slug>'│                     │
  │  │ (L3 PR 6)                           │                     │
  │  │                                     │                     │
  │  │ Empirical Bayes moment matching:    │                     │
  │  │   µ = mean(observed_rate_i)         │                     │
  │  │   σ² = var(observed_rate_i)         │                     │
  │  │   → Beta(α, β) prior for cold arms  │                     │
  │  └─────────────────────────────────────┘                     │
  │                                                              │
  └──────────────────────────────────────────────────────────────┘
                    │
                    ↓
       (loops back to top: next blueprint gets fresher posteriors)
```

---

## The 3 load-bearing invariants

If any of these breaks, the whole loop silently fails:

### 1. Slug alignment across 4 write sites

`slugify_product_name()` (L3 PR 2) is the ONLY function that produces
the canonical slug form:

| Site                             | Writer                          |
|----------------------------------|---------------------------------|
| `bandit_arms.arm_id`             | L3 PR 3 registration            |
| `bandit_arms.dimension_value`    | L3 PR 3 registration            |
| `blueprints.product_slug`        | L3 PR 2 affiliate_matcher       |
| `affiliate_clicks.product_id`    | L3 PR 7 bio-link handler        |

**How it's pinned**: `test_slugify_product_name.py::test_alignment_...`
in each PR verifies the format matches.

### 2. `arm_type='product'` filter

Every consumer that reads product arms filters on
`arm_type='product'` — this prevents accidental crossover into
style / transformation / source / hour arms which use different
composite `arm_id` shapes.

**How it's pinned**: `test_register_click_rewards.py::
test_filters_on_product_arm_type` (and equivalents in
`test_register_conversion_rewards.py`, `test_api_product_bandit.py`).

### 3. Idempotence-by-construction of reward runners

Both `register_click_rewards` (L3 PR 8) and `register_conversion_rewards`
(L3 PR 11) use `alpha = 1 + expression` (pure set), not `alpha = alpha
+ delta` (incremental). Rerunning always converges.

**How it's pinned**: `test_register_conversion_rewards.py::
TestIdempotence::test_no_incremental_updates`.

---

## The PR ledger

### Phase A — Foundation

| PR | Commit    | Ships |
|----|-----------|-------|
| Layer 1 | `39d029ef` | `seasonal.yaml` + env var expansion in `seasonal.py` |
| L3 PR 1 | `aa52d38e` | Alembic `a8w9x0y1z2a3` — `product_slug`, `price_inr`, `blueprint_id` UUID FK, `commission_pct` |
| L3 PR 2 | `123cefa0` | `slugify_product_name()` + write-site wire + backfill script |
| L3 PR 3 | `b1087cd1` | `register_product_arms.py` — 108 arms |

### Phase B — Selector

| PR | Commit    | Ships |
|----|-----------|-------|
| L3 PR 4 | `2af37b93` | `ProductSelector` LinUCB module |
| L3 PR 5 | `afb7f298` | Matcher observation-only wire + `_log_selector_divergence` |
| L3 PR 6 | `581729cd` | Cross-niche transfer for product arms |

### Phase C — Attribution

| PR | Commit    | Ships |
|----|-----------|-------|
| L3 PR 7 | `7b2f3d2e` | Click UUID guard + commission_pct snapshot |
| L3 PR 8 | `91f2583a` | Reward wire (click → arm) + `register_click_rewards.py` |

### Phase D — Observability

| PR | Commit    | Ships |
|----|-----------|-------|
| L3 PR 9 | `574f6945` | `/api/v1/product-bandit/summary` endpoint |
| followup | `acd79875` | Click-rewards systemd timer (06:00 UTC) |
| followup | `147c0b36` | React ProductBanditCard on Mission Control |

### Phase E — Rollout hygiene

| PR | Commit    | Ships |
|----|-----------|-------|
| L3 PR 13 | `3f6dfbf5` | Catalog structural invariant pins (9 tests) |
| L3 PR 14 | `80151a14` | No-disabled-products pin + operator runbook |

### Phase F — Real revenue

| PR | Commit    | Ships |
|----|-----------|-------|
| L3 PR 10 | `5405d9fa` | Amazon CSV importer + `affiliate_conversions` table (migration `b9y0z1a2b3c4`) |
| L3 PR 11 | `5f60f6f6` | Bernoulli reward escalation + 3 systemd timer pairs |

### Phase G — Divergence tracking

| PR | Commit    | Ships |
|----|-----------|-------|
| L3 PR 12a | `cf21d734` | `selector_divergences` table (migration `c0z1a2b3c4d5`) + `_persist_divergence` + `/divergence-stats` endpoint |
| followup | `6e2d8082` | DivergenceStats React section on ProductBanditCard |
| followup | `b7cd11b5` | `scripts/monetization_preflight.py` readiness scanner |

**Deferred**:

* **L3 PR 12b** — per-niche auto-flip enforcement. Reads
  `/divergence-stats` for each niche; when `ready_for_enforcement=true`
  (≥30 samples + ≥90% agreement), flips a per-niche enforcement flag.
  Genuinely blocked on ≥7 days of accumulated divergence data.

---

## Feature flags (all default OFF)

| Flag                                    | Guards                          | Default |
|-----------------------------------------|--------------------------------|---------|
| `GENLAB_PRODUCT_SELECTOR_ENABLED`       | ProductSelector + divergence-log write | `false` |
| `GENLAB_CROSS_NICHE_TRANSFER_ENABLED`   | Cross-niche prior read side    | `false` |

Once flipped, they take effect on the next pipeline run — no restart
required (env vars re-read via `os.environ.get`).

Neither flag affects Phase A/C machinery. Migrations, arm registration,
click writes, and reward runners fire regardless. The flags gate the
STEERING side (whose picks are read by pipeline stages).

---

## Systemd timers

| Timer                                          | When | Fires |
|------------------------------------------------|------|-------|
| `genlab-import-amazon-conversions.timer`       | 05:45 UTC | L3 PR 10 CSV importer |
| `genlab-register-click-rewards.timer`          | 06:00 UTC | L3 PR 8 reward (retires when operator adopts conversion-rewards) |
| `genlab-register-conversion-rewards.timer`     | 06:15 UTC | L3 PR 11 Bernoulli superset |
| `genlab-cross-niche-transfer.timer` (existing) | Mon 05:30 UTC | Weekly prior refit |

Full daily sequence (times in UTC):

```
03:30 — genlab-anticipate-trends           (Intervention 5)
04:00 — genlab-late-reward                 (Intervention 1)
05:00 — genlab-anticipation-accuracy       (Mon only, Intervention 5b)
05:30 — genlab-cross-niche-transfer        (Mon only, Intervention 2)
05:45 — genlab-import-amazon-conversions   ← L3 PR 10
06:00 — genlab-register-click-rewards      ← L3 PR 8
06:15 — genlab-register-conversion-rewards ← L3 PR 11
06:30 — pipelines fire
```

---

## Operator activation sequence

After PR merge + deploy:

```bash
# 1. Land all migrations
alembic upgrade head

# 2. Backfill existing blueprints' product_slug
python scripts/backfill_product_slug.py --apply

# 3. Register initial 108 product arms
python scripts/register_product_arms.py --niche all

# 4. Apply catalog cleanup (5 disabled products)
# See docs/OPERATOR-monetization-cleanup-2026-07-07.md

# 5. Enable systemd timers
sudo systemctl daemon-reload
sudo systemctl enable --now genlab-import-amazon-conversions.timer
sudo systemctl enable --now genlab-register-click-rewards.timer
sudo systemctl enable --now genlab-register-conversion-rewards.timer

# 6. Verify readiness
python scripts/monetization_preflight.py

# 7. Flip selector flag to start accumulating divergence data
export GENLAB_PRODUCT_SELECTOR_ENABLED=true
# (add to .env; restart pipelines)

# 8. WAIT ~7 days for divergence data to accumulate

# 9. Check per-niche readiness for enforcement flip
curl "https://review.aspirehub.ai/api/v1/product-bandit/divergence-stats?niche_id=gaming"
# Look for "ready_for_enforcement": true

# 10. (Not yet shipped) L3 PR 12b flips per-niche enforcement
#     when ready_for_enforcement clears.
```

---

## Kill switches

If something goes wrong on prod:

| Situation | Kill switch |
|-----------|-------------|
| Selector picks wrong products | `unset GENLAB_PRODUCT_SELECTOR_ENABLED` → matcher-only again |
| Reward runner produces bad posteriors | `systemctl disable genlab-register-conversion-rewards.timer` → arms stay at last-written state |
| Amazon CSV import writes bogus data | `TRUNCATE affiliate_conversions;` — safe because idempotent-by-hash means re-importing rebuilds |
| A specific arm is bad | `UPDATE bandit_arms SET alpha=1, beta=1 WHERE arm_id=?` — resets to uniform prior |
| Cross-niche transfer produces bogus priors | `unset GENLAB_CROSS_NICHE_TRANSFER_ENABLED` → priors ignored at consume time |

Every runner is idempotent-by-construction. Re-running always
converges to the same state given the same source data.

---

## Rollback plan

If a migration needs to be reverted:

```bash
# Back out one migration at a time
alembic downgrade -1

# Migration chain (revision → what it added):
#   c0z1a2b3c4d5 → selector_divergences (L3 PR 12a)
#   b9y0z1a2b3c4 → affiliate_conversions (L3 PR 10)
#   a8w9x0y1z2a3 → blueprints.product_slug, price_inr,
#                  affiliate_clicks.blueprint_id, commission_pct (L3 PR 1)
```

All three migrations have working `downgrade()` functions with pin
tests. `alembic downgrade base` returns the DB to pre-L3 state.

Existing production data is preserved — all columns are nullable, all
tables are additive. Rolling back schema doesn't destroy blueprints,
clicks, or bandit state.

---

## Kill-switch defence: the observation-only design

Every consumer wire ships in observation-only mode first:

* L3 PR 5 wire logs divergence BUT returns matcher's pick
* L3 PR 12a persists divergence BUT doesn't change matcher return
* L3 PR 12b (not shipped) will flip enforcement per-niche when data
  clears the threshold

This means every deploy leaves production behavior UNCHANGED. Only
when the operator flips a flag AND enough data has accumulated does
the selector's pick become the caption's product. The flag flip is
per-niche, so a bad selector on gaming doesn't affect movies.

---

## Metrics + Mission Control

The operator surfaces:

* **`ProductBanditCard`** — per-niche top-5 products by Beta posterior
  mean. `flag_enabled` badge shows observation vs active state.
* **`DivergenceBadge`** (embedded in card) — per-niche
  agreement rate + ready-for-enforcement pill.
* **Preflight script** — one-command readiness scan; JSON output
  suitable for CI polling or a future health-check card.

---

## Sprint totals

* **19 commits** merged fast-forward to `main`
* **~15,000 lines** added
* **~220 pin tests** added across the sprint
* **3 alembic migrations** — one for schema, one for conversions,
  one for divergences
* **4 new systemd unit pairs**
* **3 new dashboard endpoints** — summary, divergence-stats, plus
  the auto-approval calibration-stats pattern this whole sprint
  followed
* **2 new Mission Control card sections**

---

## Related memories

* `[[sprint-monetization-l3-complete-2026-07-07]]` — session ledger
* `[[monetization-gap-analysis-2026-07-07]]` — the audit that
  motivated the sprint
* `[[intervention-2-cross-niche-transfer-shipped-2026-07-01]]` — base
  for L3 PR 6's routing extension
* `[[bandit-decision-architecture-2026-06-30]]` — LinUCB / Thompson
  conventions
* `docs/OPERATOR-monetization-cleanup-2026-07-07.md` — the catalog
  cleanup runbook (5 disabled products)
