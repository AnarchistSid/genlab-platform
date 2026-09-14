# SOURCE-04 §3 — yt-dlp updated. It fixed NEITHER download failure. 2026-09-14 ~07:40Z.
Collision named before acting: the standing "No installs on prod" rule vs §3's instruction
to update yt-dlp on prod. Treated this brief as superseding it for this tool only.

## Done
`/opt/genlab/.venv` yt-dlp **2026.06.06.234447 -> 2026.08.30.232658** (via `uv pip`; the
venv has no `pip` binary). Worthwhile hygiene — 90+ days stale on a tool whose extractors
change weekly.

**`/usr/local/bin/yt-dlp` is a 260-byte bash wrapper** (created Jun 13) that execs
`python -m yt_dlp` from the venv. That is why `ensure_yt_dlp_environment` reported "wrapper
already current" for three months: the wrapper genuinely was current; the package it wraps
was not. The check inspects the wrong object. **Not yet fixed** — §3's second bullet stands.

## RETRACTION — staleness was NOT the cause of the Reddit failures
Today's fire logged:
```
yt-dlp failed for https://v.redd.it/8fckyoodlaph1:
  WARNING: Your yt-dlp version (2026.06.06.234447) is older than 90 days!
```
I read that as the failure cause. **It is a WARNING that the error handler captured as the
failure text.** With yt-dlp now current, the same URL gives the real error:
```
ERROR: [generic] 8fckyoodlaph1: Unable to download webpage: HTTP Error 403: Blocked
```
**Reddit returns 403 to the prod datacenter IP.** That is
`[[class-of-bug-datacenter-ip-bot-detection]]`, whose documented fix ladder is
UA+delay -> cookies file -> Cloudflare tunnel -> residential proxy. A tool update could
never have fixed it.

**Finding in its own right: the download error log surfaces a warning as the cause.** Any
yt-dlp warning printed before the error becomes the recorded reason, which is how a 403 has
been reported as a version problem.

## ScoreBat — 8354a0b8's disable condition STILL HOLDS
v1 is alive and returning **50 items** (so "dead legacy endpoint" was wrong, as already
retracted). The `embed` field carries an `<iframe src='https://www.scorebat.com/embed/v/…'>`.
Extracted three real iframe URLs and tested the updated yt-dlp:
```
https://www.scorebat.com/embed/v/69eede64541f0/  -> ERROR: Unsupported URL
https://www.scorebat.com/embed/v/69ee907b9f786/  -> ERROR: Unsupported URL
```
yt-dlp has **no extractor for scorebat.com/embed**. So the re-enable condition ("IF scorebat
starts emitting yt-dlp-compatible URLs") is **not met**, and the commit's second path — a
ScoreBat-specific downloader that fetches the embed page and finds the real media source
inside it — remains the required work. The API hands us the iframe URL for free, so the
scraper starts one step in.

## Three invalid tests caught before reporting (T-20)
1. A fabricated ScoreBat URL returned 404 — I had guessed the ID. Replaced with real IDs
   from the API.
2. `sudo -u genlab` left `HOME=/root`, so yt-dlp failed on
   `Permission error while accessing modules in "/root/yt_dlp_plugins"` — an environment
   error, not a URL verdict. Re-run with `env HOME=/tmp`.
3. The original staleness reading above.
Each would have produced a confident wrong conclusion.

## Net state after §3
| target | before | after | fixed? |
|---|---|---|---|
| yt-dlp version | 2026.06.06 (90d) | 2026.08.30 | yes — hygiene |
| Reddit v.redd.it downloads | failing | **still failing, 403 datacenter-IP** | **no** |
| ScoreBat | disabled 07-14 | **still unsupported by yt-dlp** | **no** |
| `ensure_yt_dlp_environment` check | inspects the wrapper | unchanged | **no** |

## Consequence for §1
The sports funnel's last stage is worse than measured: Reddit wins all five slots AND its
v.redd.it downloads 403. Making the cut highlight-first (§1) therefore fixes more than the
mix — it routes around a download path that is currently failing.
