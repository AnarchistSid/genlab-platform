# CALIBRATION-04 — character presence: four instruments measured, none usable

ANIME-PEAK-07 §2 asks windows to score on character presence. Before scoring
anything, the scorer was measured against 50 hand-labelled frames (10 per
pilot clip, uniform across each clip, labelled from contact sheets and
written to disk before any detector ran).

Labels: YES 19 (character clearly on screen) · SMALL 7 · NO 19 (effect,
debris, landscape, blast) · CARD 5 (Crunchyroll outro).

| instrument | recall on YES | false-pos on NO/CARD |
|---|---|---|
| YuNet @1080, conf 0.15 | 79% | 79% |
| YuNet @1080, conf 0.25 | 68% | 53% |
| YuNet @1080, conf 0.50 | 32% | 11% |
| YuNet @300 (as first reported) | 53% | 21% |
| lbpcascade_animeface, swept | 32% | 0% |
| union (lbp OR YuNet) | 79% | 53% |
| ink-contour features, best d' | — | d'=0.63, ~65% accuracy |

No operating point separates a character from debris on this material.
The reason is structural: every instrument answers "is there a face", and in
action animation the character is routinely faceless in frame — backs, hands,
cropped heads, motion smear. Frame #20 is two hands filling the screen; #11 is
Big Mom's face at full width; both are missed.

## What this retracts

The presence percentages reported earlier this session — the shipped-reel
table (12/25/30/37/56%) and the source-in-window table that concluded "the
crop lost nothing, the windows are the cause" — were all produced by the
53%-recall instrument. The CONCLUSION survives (see below) but it now rests
on the contact sheets, not on those numbers. The numbers are withdrawn.

## What replaced it

A 7x7 contact sheet per clip at ~2 s stride, read directly. That is a real
measurement of the real quantity and it located the correct window in every
clip in one look. All five marked windows were wrong in the same direction:
they were placed on the marked IMPACT, and the impact is the frame with
nobody in it.

| fight | was | now | what is actually there |
|---|---|---|---|
| zoro_vs_king | 55–69 | 74–87 | was blast + rubble; now the finishing cut and King's last line |
| luffy_vs_kaido | 36–46 | 65–77 | was 10 s of effect streaks; now the final clash |
| gojo_vs_jogo | 72–80 | 63–77 | the Red technique — but see hardsub |
| deku_vs_overhaul | 56–64 | 66–78 | was a stylised impact panel; now the power-up to the clenched fist |
| tanjiro_vs_akaza | 31–38 | 72–96 | was the early exchange; now 24 s of continuous character |

## Two findings the sheets produced that nothing was looking for

1. **Every clip ends in a Crunchyroll outro card** — 8 s (Zoro, Luffy, Deku,
   Tanjiro) to 20 s (Gojo). Nothing excluded them; a window drifting late
   would have rendered the subscribe card into a reel.
2. **Three of five sources carry burned-in subtitles** — Spanish on the JJK
   upload, English on the MHA one. The channels are licensed, so the rights
   gate passes and the frames are still unusable. This is what §3's gate is
   for, and it now disqualifies gojo_vs_jogo outright (93%).

## fighters.py is a diagnostic, not a gate

It stays in tree for the action_box geometry, with the measurement in its
docstring. Nothing should gate on its presence signal.

## Fetching the cascade

`genlab-core/models/` is gitignored (binaries). To reproduce the lbp row:

```
curl -sSL -o genlab-core/models/lbpcascade_animeface.xml \
  https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml
```
