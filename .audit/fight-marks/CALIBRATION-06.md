# CALIBRATION-06 — PEAK-09: the bed, measured before it is trusted

## The meter had to be reconciled before the gate meant anything

The packet reports the v2 beds at "sub-bass 15–16% of spectrum" and gates
candidates at "≥ 25%". My first implementation measured a **power** ratio and
read **41–50%** on the same files — so every candidate would have cleared 25%
and the gate would have been unfalsifiable.

| reel | power < 120 Hz | magnitude < 120 Hz | packet |
|---|---|---|---|
| zoro_vs_king | 49% | 14.1% | 15–16% |
| luffy_vs_kaido | 41% | 14.1% | 15–16% |
| deku_vs_overhaul | 50% | 17.4% | 15–16% |
| tanjiro_vs_akaza | 42% | 14.7% | 15–16% |

Spectral **magnitude** share reproduces the packet's numbers, so that is what
`sub_share` implements and what `MIN_SUB_SHARE = 0.25` is stated against.
`sub_power_share` keeps the other reading under its own name. The two are not
interchangeable and the difference is 3×.

A related note on tempo: `detect_grid` searches 120–180 BPM, so it cannot
report the packet's 99 BPM reading. All four v2 reels measure 150 against the
packet's 152.

## A drop is a level shift, not a hit

`find_drop` compares the median of the following 2 s to the median of the
preceding 2 s of sub-weighted energy. The loudest moment and the biggest
single-frame jump are both **hits**, and a hit must not read as a drop —
pinned in `test_a_single_hit_is_not_a_drop`.

## The §3 gates fail every v2 reel, on exactly the named faults

| reel | sub | loudest second | impact | verdict |
|---|---|---|---|---|
| zoro_vs_king | 14% | 1.30 s | 9.20 s | fails sub, loudest |
| luffy_vs_kaido | 14% | 2.10 s | 13.80 s | fails sub, loudest |
| deku_vs_overhaul | 17% | 4.40 s | 13.50 s | fails sub, loudest |
| tanjiro_vs_akaza | 15% | 7.20 s | 18.20 s | fails sub, loudest |

All four pass LUFS and true peak. The loudest second is the hook slam in
every case — the title louder than the punch.

## The first register prompts failed the gate 12 times out of 12

Round one measured 10–25% sub share across 12 candidates and cleared 25%
**zero** times. That reading is ambiguous on its own: either the beds are
wrong or the bar is unreachable for `elevenlabs/music`. Three probe prompts
settled it:

| probe | sub | drop | click |
|---|---|---|---|
| sub_forward | **48%** | 9.15 s (+18.6 dB) | 0.001 |
| sub_only | **70%** | none (+0.2 dB) | 0.012 |
| slowed | **30%** | 14.85 s (+45.1 dB) | 0.087 |

The bar is reachable, so the fix was prompt strength, not a lower gate —
which was the tempting move and would have locked in beds carrying a quarter
of the genre's low end. Two limits came with it, both worth keeping:

* asking for sub **alone** (70%) lost the drop completely;
* the strongest sub prompt measured a click flatness of 0.001 — **sub and
  cowbell compete for the same mix**.

## Two gates that are not what they claim

* **`click_present` ships unenforced.** On the v2 orchestral beds the
  statistic spans 0.011–0.113, so it does not separate a cowbell from cymbals
  and brass. It needs a positive control, and the probe above shows the
  strongest-sub candidate scores *lowest* — the statistic may be measuring
  treble presence rather than percussion.
* **The 6–10 s drop window assumes a reel this lane does not produce.**
  PEAK-08's character-scored windows put the impacts at 9.2 / 13.8 / 13.5 /
  18.2 s. Only Zoro's falls in the packet's band. The drop is therefore aimed
  at each reel's own wind-up and the residual absorbed by `align_bed`, which
  either trims into the build or leaves a silent opening. Both are costs and
  both are reported per reel.

## §4 and §5 are not executed

`music_licensed` is not added and nothing is subscribed: §4 is a purchase.
The evidence for the decision is above — generated beds reach 48% sub with a
real drop, so the library's value would be **trend-matching**, not audio
quality.

§5 requires posting natively from the in-app composer, which is publishing on
the operator's accounts. The protocol is written below; it is not run.

### §5 protocol, ready to execute on approval
1. Two approved peak reels from the same show, posted the same day.
2. **A** — native: in-app composer, trending phonk sound selected from the
   Reels audio page, same caption and cover.
3. **B** — pipeline: generated bed, posted through the publisher.
4. Record at 7 days: reach, plays, average watch time, saves, shares.
5. Decision rule from the packet: native wins by > 2× reach → the biggest
   evergreen reels get a manual-post path with a checklist; everything else
   stays automated.
6. Confound to control: post order and time of day. Alternate across the
   two-reel pair, or the difference measures the slot, not the sound.

## Normalise by measurement, not by `loudnorm`

The §3 LUFS gate failed three of four reels at −12.1 to −12.7 against a
−14 ± 1 target, and the true-peak gate failed three at −0.9 to −1.1 against
≤ −1.0. Both had causes worth keeping.

**`loudnorm` moved the files the wrong way.** Measured on two real premixes:

| stage | zoro | tanjiro |
|---|---|---|
| premix | −13.09 LUFS | −14.96 LUFS |
| after two-pass `loudnorm` (`linear=true`) | −12.68 | −14.19 |

Both got **louder**, in the direction away from the −14 target. Single-pass
at `TP=−1.5` undershot instead, to −17.07. A static gain computed from the
premix's own integrated loudness, followed by a limiter, landed on **−14.00
LUFS at TP −1.68** in one step, and can be verified by re-measuring:

```
gain = target_lufs − measured_lufs(premix)
volume={gain}dB, alimiter=limit=-1.5dB
```

**Aiming at a ceiling cannot clear it.** The kit targets true peak at exactly
−1.0, so measurement lands either side and a `≤ −1.0` gate failed by a tenth
of a dB. The limiter now aims at −1.5.

## Aligning the drop does not make the impact the loudest second

This corrected an assumption in the packet's own design, and the measurement
is the reason the first v3 pass failed:

| | bed's loudest | show's loudest | impact |
|---|---|---|---|
| luffy_vs_kaido | 16.90 s | — | 13.37 s |
| deku_vs_overhaul | 15.30 s | 7.40 s (+5.1 dB over its level at the impact) | 13.78 s |

A drop is where the level **steps up**; the section's own peak arrives 1.1–3.3 s
later, and the show's own audio peaks *before* the impact. So the hit owns the
loudest second outright at 0 dB, with the bed at −7 dB and the show at −11 dB,
both sidechain-ducked **by the hit**. After that rebalance the loudest second
landed +0.91 to +0.94 s from the impact on all four reels — inside the 1 s
tolerance, on every one.

## One bed came from the weaker prompt

luffy_vs_kaido passed in round 2 and was therefore never regenerated with the
round-3 prompt, which is why its sub share sat at 19% in the mix while the
others reached 26–42%. Regenerated on the same prompt as the rest, it moved
to 30% at source. **A candidate that passes early escapes the next
improvement** — worth checking for whenever a gate is re-tuned mid-run.
