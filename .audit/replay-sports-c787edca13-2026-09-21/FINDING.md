# Replay sports-c787edca13 — the before-picture for Part 29 §1

Job ran 20 Sep 21:31 → 23:05 (5661 s). Reported `ok: true`, invariance
`pass` (n=2, spread 0.005 s), votes 6/6 unanimous, finisher_presence 1.0,
96/96 mattes, `empty_count: 0`, matte area 20.5% → 20.0% with no flicker.

It selected a post-fight interview.

## The three questions

| question | answer |
|---|---|
| matte survived the cut at 21.2 s? | **not tested** — window was 12.65–15.01 s |
| ranking demoted the celebration? | **no — it selected it** |
| deliverable landed? | **no reel**; mattes only |

## Root cause

`craft_render.py:294` read:

    prof, fps = motion_profile(clip)

`motion_profile` returns `(per_frame_values, duration_s)`. Both fields are
floats, so binding a DURATION to `fps` was silent. The clip is 40.658 s
long against a 60 fps container, so the plan ran at "fps = 40.66" in three
places at once:

1. `level_shift_anchors` — audio anchors found at the wrong rate
2. `MatteRequest.fps` — the worker decoded at 40.66 (logged, not enforced)
3. `motion_at` — indexed a per-frame profile at 40.658 samples/s

(3) chose the window:

    motion_at(12.65) read prof[514]  -> the motion at 8.58 s
    motion_at(19.90) read prof[809]  -> the motion at 13.50 s

The velocity ranking compared motion from timestamps it was not reporting.

## Secondary finding

The source is already a composited vertical reel — two stacked 16:9 panels
(post-fight interview above, opponent beside Dana White below). SAM2 was
tracking a subject inside the bottom panel of someone else's edit, so
ACTION's full-bleed subject treatment could not have applied to this source
regardless. This is Part 29 §2's `composited_source`.

## Why this is not in .deliverables/

It is evidence of a defect, not an artifact anyone should reuse. The
manifest schema's escapes (`unrecoverable`, `superseded_by`,
`reproducible_via`) all describe builds; none describe this, and the
reproducibility pin was right to reject it.
