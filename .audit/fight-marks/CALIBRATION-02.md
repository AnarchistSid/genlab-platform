# Blind calibration #2 — detector v2, dev/test split. Bar missed.

ANIME-PEAK-03. Order as executed, each step verifiable in git:

1. Test-set fights sourced through the licensed gate.
2. Blind marks written from frame grids and committed — `5b97e517` — while
   `fight_moment_v2.py` did not exist.
3. Detector built and tuned on the DEV set only, then frozen — `1df93c83`.
4. Test set run ONCE. No tuning after.

## Results at ±0.3 s

| set | fights | marks | anchor | recall | precision |
|---|---|---|---|---|---|
| dev (contaminated) | 3 | 9 | audio | 0.22 | 0.04 |
| dev | 3 | 9 | any | **0.56** | 0.07 |
| **test (blind)** | **2** | **4** | audio | **0.75** | **0.06** |
| test | 2 | 4 | any | 0.75 | 0.06 |

Per fight on the test set: Deku vs Overhaul 2/2, nearest approach 0.02 s;
Tanjiro vs Akaza 1/2, nearest 0.12 s, missed the 35.50 s spark clash. Every
test hit was confirmed by the MOTION signal, not by the spike or the field.

## The bar is recall ≥ 0.8 across ≥ 5 fights. This is 0.75 across 2.

Missed on both the value and the count. §4 does not proceed.

## Precision 0.06 is the more serious number

25 picks against 2 marks — **12.5× more picks than events**. Firing that
often buys recall cheaply, and recall is the number that looks good. The same
check, run as an app built earlier this session, flags it as degenerate:

    recall 2/2, precision 2/25 = 0.08, picks:marks 12.5x -> DEGENERATE

So the honest reading of "test recall 0.75" is that a detector firing every
0.35 s will land near most things. The recall figure is not yet evidence the
detector works.

## Two findings, both measured, neither tuned away

**Audio is not universal.** §2 anchors on the hit's sound. At the nine dev
marks the audio step measured 4.16 / 5.98 / 7.13 (Zoro), 6.73 / 7.62 / 6.03
(Luffy), 1.67 / 3.49 / **−2.17** (Gojo). Six clear 4 dB. Gojo's do not, and
one is negative — the mix gets *quieter* as Hollow Purple lands. The audio
bracket is the whole 0.22-vs-0.56 dev gap.

**"The impact frame" is ambiguous for a sustained flash.** Gojo stayed 0/3
under v2 for a reason different from v1's. The sustained-field signal does
fire on that clip, but only on the field's rising EDGE, while the marks sit
at the visible burst inside it. For a flash lasting half a second, onset and
peak are more than 0.3 s apart. That is a definition mismatch between marker
and detector, not a threshold.

## What the next iteration needs, in order

1. **Fix precision before chasing recall.** A detector at 12.5× picks-to-marks
   cannot be evaluated. Rank and cut to ~2× before any recall number means
   anything.
2. **Define the impact frame for a sustained flash** — onset or peak — and
   re-mark the dev set to that definition. Until it is defined, Gojo cannot
   be scored.
3. **Three more test fights.** The bar needs 5. Sukuna vs Mahoraga and Mob vs
   Toichiro are sourced and licensed but are 24-minute full episodes; marking
   one is not a session-sized task and needs either a coarse pre-localisation
   pass or an operator timestamp.
4. Test marks stay unused for tuning. Deku and Tanjiro are now spent as a
   *measurement*; they are still clean for tuning only if nothing is fitted
   to them, which is why nothing was.
