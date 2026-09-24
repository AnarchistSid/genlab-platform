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
