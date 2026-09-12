# CAP-01 §1 — Voice listen sheet (ElevenLabs)
**2026-09-11 · 5 niches × 3 candidates · $0.50 total · normalized to −14 LUFS (free)**

HUMAN-PENDING. No voice is chosen until Aditya's listen verdict. Nothing below is a recommendation —
a model cannot judge a voice, and the ranking column is deliberately empty.

Files: `~/cap-01/retention/voices_el_norm/` (normalized, play these) · raw in `voices_el/`

## How to listen
```bash
cd ~/cap-01/retention/voices_el_norm
for f in ai_creators_*; do echo "$f"; afplay "$f"; done   # one niche at a time
```

## Decision needed per niche: pick ONE candidate, or reject all three and I redesign.

Also an explicit choice you are making, not me: **perceived gender per channel.**
I specified it in the design prompts to get usable candidates — ai_creators unspecified,
gaming male, anime female, movies male, sports male. Redesign is $0.10 each if you want
a different mix. Across five channels this is brand identity, so it is worth a deliberate call.

### ai_creators — AI calm desk · 150 WPM · script 73w ≈ 29.2s

> Anthropic just shipped a change that most people scrolled straight past. Claude can now run for hours on a single task without losing the thread. That…

| cand | voice_id | dur | out LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `5Uy8rKNKEL66HNItX6fa` | 28.87s | -14.89 | yes |  |
| 2 | `t90LXR7U3OLLhoF1GnrE` | 27.61s | -13.98 | yes |  |
| 3 | `WmzAAOfNI8VJqC8CLrS9` | 26.28s | -15.55 | **NO — re-normalize if chosen** |  |

### gaming — energetic friend · 170 WPM · script 76w ≈ 26.8s

> Okay you have to watch this one twice, because the first time you will not believe what you just saw. One player, no shield, three seconds on the cloc…

| cand | voice_id | dur | out LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `Ki6E1Wj0tO1P1LoXBBmC` | 23.02s | -14.21 | yes |  |
| 2 | `orN6g3okSWZyxonZVpx7` | 27.59s | -14.29 | yes |  |
| 3 | `vkHASo1NhGhUIHzsYKpG` | 25.5s | -15.78 | **NO — re-normalize if chosen** |  |

### anime — warm fan-expert · 160 WPM · script 76w ≈ 28.5s

> This scene broke the entire fandom for a week, and it is only forty seconds long. No dialogue. No music for the first half. Just a character finally p…

| cand | voice_id | dur | out LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `6XM2LyKvVAXkCZEjjedN` | 31.01s | -14.29 | yes |  |
| 2 | `rOWnMSiiBimK5D2IulS1` | 31.77s | -14.02 | yes |  |
| 3 | `z9JXrhW9p5CEcFe3fO39` | 29.99s | -14.75 | yes |  |

### movies — cinematic narrator · 140 WPM · script 72w ≈ 30.9s

> Every few years a trailer arrives that refuses to explain itself. No plot. No names. Ninety seconds of images that do not add up, and one sound you ca…

| cand | voice_id | dur | out LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `4kq2DnZk8NEmc5keCoJI` | 31.77s | -14.75 | yes |  |
| 2 | `sE9HGPhPvuKtRxm45wvk` | 31.01s | -13.99 | yes |  |
| 3 | `b4cyxGj0m58HmArP40gw` | 28.01s | -14.88 | yes |  |

### sports — punchy pundit · 175 WPM · script 71w ≈ 24.3s

> Down by two. Eleven seconds left. And the coach calls the one play everybody in the building already knows is coming. Watch the defense set up for it.…

| cand | voice_id | dur | out LUFS | on target | verdict (yours) |
|---|---|---|---|---|---|
| 1 | `Q92B53vEsYCI4kjHfm2P` | 23.72s | -14.37 | yes |  |
| 2 | `3QrEhVOdXPZ2js3bPDZ4` | 24.27s | -14.19 | yes |  |
| 3 | `V1HQcMn3HwbULjwhPf6X` | 23.96s | -14.46 | yes |  |

## On the two off-target candidates

`ai_creators_cand3` (−15.55) and `gaming_cand3` (−15.78) undershot the −14 target by more
than 1 LU. Both are candidate 3; both had the widest input deviation. This is a property of
those two takes, not of the normalizer — the other thirteen landed within 0.9 LU. If you pick
either one, it gets a second normalize pass before it becomes the profile. Not a blocker.

## What happens after your verdict

Winner's provider + voice_id goes into the niche YAML as `voice_profile`, and the TTS cascade
head moves from the single shared `infsh_inworld` voice to the per-niche profile. Until then
nothing changes in the pipeline — these are eval_only assets.
