# CONTENT-01 §3 — spoken_text accumulation check
Run 2026-09-13 06:20Z. READ-ONLY. Zero production writes beyond .audit/. No flags flipped.

## STEP 0 — GATE: all five fired and completed. [Measured]
LoadState=loaded on all five (proof the units are real; `genlab-pipeline@<niche>` and
`genlab-pipeline-ai-creators` remain phantoms and were not consulted).

| niche | LoadState | start (UTC) | exit (UTC) | status | result |
|---|---|---|---|---|---|
| ai | loaded | 02:30:10 | 02:55:02 | 0 | success |
| movies | loaded | 03:30:13 | 03:37:47 | 0 | success |
| gaming | loaded | 04:00:12 | 04:21:25 | 0 | success |
| sports | loaded | 05:00:12 | 05:06:48 | 0 | success |
| anime | loaded | 06:00:12 | 06:06:16 | 0 | success |

Anime completed 06:06:16Z, 14 min before this check. No niche is NOT-YET-RUN.

## POSITIVE CONTROL [Measured]
Blueprints created since 02:00Z: ai_creators 3 · anime 1 · gaming 6 · movies 3 · sports 2
(15 total). Non-zero, so a zero in the check below would have been a real zero.

## STEP 1 — one row per niche [Measured]

| niche | bp | has_key | chars | tier | degraded | script_chars | created |
|---|---|---|---|---|---|---|---|
| ai_creators | 36e3d530 | t | 167 | infsh_inworld | **true** | 0 | 09-13 02:55 |
| anime | 468ef67e | t | 126 | infsh_inworld | false | 0 | 09-13 06:06 |
| gaming | 13a5f258 | t | 117 | infsh_inworld | false | 0 | 09-13 04:21 |
| movies | 88c9e095 | t | 147 | infsh_inworld | false | 0 | 09-13 03:37 |
| sports | 3f29c773 | t | 159 | infsh_inworld | false | 0 | 09-13 05:06 |

**(a) PASS** — all five carry `spoken_text`, key present, 117–167 chars, none empty.
The six-stage pass-through (GenerateAudio stage 15 -> PushToBacklog stage 21) holds on
all five niches. This is the shape that killed `narration_audio_path`; it did not recur.

**(c) PASS** — `audio_provider` stamped `infsh_inworld` on all five; `narration_degraded`
stamped on all five. FIX-T01 unaffected. Note ai_creators is `degraded=true` while the
other four are false.

**(b) NOT ESTABLISHED — and the content is wrong. [Measured content / Unmeasurable journal]**
* Journal cross-check is **UNMEASURABLE**: `journalctl -u genlab-pipeline-anime.service`
  for 06:00–06:10Z returns **1 line total** (positive control run before reporting the
  zero). No GenerateAudio line survives to compare against.
* The ai_creators identity check has no left-hand side: **`narration_script` is 0 chars on
  all five niches**, so "spoken_text should equal narration_script when narration is on"
  cannot be evaluated.
* What IS measurable is the stored value itself, and it is **caption text, not narration**.
  Anime 468ef67e in full:
      The Apothecary Diaries Season 3
      Via
      #Anime #AnimeReels #Apothecary
      🎬 Original: https://www.youtube.com/watch?v=9rProUQlD-I
* **All 15 blueprints created today, all five niches: `spoken_text` contains a hashtag,
  a URL, and "Via".** 15/15 on each of the three markers.

So `spoken_text` faithfully records what TTS was handed — and what TTS was handed is a
caption. Today's voice-overs speak hashtags and a YouTube URL aloud.

## STEP 2 — historical rows stay NULL [Measured]
`has_key = 0` of `total = 2625` rows created before 2026-09-13 00:00Z. No backfill occurred.

## STEP 3 — VERDICT, per niche
| niche | verdict |
|---|---|
| ai_creators | **spoken_text PRESENT (167 chars)** |
| anime | **spoken_text PRESENT (126 chars)** |
| gaming | **spoken_text PRESENT (117 chars)** |
| movies | **spoken_text PRESENT (147 chars)** |
| sports | **spoken_text PRESENT (159 chars)** |

All five PRESENT. The §4 precondition as written ("until ALL FIVE show spoken_text, §4
does not start") is **satisfied**.

## BUT — §4 SHOULD NOT START, for a reason the precondition does not cover. [Inferred]
§4 flips `whisper_sync`, and whisper aligns caption timings **against `spoken_text`**.
`spoken_text` currently contains `#Anime #AnimeReels #Apothecary` and a raw YouTube URL.
Flipping whisper_sync today would burn hashtags and a URL into the video as on-screen
word-by-word captions, on every niche flipped.

The accumulation mechanism is proven; its payload is wrong. **Recommend §4 stays blocked
until `spoken_text` carries narration rather than caption** — i.e. until the two open
questions below are answered. Reported, not fixed; no flags touched.

## Two findings this check surfaced (out of scope, filed not fixed)
1. **`narration_script` is 0 chars on all five niches** — including ai_creators, the
   narration canary, which also reports `narration_degraded=true`. The writer is not
   emitting a narration script; TTS falls back to caption text. This is the likely cause
   of the caption-as-speech behaviour above, and it relates directly to the NARR arc.
2. **ai_creators `degraded=true` while the other four are `false`** — the canary niche is
   the degraded one. Worth a look alongside (1).
