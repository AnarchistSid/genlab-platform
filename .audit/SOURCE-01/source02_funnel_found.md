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
