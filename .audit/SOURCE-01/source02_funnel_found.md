# SOURCE-02 §1b — the funnel narrows at SCORING, not dedup. 2026-09-14 ~06:45Z. READ-ONLY.
Measured from today's real sports fire (05:00Z / 10:30 IST), using the decision trace
`b821d61a` already added to pre-download-dedup. Control: 381 journal lines in window.

## The funnel, per stage
| stage | in | out | note |
|---|---|---|---|
| YouTube fetch (filters) | 106 | **51** | dropped 22 too-short, 23 too-long, 10 too-slow; quota 22 units |
| `top_n_per_run` cap | 51 | ~15 | config `top_n_per_run: 15` |
| Relevance filter | 15 | **14** | rejected=1 |
| Quality gate | 14 | **14** | 14/14 passed |
| Reddit fetch | — | **5** | across 10 subs |
| **`SportScoringStrategy`** | **21** | **5** | **"dropped 2 below 0.30; 0 trending-video stories bypassed threshold"** |
| `PreDownloadDedup` | 5 | **5** | **dropped 0 url, 0 video_id** |
| VideoGate | 5 | fewer | "no valid clip for 'Viggo Björck sets up Burnside…' — skipping" |

## The dedup hypothesis is REFUTED
`[PreDownloadDedup] 5/5 kept (dropped 0 url, 0 video_id)`. Dedup discards **nothing** on
this fire. The "repeating feed decays through video_id dedup" mechanism does not occur.
Held against May and it fails there too — it never needed a May-compatible answer because
it does not happen at all.

## The actual narrowing: `cw_strategies.scoring`, 21 -> 5
`[sports] Scored 21 -> 5 stories (dropped 2 below 0.30; 0 trending-video stories bypassed threshold)`

Only **2** were dropped by the 0.30 threshold. The other **14** were lost to selection, not
rejection — the scorer emits a top-N, and YouTube items compete against Reddit items for
those slots. **This is where 14 healthy action clips become at most a handful.**

## The clause that matters most
**"0 trending-video stories bypassed threshold."** There is an explicit bypass intended to
protect trending-video stories from the scoring threshold, and **zero stories used it** on a
fire where 14 trending videos were present and passed both the relevance and quality gates.
Either the bypass predicate does not recognise these stories as trending-video, or it is
wired to a field they no longer carry. That single clause is the most likely cause of the
189 -> 5 collapse, and it is one log line that has been printing "0" every fire.

## Corrected chain of causes for the sports collapse
1. `youtube_trending` fetch — **healthy** (25 action clips/run; 140 from league RSS).
2. `top_n_per_run: 15` + relevance + quality — **working as configured**, 14 survive.
3. **`SportScoringStrategy` selects 5 of 21, and the trending-video bypass never fires** —
   the loss.
4. ScoreBat — **deliberately disabled 07-14** (yt-dlp can't render its URLs).
5. Reddit — backfilled from July and wins scoring slots, delivering talk.
6. Composite pinned at 0.48 — because Reddit carries no engagement data (SWEEP-01 §B).

## Next measurements, in order
* Read the bypass predicate in `cw_strategies/scoring.py` and determine which field it
  tests. One read; likely a one-line fix or a field-name drift.
* Confirm the top-N: is the scorer capping at 5 by config, and what is it?
* Then re-measure the same funnel on the next fire with the bypass working.

## Not yet done
§3 ScoreBat probes (v3 token / yt-dlp against a current embed), §4 niche-fit on belt,
§5 `to_story()` `like_count`.

# =====================================================================
# SOURCE-03 §1 — CORRECTION + two live defects. 2026-09-14 ~07:00Z.
# =====================================================================

## Correcting my own "the bypass is dead" claim
The predicate is `c.get("_trending_video")` (`cw_strategies/scoring.py:192`), and that field
IS emitted by `TrendingVideo.to_story()`. The counter is:
```python
trending_bypassed = sum(1 for c in scored
                        if c.get("_trending_video") and c["final_score"] < min_score)
```
It counts trending videos scoring BELOW threshold. **`0` therefore means "no trending video
needed the bypass" — which reads as health, not failure.** My "a working counter reporting a
broken mechanism" framing was wrong about this counter.

## But the conclusion survives, by a different mechanism
The top-N cut is explicitly video-first (`scoring.py:199-203`):
```python
video_stories = [s for s in above if s.get("_trending_video")]
rest          = [s for s in above if not s.get("_trending_video")]
above = (video_stories + rest)[:top_n]        # top_clips_per_run = 5
```
So with 14 trending videos among the 21 scored, **all 5 slots should be trending.**

Measured, today's fire — the 5 that reached `DownloadTopVideos`:
```
[1/5] Viggo Björck sets up Burnside for a shorty …      (reddit:hockey)
[2/5] Why the Sedin twins will never quit on Vancouver  (reddit:hockey)
[3/5] ON THIS DAY!! Terence Crawford moves up two …     (reddit:boxing)
[4/5] Jai Opetaia and David Benavidez meet face to face (reddit:boxing)
      direct_url: v.redd.it/… , v.redd.it/… , v.redd.it/… , youtu.be/…
```
**Not one F1/FIBA/MLB highlight.** Video-first selection did not put a single trending video
in the top 5, on a fire where 14 were present and passed relevance and quality.

**Therefore `_trending_video` is absent (or falsy) on those stories by the time scoring
runs.** Same class as `narration_script`: a field the producer sets and the consumer cannot
find. And it explains `trending_bypassed = 0` for the OTHER reason — not "none needed the
bypass" but "none were recognised as trending at all". The two readings are
indistinguishable from the counter alone, which is why the counter could not have caught it.

**NOT directly confirmed.** I have not yet read a story dict at the scorer's input. That is
the one decisive test and it is the next thing to do: log or dump `_trending_video` presence
for the scorer's input set on one fire.

## SECOND LIVE DEFECT — yt-dlp is 90+ days stale and failing Reddit downloads
```
yt-dlp failed for https://v.redd.it/8fckyoodlaph1:
  WARNING: Your yt-dlp version (2026.06.06.234447) is older than 90 days!
yt-dlp failed for https://v.redd.it/t04r7b2v3cph1:  (same)
```
Two of the four Reddit clips failed to download on this fire. The wrapper reports
`[ensure_yt_dlp_environment] yt-dlp wrapper already current` — **the wrapper is current; the
yt-dlp binary it wraps is not.** A freshness check that reports "current" about the wrapper
rather than the tool is its own finding, and it is why a 90-day-stale downloader has been
running unnoticed.

This compounds: the only stories reaching download are Reddit, and half of those fail to
download. That is the end of the funnel, and it is why sports output is 32/month.

## Revised chain (third revision — each step measured)
1. fetch healthy — 25 action clips/run, 140 from league RSS
2. filters/caps working — 14 trending survive relevance + quality
3. **`_trending_video` lost before scoring** -> video-first cut selects none of them
4. Reddit talk takes all 5 slots
5. **yt-dlp 90+ days stale** -> ~half the Reddit clips fail to download
6. scorebat disabled 07-14 (yt-dlp couldn't render its URLs — same tool, same era)
7. composite pins at 0.48 on Reddit's missing engagement data

Steps 3 and 5 are both live, both one-line-ish, and both invisible to every existing metric.
