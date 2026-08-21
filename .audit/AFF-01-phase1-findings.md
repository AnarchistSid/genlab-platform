# AFF-01 Phase 1 — affiliate inventory + link health

**Run:** 2026-08-21 06:46–07:2x UTC, after all four non-BB niches fired clean
(movies 03:30, gaming 04:00, sports 05:00, anime 06:00 UTC — all `success`).
**Scope:** read-only. No prod writes. Findings only.

---

## Headline — the attribution chain is fully built and severed by one type mismatch

`affiliate_clicks.blueprint_id` is populated on **0 of 182 rows**. Not because
the plumbing is missing — every piece exists and is correct in isolation:

```
GENLAB_DOMAIN = https://review.aspirehub.ai       ✓ set
GENLAB_REQUIRE_TRACKING_DOMAIN = 1                ✓ strict mode on
cta_engine._tracked_url        → emits {domain}/links/go/{slug}?bp={attribution_id}
links.py:1068 link_go          → reads request.args["bp"], passes to tracker
link_tracker.track_click       → writes record["blueprint_id"]
PROMOTED_COLUMNS["affiliate_clicks"] contains "blueprint_id"   ✓ (rule #28 satisfied)
affiliate_clicks.blueprint_id  → uuid column, FK to blueprints
```

The break is at the last hop, and it is a **shape mismatch between two
identifiers that were never reconciled**:

* `cta_engine.py:344-349` — at CTA-injection time the blueprint row does not
  exist yet, so `attribution_id` falls back to `candidate_id`. The code comment
  states this explicitly: *"Attribute to candidate_id since the blueprint row
  doesn't exist yet at inject time (blueprint_id is empty here), which is why
  utm_content was previously always blank."*
* `candidate_id` in prod is a **64-char SHA-256 hex**:
  `f07ca82b2f17ae3143203db847fc65c524d24e07403ebdd5010bed93eae91e2c`
* `link_tracker._sanitize_blueprint_id` (line 43) accepts **UUID format only**.
  Anything else → `None`, and `track_click` then *omits the key entirely*
  (`link_tracker.py:136-140`).

So every tracked click arrives carrying a perfectly good identifier, and the
validator drops it for being the wrong shape. **182/182 NULL.**

### Two aggravating properties

1. **The rejection logs at DEBUG** (`link_tracker.py:63`), with a comment
   justifying it as anti-log-spam: *"not warn — an attacker can't spam WARN
   logs by hitting /links/go/<slug>?bp=garbage."* Sound reasoning for hostile
   input, but it means the **legitimate** rejection of our own IDs has been
   invisible for the life of the feature. Rule #19's exact shape.
2. **This is the same `candidate_id` stage-ordering defect as NARR-01's
   filename collision.** There, `candidate_id` was assigned at
   `push_to_backlog.py:2310` (stage 21) but consumed as a filename at stage 17,
   producing one `unknown_audio.mp3` per niche forever. Same field, same
   too-late assignment, different symptom. Fixing it once in a shared place
   would have closed both.

### Second, independent attribution gap

All 182 clicks originate at the **link-in-bio page**
(`review.aspirehub.ai/links/<channel>`), evidenced by the referrer column
(170 empty, 12 explicitly from `/links/{splicereel,blackboxbrief,framedrift}`).
That page is **channel-level, not reel-level** — it cannot pass a `bp` param
because it has no blueprint context. `platform_source` is likewise empty on
all 182.

Even with the UUID mismatch fixed, clicks arriving via link-in-bio remain
unattributable by construction. Caption-borne clicks (YT/FB, which do get real
URLs) would become attributable.

**This is what Phase 2 has to solve, and it is not a formality.** The
attribution-before-arming gate was the right call.

---

## Inventory

50 products / 5 niches / 80 affiliate URLs. (My earlier "82 URLs" was
approximate; the exact figure is 80 — 45 `amazon_us`, 34 `amazon`, 1 `direct`.)

| niche | cap ₹ | products | enabled | **under cap = selectable** |
|---|---:|---:|---:|---:|
| gaming | 6,000 | 10 | 10 | **4** |
| sports | 3,000 | 10 | 9 | **4** |
| movies | 2,500 | 10 | 9 | **3** |
| anime | 2,500 | 10 | 9 | **6** |
| ai_creators | 3,000 | 10 | 8 | **3** |
| | | **50** | **45** | **20** |

### F2 — 60% of the catalog is unreachable, and it's the valuable 60%

`affiliate_matcher._price_filter` (line 346) enforces
`0 < price_inr <= max_price_inr`. Twenty of fifty products survive it. The
excluded set is systematically the high-commission end — PS5 ₹49,990, Steam
Deck ₹39,999, 4K Projector ₹45,000, Smart TV ₹35,000, RTX 4090 ₹159,900.
Amazon pays a percentage, so the cap excludes exactly the items worth the most
per conversion.

This is a real tradeoff (cheap items convert at higher rates), not
self-evidently a bug — but it is currently an **unmeasured** tradeoff, set as a
constant, never A/B'd. Phase 4 lever. The filter's own log line is DEBUG.

### F3 — one "enabled" product is structurally incapable of earning

`ai_creators / Claude Pro Subscription`: `enabled: true`, network `direct`,
`url: https://claude.ai/`, `commission_pct: 0.0`. It is a plain link to
Claude with no affiliate tag and no programme behind it. It occupies one of
ai_creators' three selectable slots.

### F4 — one dead link

`sports / Fitness Tracker / amazon_us` → **HTTP 404**. Dead ASIN.

### F5 — the seasonal layer does not exist

Only `affiliate_seasonal.example.yaml` is present on prod; there is no
`affiliate_seasonal.yaml`. Any code path reading it is inert.

### F6 — tracking pixels unset

`settings.tracking.facebook_pixel_id` and `ga4_measurement_id` are both `""`.
No third-party click/conversion telemetry independent of our own table.

---

## Link health — probed from a residential IP (Mac), 2026-08-21

| result | n |
|---|---:|
| HTTP 200 | **79** |
| HTTP 404 | 1 |
| soft-404 body ("Sorry! Something went wrong", captcha, etc.) | 0 |
| network errors | 0 |
| `tag=` survived to final URL after redirects | **79 / 80** |

Probed from the Mac deliberately, **not** the VPS. Affiliate links are clicked
by end users from residential IPs; a datacenter-IP block would say nothing
about audience-facing health and would have manufactured a false outage
(`[[class-of-bug-datacenter-ip-bot-detection]]`). The single `tag_kept=false`
is Claude Pro (F3), which never had a tag.

**The links are not the problem.** 99% resolve cleanly with the tag intact.

---

## Verified NON-findings (checked, then discarded)

Recording these because each looked like a defect on first inspection and
would have been a false report:

| Looked like | Actually |
|---|---|
| Service has **no `EnvironmentFile`** and no `AMAZON_*_AFFILIATE_TAG` in its systemd environment → placeholders would ship unexpanded | **Expansion works.** 517 blueprints carry a real `tag=aspirehub06-20`, three of them from today's fire. Only 2 rows ever shipped `${...}`, both 2026-04-06. The process loads `.env` by another route. |
| `movies` has **no `affiliate_enabled` key** while the other four do | Fail-open: `niche_cfg.get("affiliate_enabled", True) is False`. Movies was never disabled — which is why yesterday's flip correctly touched only four niches. |
| `enabled` present on only **6 of 50** products | Fail-open: `p.get("enabled", True)`. 45 selectable. The 5 explicit `false`s are deliberately-disabled subscriptions (FanCode, Netflix, Crunchyroll, ChatGPT Plus, Midjourney). |
| `blueprint_id` NULL → **rule #28** (column missing from `PROMOTED_COLUMNS`) | It **is** in `PROMOTED_COLUMNS["affiliate_clicks"]`. Different root cause entirely (the UUID mismatch above). |
| Captions carry **no affiliate URL**, only "(link in bio)" | True for **Instagram only**, which is correct — IG forbids clickable caption links. YouTube and Facebook branches emit real `/links/go/` URLs (`cta_engine.py:461, 487`). My first sample happened to be the IG caption. |
| Yesterday's evergreen change points at Ring Light, which has no `enabled` key | Fine — missing defaults True, and Ring Light is under the ₹3,000 cap. |

---

## Click history

182 clicks, 2026-03-22 → 2026-08-19, all five niches represented
(movies 58, gaming 38, ai_creators 33, anime 29, sports 24).

```
2026-03   61
2026-04   45
2026-05    4
2026-06   54
2026-07   12
2026-08    6      ← through 08-19
```

47 of the 182 carry an **unexpanded** `${...}` URL — the historical PR #272
bug. Those earned zero commission regardless of what the user did.

The decline tracks the `cta_injection_enabled: false` disable (`4dd2ebb6`,
2026-08-06) and the audience baseline, not link health.

---

## Today's fire — yesterday's flip is working

| niche | blueprints | with affiliate link |
|---|---:|---:|
| anime | 3 | 3 |
| gaming | 5 | 3 |
| sports | 4 | 3 |
| movies | 2 | 2 |
| ai_creators | 1 | **0** ← correct, BB deliberately deferred to ≥2026-08-29 |

11 of 15. First affiliate-bearing blueprints since 2026-08-05.

---

## What Phase 2 inherits

1. **Reconcile the two identifiers.** Either make `attribution_id` a real
   blueprint UUID at inject time, or widen the tracker to accept the
   `candidate_id` shape and resolve it. The second is cheaper but keeps two ID
   spaces alive; the first fixes the NARR-01 sibling too. Decide deliberately.
2. **Elevate the rejection log off DEBUG** for the *self-inflicted* case,
   keeping DEBUG for genuinely hostile input. The anti-spam reasoning is sound
   and should survive the fix.
3. **Decide what link-in-bio clicks can ever be worth.** They are the entire
   current click volume and are unattributable by construction. Options: a
   per-reel link page, a short-lived bio link rotated per post, or accepting
   them as unattributed and measuring only caption-borne clicks.
4. **Only then Phase 3.** 108 product arms against an attribution rate of 0%
   would reproduce the 255 transform-arm situation exactly.

Nothing in this document was acted on. No prod writes were made.
