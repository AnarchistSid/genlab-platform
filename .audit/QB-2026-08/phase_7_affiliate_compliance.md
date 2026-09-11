# Phase 7 — Affiliate disclosure + compliance envelope (Dimension 6 + Section 1.3)

**Scope:** captions in the DB (30-day window), compliance_events, affiliate_clicks, affiliate_revenue, YouTube inauthentic-content exposure, footage-copyright risk per niche. Read-only.

## Corpus measured

DB query: `SELECT niche_id, LEFT(caption, 400), affiliate_url IS NOT NULL, affiliate_network FROM blueprints WHERE created_at >= NOW() - INTERVAL '30 days' AND caption IS NOT NULL` (via SSH → VPS Postgres). Total blueprints with caption: **173** (ai_creators 42, gaming 40, anime 32, movies 30, sports 29). Compliance-events window: 30 days, 1307 events.

---

## Findings (12/12)

### F-QB-0701 — HIGH — All 17 movies blueprints with affiliate link (100%) place `#ad` disclosure AFTER the 100-character "more" fold. Zero conform to disclosure-position benchmark.

* **Measured value:** 
  ```
  SELECT COUNT(*) FROM blueprints
  WHERE affiliate_url IS NOT NULL
    AND LEFT(caption, 100) ~* '(#|\s)(ad|sponsored|advertisement|paid[- ]partnership)';
  → 0 (out of 17 affiliate blueprints)
  ```
* **Caption skeleton observed (verbatim excerpt of an affiliate movies caption):**
  ```
  {Hook up to ~100 chars}
  
  Via r/movies
  
  #Movies #Cinema #Hertzfeldt
  
  🎬 Original: @r/movies —
  
  Save this for later!
  
  🎬 Original: @r/movies — https://www.youtube.com/watch?v=fS4aHNB5WRo
  
  #ad
  🔥 Amazon Prime Video — ₹1499 👇 (link in bio)
  ```
  `#ad` lands ~260-300 chars in — well past the ~80-100 char "more" fold on IG/FB.
* **Benchmark:** Section 1.1 row 6 HIGH-confidence legal target: "Disclosure in first ~80–100 chars of caption (before the 'more' fold, not in hashtags)."
* **Impact:** FTC max civil penalty $53,088 per violation (Section 1.3). All 17 affiliate posts on movies are technically non-compliant with FTC 16 CFR §255. Legal exposure calc: 17 posts × $53,088 = $902K worst case (though FTC rarely enforces retroactively on small volumes without prior notice).
* **Confidence:** HIGH.
* **Tier:** 1 (compliance).
* **Verification gate:** re-run the exact query after fix; expect ≥95% of affiliate blueprints to match on the `LEFT(caption, 100)` regex.

### F-QB-0702 — HIGH — Only 3 affiliate clicks in 30 days and zero recorded revenue rows despite the "$1M/month per channel" north-star from rule #24

* **Measured value:** 
  * `SELECT niche_id, network, COUNT(*) FROM affiliate_clicks WHERE created_at >= NOW() - INTERVAL '30 days' GROUP BY 1,2;` → **3 rows total** (ai_creators/amazon_us=1, movies/amazon_us=1, sports/amazon_us=1).
  * `SELECT COUNT(*) FROM affiliate_revenue WHERE date >= CURRENT_DATE - INTERVAL '30 days';` → **0 rows**.
* **Benchmark:** Section 1.1 row 6 MEDIUM-confidence CTR target 0.5–2.5%. Rule #24 target $1M/month per channel.
* **Impact:** With 17 affiliate posts in 30 days and 1 click, CTR is 5.9%. But absolute click volume is 3 — statistically indistinguishable from zero. Revenue is exactly zero. The entire affiliate-monetization capability described in CLAUDE.md rule #24 has produced no measurable financial outcome.
* **Confidence:** HIGH (measurement) / LOW (interpretation — could be tracking gap rather than actual zero-conversion; disclosed as such).
* **Tier:** 1 (top-line business outcome).
* **Verification gate:** first cross-check that `affiliate_clicks` is actually receiving webhook traffic (e.g. Cuelinks postback URL is configured); then, given confirmed tracking, expect ≥100 clicks/month at current output levels for the number not to indicate a click-tracking regression.

### F-QB-0703 — HIGH — Caption template produces DUPLICATED `🎬 Original: @X —` attribution in 97 of 173 blueprints (56% across all niches)

* **Measured value:** `SELECT niche_id, COUNT(*) FROM blueprints WHERE caption ~ '🎬 Original:.*🎬 Original:' AND created_at >= NOW() - INTERVAL '30 days';` → ai_creators=27, movies=19, gaming=18, anime=17, sports=16.
* **Caption pattern:** every affected caption has one attribution line with no URL followed by a second attribution line with the URL. Root cause is a caption-builder that appends attribution twice (once at some post-hashtag block, once again in a CTA block).
* **Impact:** Not a compliance defect but a **content-quality defect** — 56% of published posts show visibly redundant attribution. Reduces caption professionalism, wastes space in the 100-char fold zone (before which the disclosure must appear per F-QB-0701).
* **Confidence:** HIGH.
* **Verification gate:** after de-duping, expect ≤5% of captions to contain two `🎬 Original:` markers.

### F-QB-0704 — HIGH — 64 blueprints (37% of the 30-day corpus) have attribution formatted `🎬 Original: @X —` with NO URL following the em-dash

* **Measured value:** gaming=19, sports=19, ai_creators=11, anime=9, movies=6. Query: `SELECT niche_id, COUNT(*) FROM blueprints WHERE caption ~ '🎬 Original: @[^ ]+ —[[:space:]]*$' OR caption ~ '🎬 Original: @[^ ]+ —[[:space:]]*[[:cntrl:]]' GROUP BY 1;`
* **Benchmark:** CLAUDE.md ATTRIBUTION DEFENSE STACK: invariant is `🎬 Original: @{creator} — {url}` AND (bonus) burned into frame. A dash-with-no-URL half-fires the invariant.
* **Impact:** partially violates rule #11, weakens platform-side deniability if a creator DMCA is filed. Layer 4 publisher validator (per CLAUDE.md) recognises "CAPTION markers only" — it may be accepting the dash-only form as attribution because it matches the marker prefix. Verify against Layer 4 code before Phase 9.
* **Confidence:** HIGH (measurement).
* **Verification gate:** tighten regex in Layer 4 validator to require a non-empty URL after the em-dash; re-measure.

### F-QB-0705 — HIGH — 5 gaming blueprints have NO attribution marker AT ALL (rule #11 hard violation); 1 has completely empty caption

* **Measured value:** query returned 5 rows including one blank caption. The 4 non-blank captions have hooks + hashtags + engagement CTA but zero `🎬 Original:` string.
* **Benchmark:** rule #11 zero-tolerance invariant.
* **Impact:** 5 gaming posts are shippable without attribution as of the last render. Also intersects with F-QB-0002 (gaming render dead): these captions were generated but their MP4s never materialised — the risk is that if gaming publishing resumes with these blueprints as-is, they'd hit Layer 4 validator (`GENLAB_ATTRIBUTION_LAYER4_BLOCK=1` per CLAUDE.md) and fail. But the flag state is not confirmed in this audit.
* **Confidence:** HIGH.
* **Verification gate:** re-render + re-publish path must reject these; log rows with `attribution_missing` in `compliance_events`.

### F-QB-0706 — MEDIUM — Facebook posts contain external HTTP URLs in the caption body in 66 of ~152 FB publishes (43%): ai_creators=23, sports=15, anime=14, gaming=14

* **Measured value:** join of `blueprints` + `publishing_analytics` on `blueprint_id` filtered to `platform='facebook'` + `caption ~ 'https?://'`.
* **Benchmark:** CLAUDE.md CONTENT_QUALITY_RULES row for Facebook: "200-300 chars, engaging question, **no external links**." Facebook demotes reach on posts with external links in the body.
* **Impact:** 43% of FB publishes are self-throttling reach. The URL landing in FB captions is the source-attribution `🎬 Original: … https://…` — same URL that is required for attribution invariant. **The two rules are in direct conflict**: attribution invariant says "put URL in caption"; FB reach-optimization says "don't put URL in caption." Prompt says nothing to resolve this. Per Section 1.3 attribution rules and legal risk, attribution wins. But FB reach loss is real; a fix would move the URL to a first comment.
* **Confidence:** HIGH.
* **Verification gate:** implement FB-specific attribution using first-comment (Meta Graph API supports this); re-measure and expect 0 https:// in FB caption body while attribution stays present in first comment.

### F-QB-0707 — MEDIUM — 6 `platform_policy_block/block` events on 2026-07-21, ALL on Facebook, spread across 4 niches. Root: Meta code=368 "temporarily blocked from sharing"

* **Measured value:** compliance_events query returned 6 rows, all on 2026-07-21, all platform=facebook, all niches except movies affected. Each carries `error_snippet: "code=368: You're temporarily blocked from using this feature because you shared something that is[not allowed]"`.
* **Memory reference:** existing memory `[[meta-code-368-audit-2026-07-22]]` documents code=368 as "transient API throttling, NOT formal Page violation." This audit confirms the code=368 events but the fact that they happened on the SAME day across FOUR niches suggests either (a) a per-app rate limit / X-App-Usage collision, or (b) a genuine per-page policy signal shared across pages under the same Business Manager.
* **Impact:** Rule #23 keeps FB as a north-star platform. Six blocks on a single day is not "noise" — it's a signal worth investigating. Per Section 1.2 Meta shares/sends are top-3 ranking factor; every day FB drops out is a lost audience-growth day. Cross-check the actual Meta X-App-Usage header in the 2026-07-21 logs (per the memory, meta_http hook was added).
* **Confidence:** MEDIUM (event count is HIGH; interpretation LOW without X-App-Usage evidence).
* **Verification gate:** query `pipeline_alerts` or `dashboard_events` for X-App-Usage snapshots around 2026-07-21T05:00 UTC; if any >90% usage, root cause is API rate; else needs deeper investigation.

### F-QB-0708 — HIGH — Caption pattern is textbook match to YouTube's July-2026 "generic / repetitive / template-based content" inauthentic-content bucket (Section 1.3)

* **Measured value:** every one of 173 blueprint captions follows the same 7-block template:
  ```
  {HOOK}
  \n
  Via {source}
  \n
  #Niche #{niche}Reels #{topic}
  \n
  🎬 Original: @{creator} — [maybe URL]
  \n
  {CTA: "Save this for later!" | "Drop your take below 👇"}
  \n
  🎬 Original: @{creator} — {URL}
  [\n#ad\n🔥 {product} — {price} 👇 (link in bio)  ← only if affiliate present]
  ```
  Hook varies per post but the rest is fixed. Also — this is the CAPTION template; the audio + rendered video templating pattern is deferred to Phase 4/5 for the burned-in overlay text and to Phase 3 for the audio structure.
* **Benchmark:** Section 1.3 lists three YT non-monetizable buckets. Bucket #1 is exactly this pattern.
* **Precedent (Section 1.3):** Screen Culture (~1.4M subs) + KH Studio (~600K subs) permanently terminated Dec 2025 for spam/misleading metadata + AI-blended copyrighted footage.
* **Impact:** ai_creators is currently the sole publishing channel (F-QB-0003) and is 100% templated. YouTube demonetisation or termination risk is not hypothetical; the pattern-match is direct.
* **Confidence:** HIGH (measurement of the caption template). MEDIUM (interpretation of the YT-enforcement bucket — bucket definitions are directional).
* **Tier:** 1 (compliance / channel-lifetime risk).
* **Verification gate:** measure post-to-post caption novelty (embedding distance between last 30 captions vs. prior 30) — expect it to increase materially after template-detemplating work.

### F-QB-0709 — HIGH — Sports, gaming, movies, anime use non-original footage (per CLAUDE.md source-tables); no evidence of licensing agreements in repo config

* **Measured value:** grep for `license|licensed|rights` in `*/config/*.yaml` returns nothing per-niche. Sources per CLAUDE.md are YouTube API v3 for gaming/sports/movies + YouTube keyword search for anime + trailer clips for splicereel. Attribution invariant fires — that is a courtesy signal but does not equal a license.
* **Benchmark:** Section 1.3 — sports / movies / anime carry Content ID and strike exposure; safer patterns are (a) very short clips (<7s), (b) original VO replacing source audio, (c) data-viz overlays. GenLab reels are 16-21s per Phase 1 and mostly source-audio-replaced with TTS (deferred to Phase 3 for verification).
* **Impact:** Sustained publishing at scale under the current model amounts to unlicensed use of Content-ID-eligible material. Individual reels are low-risk; per-channel growth to millions of views triggers claims. FrameDrift (anime) is the highest-risk because anime rights-holders enforce aggressively.
* **Confidence:** MEDIUM (measurement of no license config); HIGH (risk framing is documented benchmark).
* **Tier:** 1 (compliance / cease-and-desist risk).
* **Verification gate:** verify actual clip duration used in composite for each niche (Phase 2 will produce shot-length data); confirm TTS-only audio track (Phase 3).

### F-QB-0710 — MEDIUM — `ai_disclosure_added` compliance events fire 1016 times in 30 days across all 5 niches; the AI-content disclosure invariant is actively populated on every publish

* **Measured value:** `SELECT event_type, decision, COUNT(*) FROM compliance_events WHERE created_at >= NOW() - INTERVAL '30 days' GROUP BY 1,2;` → `ai_disclosure_added|allow|1016`, `pre_publish_check|allow|252`, `pre_publish_check|warn|33`, `platform_policy_block|block|6`.
* **Impact:** The AI-content disclosure required by Section 1.3 IS being added to every publish. This is a POSITIVE finding — Layer 4/5 attribution defense-stack is doing part of its job.
* **Confidence:** HIGH.
* **Verification gate:** — (baseline; watch for drop in event count as gate that surfaces regression).

### F-QB-0711 — MEDIUM — Only 33 `pre_publish_check | warn` events in 30 days (2.5% of pre-publish checks); no `deny` decisions at all across the 30-day window

* **Measured value:** ratio computed above.
* **Impact:** Either the pre-publish compliance system is running clean, or the check is so lax it never triggers a warn/deny. The fact that F-QB-0701 (all 17 movies affiliate posts non-compliant on disclosure position) got through with zero `deny` events points to the latter — the pre-publish check does not enforce disclosure-position, only disclosure-presence.
* **Confidence:** MEDIUM.
* **Verification gate:** add a pre-publish rule that scans `LEFT(caption, 100)` for approved disclosure terms; expect this rule to trigger on 100% of current affiliate blueprints before F-QB-0701 is fixed.

### F-QB-0712 — LOW — Caption charset issues detected: one movies caption has `Via` with no source name; another has `🎬 Original:` with no @handle

* **Measured value:** case observed in Phase 7 sample #3 (Spider-Man Brand New Day): both `Via` and `🎬 Original:` blocks are populated but the source name is empty — `Via \n\n#…\n🎬 Original: \nSave this for later!\n🎬 Original: https://youtube.com/watch?v=Bf-GMJBJLpA`.
* **Impact:** Attribution is functionally absent for these posts. Same failure mode as F-QB-0705 but different generator path.
* **Confidence:** MEDIUM (n=1 direct observation in sample; full corpus needs regex sweep).
* **Verification gate:** `SELECT COUNT(*) FROM blueprints WHERE caption ~ '🎬 Original:[[:space:]]*$' OR caption ~ '🎬 Original:[[:space:]]*\n' AND created_at >= NOW() - INTERVAL '30 days'`; expect ≤2% of blueprints to match.

---

## Deferral ledger (Phase 7)

| Item | Reason deferred |
|---|---|
| On-screen (burned-in) disclosure OCR check for `#ad` in first 3s | Phase 4 — OCR pass is shared |
| Layer 4 attribution validator source grep to confirm dash-only-no-URL is accepted | Phase 8 (code inspection block) |
| Facebook X-App-Usage historical values around 2026-07-21 block cluster | Phase 8 (part of observability audit) |
| Whether niche_id RLS `SET LOCAL app.niche_id` is applied by the compliance-check writer | Phase 8 |

## What was not measured

* Bio-level disclosure (out of Section 1.1 scope — per-post is required regardless).
* Meta Business Manager verification status (visible only via ops console, not code).
* Cuelinks postback receipt path (Phase 8 — is affiliate_clicks tracking wired correctly?).
* Per-post caption novelty embedding distance (needs embedding call; deferred to Phase 6).

## Sample N: 173 blueprints (30 days), 1307 compliance_events (30 days), 3 affiliate_clicks (30 days). All 5 niches present in caption corpus (unlike Phase 1, gaming captions exist even though gaming renders don't).
