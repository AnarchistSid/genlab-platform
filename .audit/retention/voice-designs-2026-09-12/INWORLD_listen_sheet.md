# VOICE-02 — Inworld listen sheet
**2026-09-12 · 5 niches × 3 candidates · all 15 published AND round-tripped**

HUMAN-PENDING. No voice is chosen until Aditya's verdict. The ranking column is
deliberately empty — a model cannot judge a voice.

## Play them
```bash
cd ~/GenLab/.audit/retention/voice-designs-2026-09-12/inworld/norm
for f in ai_creators_*; do echo "$f"; afplay "$f"; done   # one niche at a time
```

## Read this before listening — one bias to discount

**7 of 15 normalised to >1 LU below the −14 target: every movies and sports
candidate, plus anime cand2.** They will sound *quieter*. That is a property of
the normalisation, not of the voice — `loudnorm` cannot lift a wide-dynamic-range
source to −14 without breaching the −1 dBTP ceiling, so it lands short (the same
crest-factor mechanism as FIX-LOUD). Judge timbre, pace and register, and
discount loudness; it is fixable independently and does not distinguish the
candidates within a niche.

## Also your call, not mine

**Perceived gender per niche.** I specified it in the prompts to get usable
candidates. Changing it is ~$0.10 per redesign. Across five channels this is
brand identity, so it is worth a deliberate decision rather than an inherited one.

## Decision per niche: pick ONE candidate, or say "none" and I redesign.

### ai_creators — AI calm desk · 150-160 WPM · prompt gender: unspecified in prompt

> Anthropic just shipped a change that most people scrolled straight past. Claude can now run for hours on a single task without los…

| cand | file | dur | LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `ai_creators_cand1_9eba7277.m4a` | 12.1s | -14.45 | yes |  |
| 2 | `ai_creators_cand2_68744142.m4a` | 11.9s | -14.42 | yes |  |
| 3 | `ai_creators_cand3_7fb30b9e.m4a` | 11.8s | -14.13 | yes |  |

### gaming — energetic friend · 175-190 WPM · prompt gender: male

> Okay you have to watch this one twice, because the first time you will not believe what you just saw. One player, no shield, three…

| cand | file | dur | LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `gaming_cand1_c2060e9d.m4a` | 9.2s | -14.32 | yes |  |
| 2 | `gaming_cand2_2c45a08e.m4a` | 9.1s | -14.53 | yes |  |
| 3 | `gaming_cand3_e9a705a9.m4a` | 9.3s | -14.7 | yes |  |

### anime — warm fan-expert · 165-180 WPM · prompt gender: female

> This scene broke the entire fandom for a week, and it is only forty seconds long. No dialogue. No music for the first half. Just a…

| cand | file | dur | LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `anime_cand1_99f514bb.m4a` | 11.4s | -14.43 | yes |  |
| 2 | `anime_cand2_6b397f5c.m4a` | 11.5s | -15.16 | **no — quieter, discount it** |  |
| 3 | `anime_cand3_4d8cfb63.m4a` | 11.9s | -14.59 | yes |  |

### movies — cinematic narrator · 145-160 WPM · prompt gender: male

> Every few years a trailer arrives that refuses to explain itself. No plot. No names. Ninety seconds of images that do not add up, …

| cand | file | dur | LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `movies_cand1_00f25678.m4a` | 13.9s | -15.48 | **no — quieter, discount it** |  |
| 2 | `movies_cand2_40b0af3e.m4a` | 12.6s | -16.25 | **no — quieter, discount it** |  |
| 3 | `movies_cand3_3db3cd7b.m4a` | 13.5s | -15.54 | **no — quieter, discount it** |  |

### sports — punchy pundit · 150-185 WPM · prompt gender: male

> Down by two. Eleven seconds left. And the coach calls the one play everybody in the building already knows is coming. Watch the de…

| cand | file | dur | LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `sports_cand1_6dda37a6.m4a` | 9.8s | -15.32 | **no — quieter, discount it** |  |
| 2 | `sports_cand2_42b74aae.m4a` | 10.2s | -15.32 | **no — quieter, discount it** |  |
| 3 | `sports_cand3_d1bd94d9.m4a` | 10.5s | -15.35 | **no — quieter, discount it** |  |

## What happens after your verdict

The chosen `voice_id` goes into `voice_profile` in the niche YAML and the TTS
cascade head moves from the default Inworld voice to the per-niche one. Until
then nothing changes in production — these are eval assets. Every candidate is
already published and verified against the production synthesis path, so applying
a pick is a config change only, with no further generation.

Loudness is corrected at render time by `ValidateVideos`, so a quieter sample
here does not imply a quieter reel.
