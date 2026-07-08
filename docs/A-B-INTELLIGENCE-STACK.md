# A + B Intelligence Stack — architecture

**Sprint**: 2026-07-07 → 2026-07-08 (2-day session, 5 commits)
**Scope**: end-to-end top-creator learning — timing (A) + structural
correlations (B) — from config to Mission Control observability
**Status**: OBSERVATION-ONLY across the board; consumer wires
deferred by design pending validation data

This doc is the map. Every referenced commit is on `main` and
fast-forward-merged. Every referenced module has pin tests.

---

## The problem this solves

The operator asked: *"Why can't the agent continuously learn from
top-performing channels in each niche?"* ([[top-creator-learning-scope-2026-07-07]])

The honest answer has two halves:

### What CAN'T transfer (the "correlation ≠ causation" trap)

Copying a 30M-subscriber channel's hook doesn't inherit their 15M
subs. Their viral hit's attribution is:

* ~90% existing audience
* ~5% algorithm push
* ~5% actual content quality

The reward signal doesn't transfer either — the bandit trains on
`(pattern → observed views)` and their `pattern → 5M views` isn't
a valid training example for a 200-view channel. This is textbook
Simpson's paradox territory.

### What CAN transfer (two honest signals)

**A — Timing (leading indicator)**. When a top-tier creator
publishes about topic X, X is trending several hours before Google
Trends catches it. Feed this into `TrendAnticipation` as a fifth
signal so the pipeline PRIORITIZES what to publish. No causal
claim about style.

**B — Structural feature priors (weak, correlational)**. Compute
Spearman correlations between structural features (title length,
question format, publish hour, ...) and view velocity across each
niche's top-100 clips. Map correlations to WEAK bandit prior
adjustments — an order of magnitude weaker than actual observed
reward, so the bandit's own observations quickly override.

## The data flow (complete loop)

```
config/top_creators.yaml (per niche)
        │
        │ [A.2] scripts/watch_top_creator_uploads.py
        │       4×/day systemd timer — polls YouTube playlistItems
        │       for each watchlist channel
        ▼
$GENLAB_TMP/top-creator-uploads/YYYYMMDD-<niche>.json
        │
        ├─── [A.3] trend_anticipation._signal_creator_upload_lead
        │         Reads today's/yesterday's artifact, matches topic
        │         with word-boundary regex, recency-weights uploads
        │         (≤6h→1.0, 6-24h→linear decay, >24h→0.0), saturates
        │         at min(len(creators), 3)
        │         Returns None (unavailable) or float in [0, 1]
        │         Contributes to 5-signal composite at weight 0.10
        │
        └─── Mission Control /api/v1/top-creator-priors/uploads
                  Server-injects flag_enabled + artifact_stamp
                  React card shows fresh=N/M counter using same
                  ≤24h threshold as A.3

YouTube Data API v3 mostPopular chart (per niche)
        │
        │ [B.2] scripts/refit_top_creator_priors.py
        │       Weekly Sunday systemd timer — fetches ~24 videos
        │       per niche category
        ▼
Per-video structural features (B.1's extract_features):
title_length_chars, title_ends_with_question, title_starts_with_number,
title_number_count, title_emoji_count, title_allcaps_word_count,
description_length_chars, description_first_line_length,
description_has_timestamps, tags_count, publish_hour_utc,
publish_day_of_week
        │
        │ [B.1] compute_feature_view_correlation
        │       Pure-Python Spearman rank correlation
        │       (robust to view-count log-normal outliers)
        ▼
$GENLAB_TMP/top-creator-priors/YYYYMMDD-<niche>.json
        │
        ├─── [B.3] top_creator_priors.load_correlations
        │         Reads today's; falls back within 8-day window
        │         Flag-gated: returns None even when file exists
        │         if GENLAB_TOP_CREATOR_PRIORS_ENABLED != "true"
        │
        ├─── [B.3] correlation_to_prior_delta
        │         Pure math: r > 0 → α bump, r < 0 → β bump,
        │         bounded at MAX_DELTA = 2.0 (deliberately weak)
        │
        ├─── [B.3] get_arm_prior_adjustment (consumer surface)
        │         None = feature unknown / flag off / no artifact
        │         (0, 0) = feature present but r=0 (informative zero)
        │
        │         ← CONSUMER WIRE NOT SHIPPED (see below)
        │
        └─── Mission Control /api/v1/top-creator-priors/latest
                  Server-injects flag_enabled + artifact_stamp
                  React card shows top-|r| feature + Spearman value
                  with sign preserved
```

## The 3 load-bearing invariants

### 1. None vs zero distinction (pinned by tests)

Every signal function in the stack uses the same 3-state semantics:

* `None` — signal source unavailable (missing artifact, malformed
  JSON, unknown feature). Caller redistributes / abstains.
* `0.0` — measured, found uncorrelated. **Informative zero** —
  contributes real mass to the composite.
* `(positive, 0)` / `(0, positive)` — real signal.

This distinction is what prevents silent signal-mass loss (see
[[silent-loss-bug-class-pattern]] for the class of bugs it
prevents). Pinned in `test_creator_upload_lead_signal.py::
TestArtifactDiscovery` (None cases) + `test_zero_matches_returns_zero`
(informative-zero case).

### 2. Weight-mass conservation (5 signals, sum = 1.00)

After A.3's rebalance the composite weights are:

| Signal | Weight | Reasoning |
|--------|--------|-----------|
| `search_velocity` | 0.55 | Sharpest math (2nd derivative), most mature |
| `creator_pickup` | 0.15 | Broad-YouTube search — wider net |
| `creator_upload_lead` | 0.10 | Narrow (only ~10 curated creators) but sharpest leading indicator |
| `social_velocity` | 0.13 | Reddit karma rate |
| `news_lead` | 0.07 | RSS lead — weakest of the 5 |

Pinned by `TestWeightsInvariant::test_weight_sum`,
`test_all_5_signals_present`, and `test_search_velocity_still_dominant`.

### 3. Observation-only discipline (pinned by test invariant)

B.3's `TestObservationOnlyDiscipline` asserts:

```python
def test_public_surface_is_read_only(self):
    assert set(tcp.__all__) == {
        "MAX_DELTA",
        "correlation_to_prior_delta",
        "get_arm_prior_adjustment",
        "load_correlations",
    }

def test_no_side_effect_helper_exists(self):
    assert not hasattr(tcp, "apply_prior_to_arm")
    assert not hasattr(tcp, "update_arm_alpha_beta")
```

A future PR that tries to sneak in a side-effecting helper without
also updating the scope memo and this test fails CI. This is the
architectural boundary expressed as a machine-enforced invariant —
same pattern React side uses (see the "two badges never merge"
test in `TopCreatorPriorsCard.test.tsx`).

## The commit ledger

| Ship | Commit | Files | Tests |
|------|--------|-------|-------|
| A.1 | `99125cc8` | `top_creators.yaml` × 5 niches + `top_creators_config.py` + loader | 15 |
| A.2 | `31155c76` | `watch_top_creator_uploads.py` + systemd units | 18 |
| B.1 | `8db8ef94` | `top_creator_features.py` extractor + pure-Python Spearman | 34 |
| A.3 | `5a314132` | `trend_anticipation._signal_creator_upload_lead` + 5-signal weight rebalance | 18 (16 new + 2 rebalance) |
| B.2 + B.3 | `786b20e2` | `refit_top_creator_priors.py` + systemd + `top_creator_priors` module | 40 (14 + 26) |
| MC API | `fcd45feb` | `dashboard/server/api/top_creator_priors.py` + review_server registration | 14 |
| React card | `57d918ad` | `TopCreatorPriorsCard.tsx` + api client/types/keys + MissionControl mount | 7 |

Sprint total: 146 new/updated pin tests. All commits fast-forward-
merged to `main`. Full sweep runs green at:

* `pytest genlab-core/tests/` — 827 passed, 81 skipped
* `pytest dashboard/tests/` — 21 passed (7 sibling + 14 new)
* `npx vitest run src/views/mission-control/__tests__/` — 130
  passed (7 new)

## Feature flags (all default OFF)

Every runner and consumer is gated behind an exact-match
`"true"` / `"TRUE"` / `"True"` env var. NO strip, whitespace counts
as unset. Consistent flag-parsing across the stack.

| Flag | Gates | Default |
|------|-------|---------|
| `GENLAB_TOP_CREATORS_ENABLED` | A.2 poll runner (if off → no-op) | off |
| `GENLAB_TREND_ANTICIPATION_ENABLED` | A.3's pipeline read-side steering | off |
| `GENLAB_TOP_CREATOR_PRIORS_ENABLED` | B.2 runner + B.3's `load_correlations` return | off |

The runners themselves ALWAYS run when their systemd timer fires;
the flag guards whether downstream code CONSUMES the artifact.
This is what the sprint memo calls the **observation-only** phase.

## Systemd timers

Two new timer/service pairs shipped:

```
deploy/systemd-phase2/
├── genlab-watch-top-creators.service       ← A.2 runner
├── genlab-watch-top-creators.timer         ← 4× daily @ 02/08/14/20 UTC
├── genlab-refit-top-creator-priors.service ← B.2 runner
└── genlab-refit-top-creator-priors.timer   ← Weekly Sun 04:00 UTC
```

Both are `Persistent=true` so a missed fire catches up on next boot.
Both invoke scripts via file path (not `python -m`) because
`scripts/` is not a Python package — pinned by
`tests/runbooks/test_systemd_module_drift.py`.

## Quota budget

YouTube Data API v3 quota is 10,000 units/day. This stack's cost:

| Runner | Cadence | Cost / fire | Monthly |
|--------|---------|-------------|---------|
| A.2 (watch creators) | 4×/day | 10 units (10 creators × 1 unit; playlist-ID cache reduces from 2) | ~1200 units |
| B.2 (refit priors) | 1×/week | 4 units (4 niches × mostPopular) | ~17 units |

Combined: ~1220 units/month = **~0.4% of daily budget**. Effectively
free.

## Rollout gate — when does the consumer wire land?

The consumer wire (`get_arm_prior_adjustment` invoked at arm
instantiation) is DEFERRED. Two prerequisites:

### Prerequisite 1 — stable arm↔feature mapping

Current arm ID space is `content_type__platform` (see
`meta_prior.TRANSFER_MATRIX`). B.1's feature space is
title/description/timing. No natural 1:1 today.

The mapping stabilises once the transformation orchestrator's 11
dimensions (music_mood, caption_style, caption_pacing, etc.)
accumulate enough reward data that specific features correlate
with specific arm rewards. Estimated: **4-8 weeks** of live-fire
data at current publish cadence.

### Prerequisite 2 — correlation stability

Weekly B.2 refits should agree within ±0.1 for the same feature
across ≥ 2 consecutive weeks. If Spearman correlations swing
week-to-week, the priors would inject noise rather than signal.

Both green → the consumer wire is a **one-line adopter** at
`arm_loader.save_arm`:

```python
# Pseudo — landing PR
for feature in _ARM_TO_FEATURES.get(arm_id, []):
    adj = get_arm_prior_adjustment(niche_id, feature)
    if adj is not None:
        alpha += adj[0]
        beta += adj[1]
```

## Mission Control observability

Two Flask endpoints + one React card ship the operator surface:

| Endpoint | Fallback window | Consumer |
|----------|----------------|----------|
| `/api/v1/top-creator-priors/latest?niche_id=X` | 8 days | B.2 correlations |
| `/api/v1/top-creator-priors/uploads?niche_id=X` | 1 day | A.2 uploads |

The 1-day upload window is deliberate — it matches A.3's recency-
weight zeroing at >24h. Showing older would mislead the operator
about active-signal state.

**`TopCreatorPriorsCard`** on Mission Control shows 5 rows (one per
niche), each with:

* Two independent flag badges (`B.2` + `A.2`) with tooltip explaining
  active-vs-observation-only state
* Top-|r| feature name (truncated to 22 chars) + Spearman value
  with sign preserved and color-coded
* `fresh=N/M` upload counter — mirrors A.3's ≤24h threshold so the
  card can't lie about what the composite will actually see

Same visual language as `CounterfactualReplayCard` per
`dashboard/CLAUDE.md`'s intelligence-engine card discipline.

## Kill switches

If any part of the stack misbehaves in prod:

1. **A.2 runaway** — `systemctl mask genlab-watch-top-creators.timer`
   (stops the 4×/day fire; artifact stays valid for 24h)
2. **B.2 runaway** — `systemctl mask genlab-refit-top-creator-priors.timer`
3. **A.3 poisoning the composite** — set `GENLAB_TREND_ANTICIPATION_ENABLED`
   OFF in `.env` and restart the pipeline units. The runner keeps
   writing artifacts but nothing consumes them.
4. **Full stack disable** — delete the two artifact dirs and unset
   all three flags. Card renders "No data yet" gracefully; A.3
   redistributes weight to the other 4 signals.

## Follow-ups (not blocked on prod data)

Small additions that would extend the stack without needing
validation:

* **Cross-niche fallback for feature priors**. If sports has < 24
  videos in a week's mostPopular (edge case), inherit gaming's
  correlations weighted by `meta_prior.TRANSFER_MATRIX`. Extends
  the existing hierarchical Bayes pattern; ~50 LOC.
* **Correlation stability card**. `TopCreatorPriorsStabilityCard`
  that overlays the last 4 weeks of correlations per feature so
  the operator can eyeball the ±0.1 threshold visually rather than
  hand-compute it.
* **A.3 weight-decay accuracy runner**. Weekly Spearman-r validation
  of `creator_upload_lead` against observed peak timing, same
  pattern as `measure_anticipation_accuracy.py` for the existing
  4 signals.

## Related

* [[top-creator-learning-scope-2026-07-07]] — the scope discipline
  memo (correlation ≠ causation)
* [[intervention-5-a3-creator-upload-lead-shipped-2026-07-08]] —
  A.3 detail memory
* [[intervention-b23-top-creator-priors-shipped-2026-07-08]] —
  B.2 + B.3 + observability detail memory
* [[intervention-2-cross-niche-transfer-shipped-2026-07-01]] —
  same "READ ships, consumer wire deferred" pattern as B.3
* [[silent-loss-bug-class-pattern]] — the None-vs-zero discipline
  applied throughout this stack
* `dashboard/CLAUDE.md` — the intelligence-engine card discipline
  the React card follows
