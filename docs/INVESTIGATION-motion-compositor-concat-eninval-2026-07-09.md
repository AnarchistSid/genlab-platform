# Motion compositor concat exit=234 (-22 EINVAL) — scoped investigation

**Task**: #632 (filed by this doc, 2026-07-09 late evening)

**Status**: Investigation in progress; fix deferred pending live-capture
of a failing render's intermediate outputs.

## The symptom

Motion_compositor's FFmpeg concat fails ~50% of renders with:

```
[aost#0:1/aac] Task finished with error code: -22 (Invalid argument)
[vost#0:0/libx264] Task finished with error code: -22 (Invalid argument)
[out#0/mp4] Nothing was written into output file, because at least one
    of its streams received no packets.
frame=    0 fps=0.0 q=0.0 Lsize=       0KiB time=N/A bitrate=N/A
Conversion failed!
```

Reference: production journal 2026-07-09 at 12:47:26 (movies), 12:49:11
(movies), 13:32:48 (movies), 12:10:33 (gaming), 12:18:20 (gaming +
success).

## What we've ruled out

### 1. Stream property mismatch — NOT the cause

Probed both the intro asset and a failing render's base composite:

| Stream | Intro (`pattern_break_intro.mp4`) | Failing source (`grand_theft_auto_v_vertical.mp4`) |
|---|---|---|
| Video codec | h264 | h264 |
| Pix fmt | yuv420p | yuv420p |
| Frame rate | 30/1 | 30/1 |
| Audio codec | aac | aac |
| Sample rate | 48000 | 48000 |
| Channels | 2 (stereo) | 2 (stereo) |

**Identical properties.** No mismatch that `aformat=sample_rates=48000:
channel_layouts=stereo` would need to catch.

### 2. Filter graph correctness — NOT the cause

Ran the exact filter graph the code produces (`build_concat_filtergraph`)
against the failing base composite + intro + outro on prod. Result:

```
exit=0
9573891 bytes output
frame=931 fps=18 duration=00:00:31.04
```

**Concat SUCCEEDS statically.** So the code's filter graph is fine.

### 3. Asset resolution — NOT the cause

All template names in the 4 activated niches' `visuals.yaml` map to
existing physical asset files. `_resolve_asset_path` returns real
paths, not None.

## What we haven't ruled out (deferred)

### Hypothesis A: intermediate stage produces bad intermediate file

The failing renders show `stages_applied=['highlight_moment', 'music_mood',
'intro_animation', 'outro_cta']` with output 10.03s < window_seconds=12s.
That's suspicious — intro (~2.5s) + highlight (12s) + outro (~2.5s) should
be ~17s, not 10s.

If `music_mood` (audio replace) or `pan_zoom` (video filter) sometimes
produces a file with:
- 0 audio packets in some frames
- Timestamp gaps
- Truncated stream
- Missing audio stream entirely (if replacement audio failed)

… then the NEXT concat step sees a "valid file" (ffprobe shows streams)
but the actual packet delivery fails at concat time.

### Hypothesis B: race condition / partial write

Motion_compositor's input file is being written by an earlier stage that
returns success before the file is fully flushed. Concat starts reading
too early.

Weak evidence for this: static reproduction always succeeds, live
production ~50% fails.

### Hypothesis C: music_mood replaces audio with a broken track

`AudioReplacer` from `#510` mixes source audio with music bed via
FFmpeg. If the mix produces a 0-duration or corrupted audio stream,
the subsequent concat sees an incompatible input.

## Confirmatory experiment for tomorrow

With #631's log elevation deployed, tomorrow's 06:30 UTC fire will
produce ERROR logs naming the specific failing (niche, intro_arm,
outro_arm) tuples. When one fires, immediately:

1. Copy the failing render's `/tmp/genlab_transform_*/` intermediate
   dir before it's cleaned up
2. `ffprobe -show_frames` on each stage's intermediate output
3. Look for zero-packet ranges, audio dropouts, timestamp gaps
4. Match against which stage produced the degradation

That gives a repro fixture for a proper unit test in the next PR.

## Ship rationale for tonight's stopping point

Tonight's investigation ruled out the 3 most plausible static causes
(property mismatch, filter graph, asset resolution) but couldn't
reproduce the failure locally. The bug is content-dependent and
requires live-capture to characterize. Fixing without knowing WHICH
stage degrades the file would be blind patching — likely creating a
follow-up bug like PR #742's "predictive comment" pattern.

**PR #755 (#631) shipped tonight** provides the log signal needed
for tomorrow's diagnostic. Without it, we'd have kept debugging
blind.

## Related

- `[[transformation-attribution-min-duration-guard-trap-2026-07-09]]` —
  the parent bug (symptom-level fix via #630)
- `[[class-of-bug-alerts-must-reflect-current-state-not-historical-signal]]`
  — sibling pattern; here the "current state" is a per-render
  intermediate file whose degradation only shows up mid-pipeline
