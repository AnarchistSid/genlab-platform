# INV-01 — Consolidation inputs
**Window: 14 days ending 2026-09-10. Measured only. No estimates.**

| niche | compliance warns | reels published | reward mean (48h) | follower Δ | VO tier |
|---|---|---|---|---|---|
| ai_creators | 32 | 8 (8 active days) | 0.0563 (31/32) | FB +3, IG 0, YT 0 | `UNMEASURABLE` |
| gaming | 61 | 9 (9 days) | 0.0527 (34/36) | FB 0, IG 0, YT 0 | `UNMEASURABLE` |
| movies | 40 | 10 (10 days) | 0.0855 (40/40) | FB +8, IG 0, YT 0 | `UNMEASURABLE` |
| sports | 36 | 9 (9 days) | **0.1871** (33/36) | FB 0, IG 0, **YT +7** | `UNMEASURABLE` |
| anime | 4 | **1** (1 day) | `UNMEASURABLE` (0/4 rewarded) | FB 0, IG 0, YT 0 | `UNMEASURABLE` |

**`UNMEASURABLE: VO tier`** — `media["audio_provider"]` is written in memory by
`generate_audio.py` but never persisted. `blueprints.extra->>'audio_provider'`
is absent on **all 158 blueprints** in the window. The tier cannot be recovered
from the DB, and journal retention (below) rules out recovering it from logs.
This blocks the playbook's "hero reel on Edge/gTTS = DEGRADED" determination for
every niche. (M — enumerated, not sampled.)

**`UNMEASURABLE: anime reward mean`** — 4 feedback rows, 0 carry `reward_48h`.
Not a zero; no observations exist.

## Absolute audience (context for the deltas)

| niche | FB fans | IG followers | YT subs |
|---|---|---|---|
| ai_creators | 10,022 | 0 Δ | 5 |
| movies | 8,653 | 0 Δ | 4 |
| anime | 56 | 0 Δ | 0 |
| gaming | 26 | 0 Δ | 0 |
| sports | 19 | 0 Δ | **17** |

The two five-figure FB counts sit on legacy pages whose provenance is an open
operator blocker (H-07). They are **not** comparable to the other three and
should not be read as reach. Total measured follower movement across all ten
channel×platform cells in 14 days: **+11**.

## What the numbers support

- **anime is the outlier on every axis**: 1 reel vs 8–10, zero rewards, zero
  audience movement. Its low compliance-warn count (4) is a *consequence of not
  publishing*, not a safety signal — do not read it as a positive.
- **sports is the only channel with subscriber movement** (+7 YT) and the
  highest reward mean (0.1871, 3.5× gaming). On a per-reel basis it is the
  best-performing channel measured.
- **gaming carries the highest compliance load** (61 warns) for mid-pack reward
  (0.0527, lowest of the four publishing niches).

These are inputs, not a recommendation. The decision is the operator's.
