# Q — ai_creators: four downloads, four 120 s timeouts (filed 2026-09-21, ANIME-10 §1)

**Read, not fixed.**

## What happened

Run `ai_creators_20260921_112307` (16:53–17:05 IST). Funnel:

```
8 trending videos -> 3 past relevance -> 4 stories through dedup
4/4 found source URLs
VideoGate: dropped 4 clipless stories; remaining=0
RunReport: ai_creators | failed | 763s | stories=0 blueprints=0
```

`clip_index.json`: `"videos_failed": 3`, every YouTube entry
`"error": "download timed out"`.

Per URL:

| url | outcome |
|---|---|
| `youtube.com/watch?v=dzZlf2OYRnU` | timed out after 120 s |
| `youtube.com/watch?v=5CkaWCQKDd4` | timed out after 120 s |
| `youtube.com/watch?v=diP0ylIZtQE` | timed out after 120 s |
| `youtube.com/watch?v=sOnf…` | timed out after 120 s |
| `v.redd.it/k5bifyfc6uqh1` | `ERROR: [Reddit] 1wm769v: Account authentication is required` |

All four timeouts land at 17:05:48 within 700 ms of each other, so they ran
CONCURRENTLY and all consumed the full 120 s
(`tuning.yaml download.timeout_seconds: 120`).

## Not cookies

Checked first because it is the usual suspect. `YT_DLP_COOKIES_FILE`
(`/opt/genlab/.runtime/yt_cookies.txt`) was 7450 bytes, mtime 1 h before the
read, and `genlab-yt-session-warm.service` last exited `success`. The jar is
healthy; this is not a stale-cookie failure.

## What to look at

Four parallel yt-dlp against a 4 GB box that was at 571-613 MB free with
other pipelines queued. Candidates, in the order worth measuring:

1. Throughput under concurrency — time ONE of these URLs by hand, unloaded,
   and see whether 120 s is even the right budget for it.
2. Download concurrency — four at once on this box may simply not fit.
3. Whether 120 s is right at all: it is a flat budget for a clip whose size
   nobody measured.

Do not raise the timeout before (1). A timeout raised without measuring
throughput converts a fast failure into a slow one.

## Sibling read, same run: relevance

Two of the four candidates that passed the AI-news relevance gate
(threshold 0.30) were MUSIC TRACKS:

```
[Electronic Metal] Hyper Motion – Neon Pulse | ネオンが駆け抜ける
[PopPunk] S.H.G.
```

`RelevanceGate: 4/5 stories passed relevance filter (threshold=0.30)` —
so the gate rejected one item and admitted both of these. On a channel whose
niche is AI news, a PopPunk track is not a borderline call.

Worth reading alongside the anime finding that its threshold (0.35, the
strictest) is carrying a keyword-search intake. ai_creators is at 0.30 with a
category-and-RSS intake and is admitting music.

## Method note

The first pass at this reported "2 minutes 19 seconds of complete silence"
from the download stage. That was false: the four timeout WARNINGs were in
the journal all along, and the grep used `yt-dlp failed` and `download.*fail`
— neither of which matches `yt-dlp timed out`. Second time in two turns a
filtered search was reported as an absence. A negative search result is a
claim about the search.
