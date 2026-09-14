# SOURCE-02 §1-§2 — the fetcher is HEALTHY; ScoreBat was disabled on purpose.
2026-09-14 ~06:30Z. READ-ONLY, prod.

## §1 `youtube_trending`: NOT degraded. It returns 25 action clips right now.
Ran the real fetcher on prod with the live config:
```
[sports] Category chart: 6 videos (1 unit)
[sports] Subscribed channels: 140 unique videos collected      <- league RSS WORKS
[sports] 70/146 passed filters (velocity>=400, 15-600s)
         [dropped: 30 too-short, 34 too-long, 12 too-slow] | quota: 7 units
=> 25 videos survive the entire fetch funnel
   Race Highlights | Spanish GP · Qualifying Highlights · FP3 Highlights
   USA Erases 14-Point Deficit To Win FIBA World Cup · F2 Sprint Race Highlights
```
**Quota is fine** (HTTP 200, 7 units used). **`youtube_channels` works** — 140 videos from
the official league feeds; my earlier suspicion that it was configured-but-dead was wrong.
**The content is action**, exactly what the format needs.

**So the 189 -> 5 collapse is NOT in the fetcher.** It is downstream: `top_n_per_run: 15`,
URL/video_id dedup, VideoGate (yt-dlp), the composite gate (`min_composite_score: 0.32`),
or the daily cap. Not yet isolated — that is the next measurement, and it needs the funnel
instrumented per stage, not per fetch.

**Leading hypothesis, untested:** the trending chart and league RSS return the SAME videos
day over day. With `video_id` dedup, each one can become a blueprint once. Reddit posts are
fresh daily. So a repeating feed decays toward zero new blueprints while a churning feed
keeps producing — which would make the "collapse" a dedup interaction, not a source failure.
That fits the shape (189 -> 57 -> 7 -> 4 -> 5) but does not explain why May was fine.

**Also observed, tonight-specific:** `[niche-fit] LLM ranking raised (using velocity)` —
niche-fit ranking is degraded to raw velocity because Anthropic is unfunded. Not July's
cause; it IS today's quality hit, and it will clear when the primary is funded or when
Tier A's belt routing covers this call site (it uses a different path — worth checking).

## §2 ScoreBat: NOT a legacy-endpoint problem. Disabled deliberately.
`8354a0b8` (2026-07-14) `fix(sports): disable scorebat — URLs unrenderable by yt-dlp`,
setting `enabled: false` in `ClutchWire/config/sources.yaml`. The commit records both the
cause and the re-enable condition verbatim:
> URLs are `https://www.scorebat.com/<match-slug>-live-stream/` … Re-enable IF scorebat
> starts emitting yt-dlp-compatible URLs, OR if we add a scorebat-specific downloader that
> scrapes the [embed]

This matches the data exactly: 17 (May), 18 (Jun), **5 (Jul — disabled mid-month)**, 0, 0.

**My §2 hypothesis — "v1 is legacy, migrate to v3 with a token" — is wrong.** The endpoint
was never the problem; the DOWNLOAD was. Migrating to v3 would change nothing unless v3
emits yt-dlp-compatible URLs. The real options are (a) a ScoreBat-specific downloader that
scrapes the embed, as the commit itself proposes, or (b) check whether v3 returns direct
media URLs — worth one request before building a scraper.

## Corrected picture of the three "dead" sources
| source | what I claimed | what is true |
|---|---|---|
| `youtube_trending` | 97% degradation, cause unknown | **fetcher healthy, 25 action clips now**; loss is downstream |
| `scorebat` | dead legacy v1 endpoint | **deliberately disabled 07-14**, documented, with a stated re-enable condition |
| `espn_news` | dead since June | unexamined this pass; talk-only, lowest value |

Two of my three diagnoses were wrong, and both were wrong in the same direction: I assumed
an external failure where the cause was internal and documented. The git log answered in
one command what the API probing could not.

## Also confirmed here
The YouTube API **does return `likeCount`** (180791, 42996, 115503 on live items). So
NARR-03 §3's seam is confirmed as purely `to_story()` dropping a field that is present in
the response — a one-field fix, not a data-availability problem.
