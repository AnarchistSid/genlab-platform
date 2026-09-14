# SOURCE-01 §1b — I had it backwards. The action sources DIED; Reddit backfilled with talk.
2026-09-14 ~06:00Z. READ-ONLY, prod DB + config reads.

## RETRACTION of my §1 framing
I reported "ClutchWire isn't fetching highlights" and audited Reddit. **Reddit is a
symptom, not the cause.** Hand-labelling the three big sources by title shows they carry
exactly the action the format needs:

| source | 20-item read | verdict |
|---|---|---|
| `youtube_trending` | "Qualifying Highlights \| Spanish GP", "49ERS CLUTCH GOAL-LINE STOP", "Ohtani belts a TWO-RUN HOMER", "Max Holloway's Iconic KO", "Race Highlights \| Austrian GP" | **~18/20 ACTION** |
| `scorebat` | 12/12 `Arsenal - Newcastle`, `Getafe - Barcelona`, `Angers - PSG` | **12/12 ACTION** (match highlights by construction) |
| `espn_news` | "NFL closes conduct review", "Travis Kelce joins Taylor Swift", "Why McAfee isn't concerned" | **12/12 TALK** — and dormant since 06-13 |

## The actual defect: a three-source collapse, backfilled by Reddit
Sports blueprints per month by source:

| month | yt_trending | scorebat | espn_news | reddit | total |
|---|---|---|---|---|---|
| 2026-05 | **189** | 17 | 39 | 0 | **251** |
| 2026-06 | 57 | 18 | 32 | 0 | 122 |
| 2026-07 | 7 | 5 | **0** | 21 | 33 |
| 2026-08 | 4 | **0** | 0 | 60 | 64 |
| 2026-09 | 5 | 0 | 0 | 27 | **32** |

* `youtube_trending` — **189 → 5, a 97% collapse.** Still alive, barely.
* `scorebat` — **17/18 → 0. Dead since August.**
* `espn_news` — **39/32 → 0. Dead since July.**
* `reddit:*` — **appears in July, from nothing to 60/month.** It was added (or promoted) as
  the action sources failed, and it delivers discussion, not moments.
* **Total sports output is down 87% from May** (251 → 32).

## This is the same event as SWEEP-01's composite collapse
SWEEP-01 §B measured sports composite falling from median ~1.000 (May/June) to ~0.483 in
**July 2026**, then pinning at 0.480. The source mix flips to Reddit in **July 2026**.
Same month, same cause: Reddit stories carry no engagement data, so `engagement_factor`
sits at its 0.5 floor and the composite degenerates to a constant. **Two separately-
investigated defects are one event** — the sports source collapse.

## Why my TREAT-01 corpus was 4/4 press conferences
I sampled recent clips. In the last 14 days Reddit outnumbers youtube_trending **27 to 5**,
so a recent sample is ~84% Reddit, and Reddit is talk. The corpus was an accurate sample of
*today's* sourcing and a misleading sample of what the pipeline is capable of.

## Consequence for the whole TREAT-01/STYLE-01 arc
The anime-edit format is NOT blocked on rights or on finding a new action source. It is
blocked on **restoring three sources that already worked three months ago**. That is a
repair, not a procurement.

## What to investigate next, in order of volume
1. **Why did `youtube_trending` fall 189 → 5?** Largest source, still alive, so this is a
   degradation rather than an outage — quota, the relevance filter, the composite quality
   gate, or the dedup TTL are all candidates. One of them started rejecting almost
   everything in July.
2. **Why did `scorebat` go to zero in August?** `fetch_scorebat.py:26` requests
   `https://www.scorebat.com/video-api/v1/` — v1 is ScoreBat's legacy endpoint and their
   current API is v3 with a token. A dead/deprecated endpoint fits "17, 18, 5, 0, 0".
3. **Why did `espn_news` go to zero in July?** Talk-only, so lowest value to restore.

## Source attribution is too coarse to answer #1 cleanly
`youtube_channels` (official NBA/NFL highlight RSS, `category: sports_highlights`) IS read
at `trending_video_fetcher.py:1461` — but both it and the mostPopular chart label their
output `youtube_trending`. There is no way to tell from the DB which YouTube path produced
a blueprint. §3's per-source yield metric needs **sub-source** granularity or it will not
answer this question either.
