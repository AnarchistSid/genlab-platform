# CALIBRATION-12 — PEAK-17: alignment on the file that ships

## §1 The verification had to stop using the renderer's own number

My first pass compared the audio drop against the renderer's LOGGED
`impact_rel` — the very value under test. It reported v9 as a PASS at 0.1
frames. Measuring the visual impact independently (the brightest luma step
inside the hit segment) gives the real picture:

| reel | visual impact | audio drop | offset |
|---|---|---|---|
| v9 (the fixture) | 13.375 s | 12.625 s | **−18.0 frames** |
| v10, computed impact | 13.791 s | 13.417 s | −9.0 frames |
| **v10, measured impact** | 13.791 s | 13.750 s | **−1.0 frames — PASS** |

### Why v9 was 18 frames out

The impact sits at **62.5%** of the hit's source span. PEAK-15 trimmed that
clip to **60%** to drop a tail the framing gate could not read. The trim cut
the impact out of the reel entirely, and the bed was then aligned to an
arithmetic position where nothing happened. The clip now keeps 82.5% —
the impact plus 0.2 of the span — and the renderer raises if the impact's
fraction ever exceeds what the clip covers.

### And why computing it is still not enough

Even with the trim corrected, the computed time was 8.3 frames early. Every
arithmetic route carries an assumption: that the hand mark sits exactly on
the flash, that the clip covers what it is thought to cover, that no ramp
moved it. The impact's output time is now **measured on the rendered body**
and the bed is fitted to that.

## §2 The model read the tone differently, and the register could not be served

The VLM read **triumph**, not the grief/awe the packet expected: *"Tanjiro
dodges close-range strikes and lands a decisive Water Breathing cut that
severs Akaza's arm."* That is a defensible reading of these shots.

Triumph maps to `brazilian_phonk`. Neither candidate could be used:

| candidate | tempo | sub | drop | rise | verdict |
|---|---|---|---|---|---|
| brazilian_phonk 0 | 148 | 35% | 6.45 s | 0.15 s | shape OK, **7.35 s from the wind-up** |
| brazilian_phonk 1 | 148 | 34% | 6.35 s | 0.10 s | shape OK, **7.45 s out** |
| epic_hybrid 0 | 150 | 34% | 13.05 s | 0.00 s | alignable (0.75 s), **shape MISS** |
| epic_hybrid 1 | 150 | 28% | 19.95 s | 0.10 s | both miss |

**No candidate clears both gates.** The two are in tension: shape follows
the register, position follows the generator's luck. Secondary drops were
checked — the lever that unblocked PEAK-09 — and only epic_hybrid_0 has one
in range.

The shipped bed is `epic_hybrid` with a **step**, not the swell its register
requires. Across four candidates in two registers the generator produced only
steps (0.00–0.15 s rise); the swell the hybrid register is defined by was
never generated. That is a capability limit, not a tuning miss.

**"Picked by ear against the reference edit's track" was not done.** There is
no reference file and I cannot listen. The pick is by the measurable gates
alone, and that should not be read as an aesthetic judgement.
