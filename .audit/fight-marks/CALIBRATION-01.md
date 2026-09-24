# Blind calibration #1 — Gojo vs Jogo. Recall 0.0.

ANIME-PEAK-02 §2. The first calibration run against marks that were made
before the selector ran.

## Protocol, as executed

1. Frame grid at 5.83 s across the clip, then 0.25 s grids over the two
   regions that looked like impacts. `find_beats` was NOT run on this clip.
2. Marks written and hashed — `gojo_vs_jogo.json`, sha `e69fe880131807fd`,
   impacts `[75.50, 77.25, 112.00]`.
3. Committed as `9f3f0b95`.
4. **Then** the selector ran. `calibrate_blind` compares mtimes and refuses
   the reverse order.

## Result

    recall     0.00      (0 of 3 marks had a pick within 0.30 s)
    precision  0.00      (0 of 10 picks landed near a mark)
    missed     75.50, 77.25, 112.00
    picks      36.87, 38.53, 38.57, 54.13, 54.17, 54.20,
               80.57, 87.90, 113.20, 115.13

Nearest approach: a pick at 113.20 against the 112.00 mark — 1.20 s away,
four times the tolerance.

The previous, circular calibration reported 100% at a 0.002 s median.

## Root cause, measured

The detector scores a luma SPIKE against a local median over ±0.5 s. At two
of the three marks the jump is exactly **0.0**:

    t         luma   baseline   jump
    75.50     44.1      54.8   -10.7    Red fires
    77.25    154.1     154.1     0.0    second burst
    112.00   104.9     104.9     0.0    Hollow Purple

The frames are bright — 154 and 105 — but so is the whole half-second around
them, so the flash becomes its own baseline and the spike vanishes. At 75.50
the marked frame is actually DARKER than its neighbours.

Counting how long the brightness lasts:

    at  75.50s   7 of 32 surrounding frames exceed the +12 threshold
    at 112.00s   3 of 32

So the detector encodes **one studio's impact convention**. One Piece marks a
hit with a 1-3 frame flash — verified visually on Zoro and Luffy, where the
model works exactly. Jujutsu Kaisen marks Hollow Purple with a sustained
bright FIELD lasting the better part of a second, and the detector explicitly
rejects sustained rises as "a bright shot, not an impact". That rejection is
correct for a cut and wrong for this.

## What was deliberately NOT done

The thresholds were not adjusted and the run was not repeated. Tuning against
these marks would make them non-blind, which is the exact failure this
protocol exists to prevent — and it would fit the detector to one clip.

## What §3 needs before it is trusted

The target is recall ≥ 0.8 on ≥ 5 fights. Current: 0.0 on 1. The skin does
not proceed. Next step is a baseline window long enough to contain a
sustained flash (or an absolute-brightness term alongside the relative one),
then **fresh blind marks on fights not used to diagnose this** — Zoro and
Luffy are already contaminated by visual inspection, so the re-test needs
Denji, Mob, and three more that clear the source gate.
