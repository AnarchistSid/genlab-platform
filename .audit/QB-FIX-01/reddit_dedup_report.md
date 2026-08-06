# QB-FIX-01 — Reddit auth + dedup TTL reports (2026-08-06)

## Dedup TTL — NO CHANGE NEEDED (correction to prior claim)

Prior F3d-1-gate BLOCKED report asserted "dedup_keys.py doesn't honour url_dedup_ttl_days".

**That was wrong.** Both `dedup_keys.py` (lines 194-199, 221) and
`pre_download_dedup.py` (lines 129-142) DO read `url_dedup_ttl_days` from
niche_config and apply an age-based `_is_within_url_ttl` filter.

Verified via live query on movies queue:

| short_id | status | days_old | in_LIVE | in_TTL(3d) | BLOCKS |
|---|---|---:|:-:|:-:|:-:|
| Violent Night 2 | VISUAL_READY | 0.4 | t | t | **t** |
| Honest Trailers | VISUAL_READY | 0.4 | t | t | **t** |
| Insidious | VISUAL_READY | 0.4 | t | t | **t** |
| Ramayana | VISUAL_READY | 0.4 | t | t | **t** |
| X-Files (published) | PUBLISHED | 1.4 | t | t | **t** |
| Don Hertzfeldt | DRAFTED | 1.4 | f | t | f |
| Spider-Man BND | VISUAL_READY | 2.4 | t | t | **t** |
| Devil's Mouth | DRAFTED | 4.4 | f | f | f |
| Spider-Man BND (dup) | DRAFTED | 4.4 | f | f | f |
| Lamborghini | DRAFTED | 5.4 | f | f | f |
| Clayface | DRAFTED | 5.4 | f | f | f |
| **The Odyssey** | VISUAL_READY | **7.4** | t | **f** | **f** |

The Odyssey (7.4 days old, VISUAL_READY status) is CORRECTLY aged out — LIVE
but NOT in TTL → not blocking. The 6 rows that DO block are all within
the 3-day TTL. TTL enforcement is working as designed.

The remaining blocking is legitimate: YouTube trending returns the same 4-5
trailer titles day after day (Violent Night 2 / Insidious / Ramayana / Honest
Trailers / Spider-Man BND). Each morning's pipeline creates blueprints for
them; same-day retriggers see them as blocked. Not a bug — a feature. The
real remedy is (a) let the blueprints publish and drop into PUBLISHED (which
also blocks but that's correct — you don't want to republish the same trailer),
or (b) source more variety so trending has less overlap.

Logged as ME-09 to `methodology_errors.md` — F3d-1-gate report needs the
correction.

## Reddit auth — FIX SHIPPED

Prior diagnosis identified "Reddit auth cookies missing" as the block. Deep
investigation reclassified: the actual problem was **proxy misrouting**,
not auth.

**Root cause:**
* `.env` on prod has `GENLAB_HTTP_SOCKS_PROXY=socks5h://127.0.0.1:9050` (Tor)
* `fetch_reddit_clips` reads only this env var to pick its proxy
* **Reddit blocks Tor exits entirely** — verified: HTTP 403 hard
* WARP (socks5://127.0.0.1:40000, port yt-dlp uses) DOES work but rate-limits
* Every Reddit call was hitting Tor → 403 → JSON fallback → 403 → give up

**Three commits shipped:**

1. `dcec5b1d` fix(reddit): route Reddit through WARP, add 429 retry, tune pacing
   * New env var `GENLAB_REDDIT_PROXY_URL` (preferred over generic HTTP proxy)
   * 429 retry with 30s backoff, 1 retry (env-tunable via
     `REDDIT_RSS_429_RETRY_SLEEP_SEC` / `REDDIT_RSS_429_MAX_RETRIES`)
   * Skip JSON fallback when RSS was rate-limited (avoid burning more budget)
   * Bumped `REDDIT_INTER_SUB_SLEEP_SEC` default 2s → 5s

2. `928e3f6e` fix(reddit): use list subclass for rate-limit sentinel
   * Live-fire caught: `setattr(list_instance, ...)` raises AttributeError
   * Nested `_RateLimitedList(list)` subclass with `rate_limited: bool` attr
   * Caller checks `.rate_limited` attribute

3. VPS `.env` append (operational, no repo change):
   ```
   # QB-FIX-01 Reddit-auth (2026-08-06): Reddit-specific proxy override
   GENLAB_REDDIT_PROXY_URL=socks5://127.0.0.1:40000
   ```
   Backed up prior .env to `.env.bak-shadow-*` before appending.

**Verification — real movies pipeline run 19:00 IST:**

Before Reddit fix (18:37 IST run):
* Reddit stories aggregated: 12 (variable subset)
* Movies pipeline blueprints: 0
* Root cause: 4 of 5 candidates dedup'd, 1 remaining yt-dlp failed

After Reddit fix (19:00 IST run):
* Reddit stories from ~10 subreddits: r/movieclips 0, r/MovieDetails 1
  (via 30s retry), r/Cinemagraphs 0, r/movies **3/15**, r/marvelstudios 0,
  r/horror 3, others rate-limited-after-retry (correctly skipped JSON)
* **Movies aggregated 123 candidates** (was ~5)
* Scored 123 → 5 stories through PreDownloadDedup (0 dropped by dedup)
* Downloaded: 2 succeeded with F2 1080p AV1 format, 2 failed (yt-dlp
  extractor errors + Reddit v.redd.it needs auth)
* **VideoGate: 4 passed, 1 skipped** (was: 0 passed, 1 skipped)
* Currently rendering 4 reels through FrameCompositor + transformation
  + loudnorm

**Live gate evidence in journal:**
```
[reddit-rss] r/MovieDetails rate-limited (429) — sleeping 30s and retrying (attempt 2/2)
[reddit-rss] r/MovieDetails yielded 1/1 video stories via RSS (window=day)
[reddit] r/MovieReactions RSS rate-limited, JSON fallback skipped (would also 403 from datacenter IP)
[download] [F2] format=399+140 res=1920x1080 vcodec=av01.0.08M.08 acodec=mp4a.40.2 vbr=937.537 abr=129.601
Downloaded: a4fd2c5f6e94fc79.mp4 (52.1s, direct_url)
```

Status: **PASS** on Reddit-fetch verification. Full-pipeline blueprint
verification pending FrameCompositor completion (~15-20 min for 4 reels
at 1080p medium preset).

## Followups NOT in scope
* yt-dlp extractor errors on IEFmpe2QYA8 + qqzgSjZQlLo — upstream yt-dlp
  or per-URL SABR-driven bugs. Not fixable at pipeline layer.
* Reddit OAuth (600 rpm real fix) — Developer Program approval delay.
* Reddit v.redd.it downloads — require Reddit auth cookies (separate from
  the RSS/JSON proxy issue). Currently the ~50% of Reddit stories that
  have v.redd.it URLs still fail at download stage. yt-dlp needs
  --cookies for Reddit; same operator workflow as YouTube cookies for F2.
