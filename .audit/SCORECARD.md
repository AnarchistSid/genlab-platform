# GenLab Audit — Final Scorecard

**Date:** 2026-07-24. **Prod HEAD:** `edf91209`. **Findings:** 61 (5C / 19H / 23M / 11I / 3L). **Read-only.**

*Phase 7 addendum below — scores revised, F-0047 rebuilt, Threads corrected.*

---

## Section 0 gate results

- **§0.1 Journald** — persistent but `SystemMaxUse=500M` + 1 boot after 9d uptime → 30-day windows are **UNVERIFIED**. **F-0056 HIGH.**
- **§0.2 Neural-LinUCB** — no code. Zero neural/torch/xgboost refs in `learning/`. **F-0057 MEDIUM.** Phase 3B's "500 days to Neural" measured distance to nothing.
- **§0.3 Closure** — per-blueprint 66.3% (737/1,112). Session 1's 37.2% was per-post-per-platform. Both real; per-blueprint drives bandit updates. **F-0058.**
- **§0.4 Jaccard** — genuinely 0.0000 max, word-level tokenizer. Hooks are short and specific. Window = 4–6 days/channel, not 60.
- **§0.5 Passthrough** — aggregate 13% but **51.2% concentrated in gaming** (21/41). ai_creators = 0/43. F-0054 re-filed **HIGH**.

---

## Section 2 — six-dimension scores

### INTELLIGENCE — 4/10

Loop closes end-to-end (bp `a86a541f` traced), 66.3% per-blueprint closure, arms move 29–52/niche/7d. But five ML flags are dormant (F-0041), Neural-LinUCB has no implementation (F-0057), 34% of blueprints never contribute reward signal. **Citations:** F-0052, F-0057, F-0041. **+2 action:** implement Threads reward-fetch OR drop Threads officially. **Confidence:** high.

### AUTOMATION — 4/10

86% publish on three platforms is real. Against it: 4 units never fired (F-0033), guards exit 0 on missing keys (F-0034), 12 Anthropic 402s in 30d at $13/mo (F-0053). Operator vanishes for 30 days: gaming stops first at billing failure; IG cap of 17–37% missing IDs starves learning; 107 SILENT_IN_PROD except sites mask degradation. Automation ships posts; it does not maintain itself. **Citations:** Phase 3A 90/105, F-0053, F-0034. **+2 action:** auto-top-up Anthropic + elevate 107 `logger.debug` swallows to `warning`. **Confidence:** high.

### CREATIVITY — 4/10

Structural diversity is genuine — 33 distinct hooks / 100 posts, opening-token uniqueness 33/33, zero banned formulations. Movies and sports read as observant human writing. But **gaming has 51.2% source-title passthrough** (F-0054) — writer stage failing on the highest-volume channel. Caption length hits target. Sample = 4–6 days per channel. **Citations:** `phase6/06_creativity.md`, F-0054, §0.4. **+2 action:** reject blueprints where `hook ⊂ source_title` at length ≥ 8; force retry. **Confidence:** medium (narrow window).

### CAPABILITY — 5/10

Video-first: **5/5 sampled reels PROVEN** (>30 scene changes, 1080×1920, h264+aac). YT/FB/IG/Threads on all 5 channels PROVEN (see F-0061 — Threads was mis-excluded in Session 1). IG lossy (F-0050). X, TikTok: ABSENT (per rule #23 out of scope). **PROVEN:CLAIMED = 20 of 25 platform×channel cells = 80%.** Auto-approver: shipped, ai_creators-only. Neural-LinUCB CLAIMED-ABSENT. **Citations:** Phase 3A video 5/5, F-0057, F-0050. **+2 action:** decide Threads — ship or exit officially. **Confidence:** high.

### COMPLEXITY — 3/10

190K src / 198K tests / 22GB repo produces ~5 reels/day. `push_to_backlog.py:1583 execute` is CC=**223**, MI=**0.0**, 99 commits, live publish path. Four files at MI=0.0. New channel = ~2,900 lines with 22.4% ClutchWire cloning. Essential complexity is real; accidental complexity (god-modules, clone hotspots in visual_render/writing/run_pipeline) is what forces this below 5. `push_to_backlog.py` is where a bug becomes an outage. **Citations:** F-0010 (CC=223, MI=0.0), 22.4% cloning, 2,900-line channel cost. **+2 action:** extract the CC=223 `execute()` into named-stage subclasses. **Confidence:** high.

### COMPETENCY — 3/10

Backups daily + restore validated weekly (F-0035) is the strong pillar. Against it: 9,451 tests cannot complete (F-0014); 107 silent-in-prod except sites (F-0028/F-0039); 66 CVEs / 15 packages incl. `yt-dlp` on publish path; `rolsuper=t rolbypassrls=t` collapsing 20 RLS policies to advisory (F-0024/F-0045/F-0048); 5432 exposed 60+ days with a 10-char password; F-0056 journald retention. $0.063/run cost hygiene is a positive. **Citations:** F-0014, F-0024/F-0045/F-0048, F-0056. **+2 action:** F-0049 (least-privilege role) — one SQL closes three CRITICALs. **Confidence:** high.

---

## Section 3 — synthesis

### A. THE ONE-PARAGRAPH TRUTH

GenLab is a working automation over a broken foundation. It publishes ~86% of the daily mandate on three of six claimed platforms, its video-first pipeline verifies against real assets, and its bandit updates on two of every three blueprints. Beneath that: the production database is reachable from the internet fronted by a 10-character password on a role that bypasses every one of its own row-level security policies; the test suite has 9,451 tests and cannot complete; one file on the live publish path carries cyclomatic complexity 223 and maintainability index 0.0; the "Neural-LinUCB" documented as the ML ceiling has no implementation; and the observability layer forgets its own history in hours because the journal cap was set to 500 MB. A due-diligence reviewer would sign a small check against the shipping half and refuse to acquire without insisting on F-0049 and F-0024 executed before signature. The intelligence is real. The engineering beneath it is not what would survive a customer post-mortem.

### B. TOP 10 by (impact × likelihood) / effort

| # | Finding | Effort | Why it ranks |
|---|---|---|---|
| 1 | **F-0049** least-privilege role | XS | one SQL closes F-0024 + F-0045 + F-0048 |
| 2 | **F-0024** 5432 firewall | XS | ends 60-day exposure; already expiry-accepted |
| 3 | **F-0053** auto-top-up Anthropic | XS | ends weekly pipeline failure at $13/mo |
| 4 | **F-0056** journald retention | S | restores the evidence window this audit depended on |
| 5 | **F-0054** gaming passthrough | M | 51% of gaming blueprints ship as source-title reels |
| 6 | **F-0034** silent-success guard | S | rule #26 shape; catches next credential rotation |
| 7 | **F-0028/F-0039** 107 silent-in-prod | M | ~2/3 on publish path; masks incidents |
| 8 | **F-0014** test suite cannot complete | L | 9,451 tests providing no signal is worse than fewer that run |
| 9 | **F-0050** IG missing post_ids | M | 17–37% cannot close reward |
| 10 | **F-0032** self-hosted runner | M | public repo × prod host, one-typo-away |

### C. THE KILL LIST — candidates requiring verification

Static reachability was never determined; this list is **candidates**, not confirmed-dead. Full CSV: `.audit/kill_list.csv`.

- 4 never-fired systemd units (F-0033 + 3 others) — verify then unmask
- 1,342 touched-once files — per-file `git log` + import-check gate before delete
- `_hooks_legacy.py` — name-suggests candidate; verify no runtime imports
- 5 dormant ML flags (F-0041): `BEDROCK_FINETUNE`, `LINUCB_STOCHASTIC` (unsuffixed), `OPTIMAL_TIME_BANDIT`, `THOMPSON_PROPENSITY`, `TOP_CREATOR_PRIORS`, `TREND_ANTICIPATION` — ship or delete
- 17 GB `.tmp` scratch — safe per `.claude/rules/cleanup_safety.md`

### D. THE FACADE LIST — believed working, provably not

1. **RLS multi-tenancy** — 20 policies bypassed at query time by `rolsuper=t rolbypassrls=t`. The multi-tenant SaaS story rests on it and is decorative (F-0048).
2. **Neural-LinUCB roadmap** — doc-claimed, zero implementation (F-0057).
3. **"30-day cascade never fires" claims** — F-0056 vacuumed the window; cascades may be firing weekly.
4. **Auto-approver rollout** — ai_creators only; other channels still gate on operator.
5. **"4-platform publishing"** (the actual north star per rule #23) — real for YT/FB/IG/Threads (Threads is the highest-performing platform at 65–80% success per F-0061). IG lossy. X/TikTok ABSENT (correctly out of scope).
6. **9,451 tests as regression signal** — suite cannot complete; the signal is zero.

### E. THE THREE QUESTIONS

1. **What is being masked by the 107 silent-in-prod except sites?** Elevate `logger.debug` → `warning` on the 15 sampled sites, wait one week, re-audit publishing_analytics for new error clusters.
2. **What actually failed on 2026-07-21 (F-0047)?** Root cause permanently unrecoverable via journal (F-0056). Only remaining path: replay the 4 blueprint IDs with `--dry-run --verbose` and diff against a known-good run.
3. **Answered by F-0061:** Threads is not just writable — it is the highest-performing platform at 65–80% publish success rate across all 5 niches, with 55–61 successful post_ids per niche in 120 days. Restated as the actual open question: **is any of the 30-day error-count evidence real** given F-0056's journald cap vacuumed the window?

---

## What this audit got wrong

- Session 1 summary written before shells exited (fixed).
- Phase 0 stale-dir claim contradicted by its own CSV (retracted).
- `pip-audit` scanned its own venv and counted the audit tool's deps as project CVEs.
- Phase 2.5 asymmetric env grep manufactured 60 phantom drift vars; fixed to 130/130.
- AST env-read detector v1 counted log strings as reads; v2 name-only regex.
- Phase 3B reel-trace was cherry-picked. Aggregate = 66.3% per-blueprint / 37.2% per-post.
- Session 1's 10-hook read reversed direction. Aggregate: gaming carries the 51% burden.
- Phase 2.5 leaked a live Slack webhook into `.audit/`. **F-0030 against the audit process.**

---

## Which scores did I round UP?

None. **INTELLIGENCE / AUTOMATION / CREATIVITY** all held at 4 against the pull to say 5 (cherry-picked trace, partial-mandate arithmetic, gaming's 51% writer failure). **COMPETENCY** held at 3 — external standards would say 2 given `rolsuper` + 66 CVEs + 9,451 failing tests; the 3 rests on backups being genuinely good.

---

## Largest findings — separated by audience

*Phase 7.7 restructures Phase 7.6's conflated "largest single finding" claim.*

### Largest system findings (weight for funding decisions)

1. **Exposed-superuser chain (F-0024 / F-0045 / F-0048 CRITICAL):** production Postgres reachable on public 5432 for 60+ days, fronted by a 10-character password on a role with `rolsuper=t rolbypassrls=t`. All 24 RLS policies silently no-op. F-0049 role least-privilege closes three CRITICALs in one SQL. **The multi-tenant SaaS story rests on this being fixed.**
2. **Mandate 41.4% (F-0072 HIGH):** the system publishes at two-fifths of its own target. F-0072 decomposes the 70 posts/week gap: ~13 config-recoverable + ~18 defect-recoverable + ~30 capacity-limited (movies). 2.4× observation velocity available before any strategy change. **Largest single lever the audit found.**
3. **Anthropic empty-balance cascade (F-0053 CRITICAL):** weekly 47-CRITICAL pipeline_alerts cascade at $13/mo spend. Auto-reload is a console click. Also feeds F-0072's config-recoverable bucket.

### Largest audit-methodology findings (weight for trusting the audit)

1. **F-0071 CRITICAL:** the closure metric the audit invented at Phase 6 §0.3 was wrong from birth. Drove five revisions and a strategic recommendation across Phases 7.1–7.5. Sibling to F-0061 (Threads-out-of-scope misread), F-0062 (SQL scope-shadow), F-0068 (status-filter shape). All four are "query written without checking what the codebase actually does."
2. **F-0056 HIGH:** journald `SystemMaxUse=500M` invalidated every 30-day error-count query in the audit. The system destroys its own diagnostic history on a rolling window measured in hours.
3. **F-0030 process leak:** a live Slack webhook was written to `.audit/` in Phase 2.5. Redacted; extraction switched to names-only.

**These lists serve different readers.** A due-diligence buyer weighs the first list to decide whether GenLab is worth funding. An audit-consumer weighs the second list to decide whether to trust these findings. Conflating them was Phase 7.6's error.

### Findings softened during writing, restated unsoftened

- **F-0048** — called "decorative." Unsoftened: **the RLS layer is a lie.** Any claim of multi-tenant isolation is untruthful until F-0049 lands.
- **F-0053** — called "near-zero-effort auto-top-up." Unsoftened: **the primary revenue-generating channel stops working on a Sev-2 outage that recurs weekly.**
- **F-0028/F-0039** — called "107 silent sites." Unsoftened: **the codebase is designed to hide its own failures from its own operators.** Every incident retrospective is compromised.
- **F-0056** — called "audit blindness." Unsoftened: **the system destroys the evidence needed to debug itself, on a rolling window measured in hours.** Worse than no observability.
- **F-0014** — called "cannot complete." Unsoftened: **the test suite is not evidence of correctness for anything.**

---

---

## Phase 7 addendum — revisions

Part A of Phase 7 forced four corrections to this document:

**A.1 CREATIVITY 4 → 5.** Structural diversity is uniform (29–37% across 5 niches) with 33/33 opening-token uniqueness, zero banned formulations, caption length on target. That is competent-internal-ship territory. Gaming's 51.2% source-title passthrough (F-0054) is the single explicit reason not to score 6: **a single-channel structural writer defect on the highest-volume channel caps the whole dimension** even when the other four measure strong.

**A.2 The bias question was one-directional.** Redone: **INTELLIGENCE 4 → 5 → 6 (Phase 7.6), CREATIVITY 4 → 5, CAPABILITY 5 → 6.** INTELLIGENCE was raised to 5 in Phase 7, then held at 5 through 7.1-7.5 partly on 48.9% closure — **which Phase 7.6 F-0071 revealed was an audit-invented metric measuring nothing real.** Two independent counts show PF rows ≈ PA publishes on every platform (YT -4, FB 0, IG 0, Threads -3, TW 0). Reward signal is not lossy. Bandit updates 67-68 arms daily. IG trace confirms rewards move real arms end-to-end. **INTELLIGENCE moves to 6** — closed loop with near-100% coverage across all four north-star platforms is above "wouldn't ship" (5); it's at "customer wouldn't notice a problem" (8) for the loop closure specifically. Not 7+ because Neural-LinUCB still absent (F-0057) and the ML ceiling is capped. AUTOMATION stays at 4. COMPLEXITY stays at 3. COMPETENCY stays at 3.

**A.3 F-0047 root cause RECOVERED from DB.** 2026-07-21 pipeline_alerts: 47 CRITICAL — 7 `anthropic_credit_exhausted` → 16 `systemd_unit_failed` → 6 `publish_silence` cascade. Journal lost the details, DB kept them. F-0047 severity: HIGH → MEDIUM (known cause). F-0053 severity: HIGH → **CRITICAL** — this is the cascade root, not a nuisance. Downstream question 3 in "The Three Questions" is answered: replay of 4 blueprint IDs is unnecessary — root cause is F-0053.

**A.4 Threads correction.** Rule #23 places Threads *in* scope (north-star YT/FB/IG/Threads); TikTok and X are the excluded platforms. Threads has been publishing successfully 55–61/niche/120d, last post 2026-07-24 07:04:36, 65–80% success rate — **the highest-performing platform in the stack.** Filed **F-0061 HIGH** against the audit methodology. Open Question 3 restated: **not "is Threads writable" (it demonstrably is) but "is any of the audit's 30-day error-count evidence real"** — F-0056's journald cap means Phase 3A/B error forensics were single-boot single-window.

### Revised scores

| Dimension | Session 1 | Phase 7 | Delta |
|---|:-:|:-:|:-:|
| INTELLIGENCE | 4 | **5** | +1 (rigour-anchor down) |
| AUTOMATION | 4 | 4 → **3 (Phase 7.8, F-0074)** | DOWN 1 |
| CREATIVITY | 4 | **5** | +1 (rigour-anchor down; gaming defect caps at 5) |
| CAPABILITY | 5 | **6** | +1 (Threads mis-excluded) |
| COMPLEXITY | 3 | 3 | held |
| COMPETENCY | 3 | 3 | held |

**Sum 22 → 26/60.** The overall verdict does not change — working automation over a broken foundation — but the "working" half is meaningfully stronger than Session 1 said. The broken half (rolsuper + 5432 + Anthropic-empty cascade + CC=223 + 9,451 tests) is unchanged in severity and now includes F-0053's real cost (weekly 47-alert cascade).

**Word count ~1,900 with addendum. Prose-only ~1,600.** All shells exited before this summary.
