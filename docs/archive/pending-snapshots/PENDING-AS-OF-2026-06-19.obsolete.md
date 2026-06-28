# Pending work — refresh as of 2026-06-19 EOD

Snapshot after a continuous 2026-06-18 → 2026-06-19 **40-PR arc**.
Supersedes `PENDING-AS-OF-2026-06-18-night.obsolete.md`.

The day's work split into three named waves:

1. **U-24 closure** (#349 → #350 → #351) — multi-day starlette
   blocker collapsed to a 2-character fix once the
   `skipif(boolean)` vs `skipif("string")` distinction clicked.
2. **W3.3 Layer 2** (#348) — `include_embeddings=True` flag on
   `build_feature_vector`. Foundation for the hook-classifier ML
   integration.
3. **Comprehensive research arc** → 4-layer monetization fix
   (#352, #353, #354 + `GENLAB_DOMAIN` env var set on prod).
   Dispatched 5 parallel research agents; 2 of their findings were
   verified-wrong (IG/YT "silent failures" were dashboard bugs,
   not pipeline bugs); the real bugs were structurally different
   and bigger.

## TL;DR

| Bucket | Open 06-18 night | Closed 06-19 | Still open |
|---|---|---|---|
| **W3.3 layers** | Layers 2-4 multi-day | Layer 2 done (#348) | Layers 3-4 (multi-day ML; offline AUC + online shadow) |
| **U-24 starlette** | Multi-day, FastAPI compat audit needed | ✅ DONE — bumped to 1.3.1, 4348 passed 0 failed | (closed) |
| **Engineering-actionable** | 0 | 0 (and we closed 4 from research) | 3 newly-surfaced from research (see §3) |
| **Operator-blocked** | 6 (5 affiliate creds + AUTO #2 Day-8) | 0 closed | 6 (unchanged) |
| **Today's surfaced bugs** | n/a | 4 monetization layers + permissions-drift recurrence | 0 actionable; 3 deferred for own arc |

**Net actionable engineering remaining: 0 explicit blockers** — but
the research-surfaced "next wave" has 3 high-leverage items that
should be the next focus (§3 below).

---

## 1. Operator-blocked (unchanged from 06-18)

| Item | What's needed |
|---|---|
| PA-API credentials | Amazon 10 sales / 30d. **Now genuinely-possible** post-monetization fix arc — see §4 |
| Impact API credentials | Operator registration (playbook PR #341, tag values corrected in PR #353) |
| ShareASale credentials | Operator registration |
| CJ Affiliate credentials | Operator registration |
| Twitter API credentials | Content-policy decision per niche |
| AUTO #2 Day-8 enablement | Operator review ≥30/niche × ≥90% agreement (~7 days) |

---

## 2. Engineering-actionable closed today

### Wave 1 — U-24 starlette closure (4 PRs)

- **#349** convert storage-test `skipif(boolean)` → `skipif("string")`.
  Boolean form evaluates at module-collection time (decided ONCE,
  too early). String form re-evaluates at test-execution time in the
  module namespace. **The 16-character fix** that collapsed the
  multi-day blocker.
- **#350** explicit `starlette>=1.0,<2` pin in pyproject.toml +
  uv-sync to 1.3.1. Full suite: `4348 passed, 72 skipped, 0 failed`.
- **#351** mark `docs/U-24-starlette-1x-investigation.md` CLOSED with
  the 4-PR resolution chain referenced.
- (Plus #342 from earlier — `GENLAB_SUPPRESS_DOTENV` sentinel +
  autouse pop fixture; partial fix that the string-skipif fix
  completed.)

### Wave 2 — W3.3 Layer 2 + ESLint + drift (3 PRs)

- **#347** drift-check `.logs/` exclude path (+ hot-patched 2
  existing root-owned log files on prod, resolved the stale alert)
- **#348** W3.3 Layer 2 — `include_embeddings=True` flag on
  `build_feature_vector` appending `emb_0..emb_N-1` features
  alongside the 8 hand-engineered text features. Default off for
  backwards-compat with existing XGBoost model files. 5 new pins.
- Dependabot triple-merge: **#343** @types/node, **#344**
  eslint-plugin-react-refresh, **#345** @babel/core. All dev-only
  bumps, frontend now 0/0 lint.
- **#346 CLOSED** (NOT merged) — lucide-react 0.575→1.21 major bump
  removed brand-logo icons (Instagram, Youtube, Twitter, Facebook)
  due to trademark concerns. Frontend uses these 5 places. Needs
  design decision (install separate brand-icon package vs inline
  SVGs); not auto-fixable.

### Wave 3 — 4-layer monetization fix (after research)

User: "analyse and research comprehensively and exhaustively before
proceeding". Dispatched 5 parallel research agents. **2 of their 5
findings were verified-wrong** — important correction noted under §5.

- **#352** geo-link health-check accepts HTTP 405. Was rejecting
  amazon.com's `405 Method Not Allowed` response to ranged GET on
  `/dp/*` URLs, forcing US catalog matches to fall back to
  amazon.in. PR #277's geo→US routing was a structural no-op for
  catalog matches since shipped. 12 new pins, `_HEALTHY_CODES`
  contract pinned exhaustively (also adds 416 — Range Not
  Satisfiable, same class).
- **#353** corrected the affiliate-network playbook to match prod
  reality. PR #341's playbook used placeholder example tag values
  that didn't match. The 5th research agent's "env vars are
  inverted" recommendation would have BROKEN commission tracking
  (Amazon's `-20` suffix = US marketplace, `-21` = IN; prod was
  correct, playbook was wrong).
- **#354** IG CTA pivot from "link in 1st comment" to "link in bio".
  The 1st-comment promise was never fulfilled — `payload_builder.py`
  only populates `first_comment_text` for facebook/twitter and
  `instagram.py:publish()` never calls `post_comment`. Every IG
  follower was hitting a dead CTA. New copy points at
  `review.aspirehub.ai/links/<slug>` which IS working post-PR #272.
- **`GENLAB_DOMAIN=https://review.aspirehub.ai` set on prod**
  (no PR; env-var install via atomic temp-file edit + publisher
  restart). The `_tracked_url()` routing code was already correct;
  it falls back to raw Amazon URLs when this env var is empty.
  After this fix, all future YT/FB/Threads CTAs go through
  `/links/go/<slug>?bp=<candidate_id>` for per-post attribution.

---

## 3. Research-surfaced bugs deferred for own arc

The comprehensive research surfaced **3 high-leverage observability/
data-quality bugs** that weren't part of the 4-layer monetization
fix but deserve their own focused PR:

### 3a. `publishing_analytics.views=0` for 99.4% of rows

AUTONOMY-GAP-ANALYSIS §B.1 named this **"the single highest-leverage
bug to fix"** on 2026-06-12. Still open 7+ days later.

The bandit reads `pending_feedback.reward_48h` which is computed
from `publishing_analytics` (the empty side). **Result: the entire
reward signal is noise.** Top arms have α≈9, β≈100 — bandits are
confident in a 7-10% reward rate that's an artifact of the bug.

Until this lands, every W3.3 / W4.4 / AUTO #2 layer is consuming
noise. Layer 1 of W3.3 (PR #336) and Layer 2 (PR #348) are
intentionally backwards-compatible no-ops, but Layers 3-4 cannot
deliver real value without a real reward signal upstream.

### 3b. `PostgresBackend.create()` missing `app.niche_id` GUC

RLS WITH CHECK policy on `publishing_analytics` enforces
`niche_id = current_setting('app.niche_id')`. The `create()` method
(unlike `find/get/update`) does NOT call
`set_config('app.niche_id', ...)` before INSERT. Pool re-use means
inserts intermittently get rejected by the policy + exception is
swallowed in `record_publish` → ~83% of IG SUCCESS rows silently
lost.

Same class of bug affects: `feedback_registration`,
`affiliate_reply`, and ANY other writer that bypasses
`tenant_context.pg_connect()`. The SR-A/C/D arc shipped foundation +
caller migration but `PostgresBackend.create()` is one of the
remaining holdouts.

Side effects masked by this bug:
- `monetisationprogress` IG counts under-report ~83%
- `daily_cap` may let through 2nd publish per day
- Reward attribution + LinUCB miss ~83% of IG events
- AUTO #2 calibration confusion-matrix is sparse vs reality

### 3c. `run_fetch_insights.py:521` in-place status mutation

The script UPDATEs `publishing_analytics.status` from
`SUCCESS → INSIGHTS_24H → INSIGHTS_48H → INSIGHTS_168H` as each
metric window fires, overwriting the SUCCESS marker. There's no
history table — only the latest state survives.

So every dashboard/alert/probe query that filters by
`status='SUCCESS'` ages backward as collector windows fire,
producing "phantom gaps" like "anime hasn't published to YT since
April-May" when reality is that anime publishes to YT daily.

Fixes (pick one):
- Add a sibling column `last_success_at` that's never mutated
- Append-only state transition rows instead of in-place UPDATE
- Change all dashboard/alert queries to use the
  `_PUBLISHED_STATUSES` allowlist from `daily_cap.py`

---

## 4. Why PA-API is now genuinely-possible

Pre-2026-06-19, the affiliate funnel was 4-layers-broken, ensuring
0 conversions:

1. Health-check rejected 405 → all US matches fell back to amazon.in
2. Playbook had wrong example tags (would have caused commission tag
   misconfiguration if anyone had swapped to match the playbook)
3. IG CTA pointed at a comment that never got posted
4. YT/FB CTAs bypassed `/links/go` (no per-post attribution)

Post 4-layer fix arc:
- US clicks route to amazon.com (the store users actually shop at)
- IG followers see "link in bio" CTA that resolves to a real
  product redirect
- YT/FB descriptions include URLs that go through `/links/go/<slug>?bp=<id>`
- All clicks get logged to `affiliate_clicks` with per-post
  attribution → bandit + reward chain can learn

The 10-sale Amazon eligibility threshold is now genuinely-reachable
on the natural cadence (was structurally impossible before today).

---

## 5. Process lesson: verify research agent claims

The 5th research agent's "env vars are inverted" recommendation was
plausible, well-cited, and would have BROKEN commission tracking if
executed. 30 seconds of verification (check Amazon's
marketplace-suffix convention: `-20` = US, `-21` = IN) revealed:

- The 5th agent had read my own playbook (PR #341) as ground truth
- The playbook had placeholder example values, not real tags
- Prod was correct, doc was wrong

The fix was opposite to what the agent recommended — correct the
doc, not the env vars.

Two of the 5 agent findings (IG silent failure, YT publish gap)
also turned out to be misinterpretations: pipelines were actually
healthy, the DASHBOARD was lying. Real bugs surfaced from the
research were elsewhere (PostgresBackend.create RLS, status
mutation, health-check 405).

**Lesson**: parallel research is valuable but every conclusion needs
verification against actual prod/code state before acting. Example
values in docs become "ground truth" for future research =
footgun; always label or omit.

---

## 6. SaaS / multi-tenancy

| ID | Status |
|---|---|
| SR-A | ✅ Done |
| SR-B | ✅ Done |
| SR-C | ✅ Done |
| SR-D | ✅ Done |
| SR-E | ✅ Done |
| SR-F | ✅ Done |

All 6 SR-* items closed. `GENLAB_REQUIRE_TENANT_GUC=1` live since
2026-06-18. Tenant-2 onboarding structurally possible AND
quota-isolated.

§3b above (`PostgresBackend.create()` missing GUC) is the residual
loose end — it's an SR-A/C/D follow-up on the write-path side.

---

## 7. Today's grand total

**40+ PRs merged across 2026-06-18 → 2026-06-19**:

| Wave | PRs | Theme |
|---|---|---|
| 2026-06-18 morning | #310, #312-#325 | Trends + content production fixes |
| 2026-06-18 afternoon | #326-#331 | Wave 4 + W3/W4.4 |
| 2026-06-18 evening | #332-#337 | Close-out — SR-E, ESLint, U-24 investigation, W3.3 foundation, yt-warm, ElevenLabs |
| 2026-06-18 night | #338-#341 | Gaming pipeline + docs |
| 2026-06-19 early | #342 + dependabot #343/#344/#345 + #347 W3.3 L2 #348 + U-24 closure #349/#350/#351 | U-24 closure + W3.3 L2 + drift |
| 2026-06-19 mid | #352, #353, #354 + GENLAB_DOMAIN config | Monetization 4-layer fix |

Plus prod operational changes: ElevenLabs API key live, GENLAB_DOMAIN
set, drift-check exclusion + log chowns.

**0 failed units on prod. 3 open alerts** (ai_creators publish_silence
+ anime zero_blueprints + anime single_source) — all documented as
content-supply throttling, not bugs.

---

## 8. Strategic note (from research)

The 5th research agent's brutal strategic critique:

- Phase 1 ("prove on 5 channels") is **not yet proven**
- Only ai_creators consistently works; even it has negative
  follower growth
- Fix:feat ratio 1.34:1 — system in fix-mode, not build-mode
- We're treating symptoms; the chronic content-supply throttling
  isn't fixed at the source

Three strategic moves proposed:
1. Pick ai_creators as Phase-1 reference channel. Freeze the other 4
   until ai_creators has 30 consecutive publish days + positive
   follower delta + ≥1 verified affiliate conversion.
2. Fix the reward signal first (§3a + §3b) before any more learning
   layer work.
3. Make ONE affiliate dollar move end-to-end (now structurally
   possible post-Wave-3 monetization fix).

This is the operator's call — not an engineering blocker.
