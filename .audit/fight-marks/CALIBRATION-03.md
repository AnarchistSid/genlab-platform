# Window gate — recall 0.60, precision 0.30 across 5 fights. FAIL.

ANIME-PEAK-05 §4. Order held: window marks hashed and committed (`16408e61`)
while `coarse_windows` had never run on any of these clips; the coarse pass
then ran once, ≤2 windows per fight, and was not tuned afterwards.

## Per fight

| fight | studio | mark | proposed | best overlap | verdict |
|---|---|---|---|---|---|
| zoro_vs_king | toei | 55–69 | [65–75] [57–67] | 0.71 | HIT |
| luffy_vs_kaido | toei | 36–46 | [34–44] [19–29] | 0.80 | HIT |
| gojo_vs_jogo | mappa | 72–80 | [17–27] [57–67] | **0.00** | miss |
| deku_vs_overhaul | bones | 56–64 | [66–76] [75–85] | **0.00** | miss |
| tanjiro_vs_akaza | ufotable | 31–38 | [27–37] [19–29] | 0.86 | HIT |

    window recall  3/5  = 0.60   (bar 0.80)
    precision      3/10 = 0.30   (bar 0.50)

## Why, measured

Onsets counted inside the marked window versus inside the top proposal:

| fight | onsets in mark | in top pick | TOTAL in clip | verdict |
|---|---|---|---|---|
| zoro | 5 | 6 | **19** | HIT |
| luffy | 4 | 5 | **17** | HIT |
| tanjiro | 12 | 19 | 128 | HIT |
| gojo | 9 | 21 | **148** | miss |
| deku | 5 | 12 | **72** | miss |

**Onset density only discriminates when onsets are sparse.** The two clips
where it worked cleanly have 17 and 19 onsets across ~100 s. The two that
failed have 72 and 148 — Gojo's is roughly one onset per second across the
whole clip, at which rate "where are the onsets densest" carries no
information about where the fight is. Tanjiro hit at 128 onsets, which on
this sample reads as luck rather than signal.

The root cause is upstream of the ranking: `_audio_onsets` fires on any level
step, so music swells and dubbed dialogue score as hits. Gojo's clip is a
Spanish dub over a continuous score — almost every bar produces an onset.

## What would change it, and what was deliberately not done

PEAK-04 §2 named a second audio feature that was never implemented: the
**2–8 kHz SFX burst**. A hit's sound is broadband and transient; a music
swell and a voice are neither. Separating those is the missing discrimination,
and it is a feature, not a threshold.

Nothing was tuned against these five. They are now spent as a MEASUREMENT and
stay clean for tuning only because nothing was fitted to them. The next
iteration develops the SFX-band feature on the dev clips (zoro, luffy, gojo)
and needs fresh marked fights to re-test.

## Where this leaves the pilot

§5 does not proceed — the skin is gated on this. But note what the failure
actually blocks: the window locator is what picks the reel's 6–14 s. Two of
five reels would be cut around the wrong moment, and there is no cheap way to
tell which two without a human looking.

An operator-supplied timestamp per fight (one number, roughly where the fight
peaks) would bypass the locator entirely and unblock §5 for all five, with the
locator continuing to be developed against the marks in the background. That
is the one input that converts this from blocked to shipping.
