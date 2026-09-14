# SOURCE-01 §1 — ClutchWire's highlight feeds are dead. 2026-09-14 ~05:30Z. READ-ONLY.

## Yield by source (measured, prod DB)

**Last 14 days, sports:** reddit:boxing 10 · youtube_trending 5 · reddit:MMA 4 ·
reddit:Cricket 4 · reddit:baseball 4 · reddit:formula1 3 · reddit:hockey 2.

**All time, sports:** youtube_trending 483 · espn_news 231 · scorebat 85 ·
reddit:boxing 25 · reddit:baseball 22 · reddit:formula1 20 · reddit:MMA 19 ·
rss_espn 17 · rss_bbc_sport 15 · reddit:hockey 14 · espn_scoreboard 13 ·
reddit:Cricket 8 · sports 1.

**Every Reddit source that yields is a DISCUSSION sub.** Not one highlight sub appears —
ever.

## The highlight subs ARE configured and have NEVER produced a blueprint
`ClutchWire/config/sources.yaml` → `reddit.subreddits` lists `nbahighlights`,
`nflstreams`, `soccerhighlights`, `footballhighlights` alongside `MMA`, `boxing`, …
Their all-time blueprint count is **zero**.

## Why — two distinct failures, measured from prod via the RSS path the fetcher uses
| sub | HTTP | entries | v.redd.it | youtube | reading |
|---|---|---|---|---|---|
| r/nbahighlights | 200 | **0** | 0 | 0 | responds, returns nothing — dead/renamed/private |
| r/soccerhighlights | 200 | **0** | 0 | 0 | same |
| r/footballhighlights | 200 | **13** | **0** | **0** | posts exist, **carry no video links at all** |
| r/boxing | 429 | — | — | — | rate-limited here; yields 25 in prod |
| r/MMA | 429 | — | — | — | rate-limited here; yields 19 in prod |

Two different defects wearing the same symptom:
1. **`nbahighlights` / `soccerhighlights` return zero entries.** The feed is empty.
2. **`footballhighlights` returns 13 entries with zero video links.** It is not empty; it
   simply carries nothing fetchable.

## The ranking question, answered — it is NOT the cause
`listing: top`, `time_window: day`, `per_sub_limit: 6`. The hypothesis was that
upvote-ranking selects press-conference drama over highlights. **It does not get the
chance:** ranking only orders what a feed returns, and the highlight feeds return nothing
(or nothing with video). The discussion subs yield because they are the only Reddit feeds
producing fetchable video at all, and what they produce is talk.

So the defect is not "the ranking prefers talk". It is **"the only live feeds are talk
feeds"** — a harder problem, because retuning the ranking cannot fix it.

## Instrument note (T-20)
My first test ran from the Mac and returned **403 Blocked on all seven subs, including the
ones that demonstrably yield in production**. Uniform across inputs = instrument. It was my
IP; Reddit blocks the datacenter/residential path differently — the known
`[[class-of-bug-datacenter-ip-bot-detection]]` family, inverted (here the *local* host is
blocked and prod is not). Re-run from prod via the RSS endpoint the fetcher actually uses.
Subsequent 429s are self-inflicted rate-limiting from my own back-to-back requests, not a
property of those subs.

## What this means
ClutchWire has been a highlights channel with no highlight feed since it started, and
nothing downstream could reveal it: the fetchers report success (they DO return items), the
pipeline renders fine, and every check reads the composited output. The classifier corpus
(TREAT-01) was the first artifact that looked at what was actually being fetched.

## Not done this pass
* §1's per-item hand classification for ScoreBat, `youtube_trending`, `espn_news` — the
  three largest sources by volume, and `youtube_trending` is 483 all-time. Whether THOSE
  carry action is unmeasured and matters more than Reddit by volume.
* §2 (action sources by rights and yield), §3 (ingest integrity gates), §4 (ACTION
  fixtures), §5 (TALK demo).
