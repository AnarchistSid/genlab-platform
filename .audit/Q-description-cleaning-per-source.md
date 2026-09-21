# Q — Description cleaning per source (filed 2026-09-21, ANIME-09 §3)

**Not built. Filed with the measurement that motivates it.**

## What happened

ANIME-08 §2 raised the story-summary cap from 200/255 to `SUMMARY_MAX_CHARS`
(4000) across four truncation sites. The fix is correct for AniList and
actively harmful for YouTube, and the 10:21Z anime fire showed both in one
run:

| summary | script | narration_state |
|---|---|---|
| **642 chars** (link block) | **0 words** | `degraded:script_too_long` |
| 51 chars (synthesized) | 0 words | `degraded:script_too_long` |
| 51 chars (synthesized) | **27 words** | `ok` |

The 642 characters, verbatim:

```
I love Mushoku Tensei so much. I hope Season 3 ends well.

Featured Artist
Aretard - https://x.com/Ans7Xd
--------------------------------------------------------------------------
Support me on Patreon  / https://www.patreon.com/c/AniKhang
Follow me on Instagram:   / https://www.instagram.com/anikhang1/
Follow me on MyAnimeList:   / https://myanimelist.net/profile/AniKhang
Follow me on Twitter:   / https://x.com/anikhang
Join my Discord server:  / https://discord.gg/tmBpfsj96x
--------------------------------------------------------------------------
Music Used
Dead Weight (instrumental) - https://www.youtube.com/watch?v=tzCrOP0a_MQ
```

One sentence of content, then a creator's link block. Raising the cap did not
feed the writer more story; on the YouTube path it fed it more boilerplate.
The only usable script in the run came from the SHORTEST input.

## Why it is not a revert

AniList descriptions are real synopses, and the truncation there was severing
the story's turn — Firefly Wedding's description is cut mid-phrase at 255 on
"...redeem her worth in her", and the sentence after it is the one where she
proposes marriage to the assassin sent to kill her. The cap must stay lifted
for that source. The problem is per-source, so the fix must be too.

## The work

In `media/trending_video_fetcher.py`, before a description becomes a summary:

* strip URL runs and the `---` rules that bracket them
* strip lines starting `Support me`, `Follow me`, `Join my`, `Music Used`,
  `Featured Artist`, `Credits`, `Timestamps`, `Chapters`
* strip trailing hashtag runs
* strip social handles (`@x`, `x.com/`, `patreon.com/`, `discord.gg/`)
* if what remains is under the writer's 40-char floor, fall through to the
  SYNTHESIZED summary rather than passing the residue

AniList descriptions (`fetch_anime_promos`) pass through untouched.

## The gate that tells you it worked

Not summary length. Re-run the fire and compare `narration_state` and script
word count against summary length: the correlation should stop being
negative. Today the longest summary produced nothing and the shortest
produced the only `ok` script.

## Measured context

* `SUMMARY_MAX_CHARS = 4000`, four sites (`dfb46c81` fixed three the first
  pass missed because a grep was piped through `head -8`)
* writer thin-context floor: 40 chars (`base_writing._has_writable_context`)
* `_writable_summary` already synthesizes from title/channel/tags when the
  description is under the floor — that path produced the run's only working
  script, which is a hint about where the value is
