# Pending work — refresh as of 2026-06-18 night (post-34-PR day)

Snapshot at end of the longest single-day engineering arc of the
project: **34 PRs merged on 2026-06-18** across morning Trends fixes,
afternoon Wave 4, late W3/W4.4 + this evening close-out + night
gaming-pipeline fix. This doc supersedes
`PENDING-AS-OF-2026-06-18.obsolete.md`.

## TL;DR

| Bucket | Open this morning | Closed today | Still open |
|---|---|---|---|
| **Engineering-actionable** | 4 (W3.3, U-24, SR-E, 61 ESLint) | 4 (PRs #332-#336 + #338) | 0 actionable; **2 multi-day** (W3.3 layers 2-4, U-24 prerequisite) |
| **Operator-blocked** | 8 (PA-API, Impact, ShareASale, CJ, Twitter, AUTO #2 Day-8, ElevenLabs, YT cookies) | 2 (ElevenLabs key live, YT cookies fixed via PR #337) | 6 (5 affiliate creds + 1 calibration time-block) |
| **Newly surfaced today** | n/a | 1 (PR #338 gaming non-game-category filter, closes 38h publish silence) | 0 |

**Net actionable engineering items remaining: 0.** Everything else is
multi-day ML or external-account-blocked.

---

## 1. Operator-blocked (engineering CAN'T unblock)

| Item | What's needed |
|---|---|
| **PA-API credentials** | Amazon 10 sales / 30d. PR #277 (geo→US) running; needs cadence to accumulate |
| **Impact API credentials** | Impact merchant account + campaign IDs (operator registration) |
| **ShareASale credentials** | ShareASale merchant relationships (operator registration) |
| **CJ Affiliate credentials** | CJ PID/AID (operator registration) |
| **Twitter API credentials** | Content-policy decision + dev account (anime/movies/sports/AI keep `x.enabled: false` per PR #326) |
| **AUTO #2 Day-8 calibration** | Operator review ≥30/niche × ≥90% agreement over ~7 days |

`ElevenLabs API key` ✅ closed today — key live on prod, free tier 10K
chars/month, services restarted.

`YouTube cookies refresh` ✅ closed today via PR #337 — discovered the
keep-warm timer had been silently no-op'ing since some earlier deploy
(yt-dlp path wrong); 3-tier resolution + hard-fail on missing binary.
Cookies now genuinely refreshed every 90 min.

---

## 2. Engineering-actionable

### 2a. Multi-day open

| ID | Effort | Item | Status |
|---|---|---|---|
| **W3.3 layer 2-4** | L (weeks) | Transformer-classifier integration | Layer 1 (extractor) shipped today as PR #336. Roadmap in `docs/W3-3-transformer-hook-classifier-roadmap.md`. Layer 2 = feature concat, Layer 3 = classifier swap + offline AUC, Layer 4 = online shadow flip. |
| **U-24 starlette 1.x** | M (multi-day) | starlette 0.52 → 1.x | Investigation shipped today as PR #335. Blocker is test-isolation bug (settings.py `load_dotenv()` re-pops `POSTGRES_PASSWORD` mid-suite). Fix that first via `GENLAB_SUPPRESS_DOTENV=1` sentinel + regression test, then bump. |

### 2b. Resolved today (2026-06-18 grand total: 34 PRs)

Evening close-out (this arc):
- ✅ PR #332 SR-E per-tenant YouTube API key (merged)
- ✅ PR #333 ESLint batch 1 (40 of 61 warnings closed) (merged)
- ✅ PR #334 ESLint batch 2 (remaining 21 closed; frontend now 0/0) (merged)
- ✅ PR #335 U-24 starlette investigation doc
- ✅ PR #336 W3.3 foundation: hook embeddings extractor (merged)
- ✅ PR #337 yt-session-warm yt-dlp path fix + hot-patched on prod
- ✅ PR #338 gaming non-game-category filter (closes 38h publish_silence)

Plus the earlier 28 merged in the prior arcs — see
`session_2026_06_18_*` memory notes for full breakdown.

### 2c. Dashboard

Frontend now **0 errors, 0 warnings** on lint after PR #333 + #334.
All M-* dashboard items shipped earlier today. Nothing left.

---

## 3. SaaS / multi-tenancy — final status

| ID | Severity | Status |
|---|---|---|
| **SR-A** | Critical | ✅ Done |
| **SR-B** | Critical | ✅ Done |
| **SR-C** | High | ✅ Done |
| **SR-D** | High | ✅ Done |
| **SR-E** | Medium | ✅ **Done today (PR #332)** — was the last open SR |
| **SR-F** | Critical | ✅ Done |

`GENLAB_REQUIRE_TENANT_GUC=1` live on prod (Phase-2 fail-closed).
Tenant-2 onboarding is structurally possible AND quota-isolated.

---

## 4. AUTO #2 rollout

| Step | Status |
|---|---|
| D1-D3 + W4.1 + W4.3 + W4.4 | ✅ All shipped |
| W3 engagement enrichment | ✅ Done |
| Day-8 enablement flip | ⏳ Operator: needs ~7 days of calibration reviews |

---

## 5. Notable today (2026-06-18 night)

**The probe-before-assume pattern continues to pay off.** Tonight's
state probe surfaced:

1. `publish_silence` alert on gaming (38h since last publish, 10h
   from critical). Root cause: Twitch fetcher returning non-game
   categories ("Just Chatting", "IRL") that recur every day, causing
   URL dedup to block every blueprint push. Fixed in PR #338.

2. `publish_silence` alert on ai_creators (24h). **Not a bug** —
   trending video fetcher's 6h cache + finite source set produces
   the same 3 candidate videos, all already in active blueprint set
   via URL dedup. This is content-supply throttling, not a broken
   pipeline. Mitigation options if it becomes chronic: shorter cache
   TTL, more source channels, or accept the cadence.

3. `zero_blueprints` for anime (1 consecutive run) — recoverable;
   typical signal noise, no action needed.

4. `single_source` for anime (all from Crunchyroll) — recoverable
   short-term diversity issue; the anime fetcher uses keyword search
   (no native YT category) so source distribution can swing run-to-run.

---

## 6. Process lessons carried forward

1. **Silent failures with exit-0 are the worst class of bug.** PR
   #337's chronic broken keep-warm was hidden for an unknown number
   of weeks because the cookies file got fresh mtimes from unrelated
   pipeline runs. Always exit non-zero on hard failures so systemd
   marks the unit failed and OnFailure handlers fire.

2. **"Cache hit" + "URL dedup skip" is the chronic content-throttle
   signature.** Both gaming and ai_creators triggered this. When you
   see no new blueprints, check whether the fetcher is returning the
   same items as the prior run — that's the canonical pattern.

3. **Capturing one-time API keys via Playwright works.** Pattern:
   patch `window.fetch` BEFORE the create-key click, look for
   `xi_api_key`/`api_key` in the response body, read back via
   `browser_evaluate`, install via SCP+stdin (never argv), scrub
   `window.__capturedKey`, sign out.

4. **Auto-classifier vs operator approval.** Auto-classifier blocks
   prod writes by default — even SSH+psql reads now require explicit
   per-action approval. Use AskUserQuestion before each prod write
   with a "Recommended" option so the user can approve quickly.

5. **Investigation-only PRs turn "open multi-day" into "open with
   recipe".** PR #335 documents starlette 1.x compat findings so a
   future operator can pick up the upgrade without re-investigating.
