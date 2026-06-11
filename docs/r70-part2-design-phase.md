# R-70 Part 2 — Design Phase

**Status:** planning, not yet executed.
**Authored:** 2026-06-11 (session sweep #2).
**Scope:** extract `BaseVisualRenderStrategy` + `BaseScoringStrategy`
into `genlab_core/strategies/` and collapse the per-channel copies
in `SpliceReel/sr_strategies/`, `ClutchWire/cw_strategies/`, and
`FrameDrift/fd_strategies/`.

## Why this doc exists, not the extraction

The audit row asserted **"~750L copy-paste"** across the three
channels. Empirical measurement at session-#2 time gives a different
picture:

| Module | SR LOC | CW LOC | FD LOC | CW↔SR diff lines | % divergent |
|---|---|---|---|---|---|
| `visual_render.py` | 258 | 248 | 270 | 112 | **~55%** |
| `scoring.py` | 327 | 191 | 204 | 257 | **~70%** |

These aren't copy-paste duplications — they're **parallel-evolved
implementations** where each channel made different design choices
about what scoring or visual-rendering *means* for its domain.

A single-PR base-class extraction would either force a one-size-fits-all
contract that breaks per-channel intent, or create so many hook-method
overrides that the "base" is no simpler than the current per-channel
code. Neither outcome serves R-70's actual goal (collapse meaningful
duplication, not produce nominal subclass relationships).

This doc captures what a non-rushed multi-PR R-70 part 2 should look
like, sequenced so each PR is reviewable in isolation.

## Method inventory

### `visual_render.py` — method-by-method presence

| Method | SR | CW | FD | Notes |
|---|---|---|---|---|
| `__init__` | ✓ | ✓ | ✓ | log-prefix differs |
| `_ensure_config` | ✓ | ✓ | ✓ | SR reads `sources.yaml` + `visuals.yaml`; CW + FD read only `visuals.yaml` |
| `_get_whisper_config` | ✓ | ✓ | ✓ | apparently fully shared (8 lines each) |
| `prepare_whisper_words` | ✓ | ✓ | ✓ | log prefix differs; docstring differs (sports/movies/anime audio expectations) |
| `_compose_frame` | ✓ | ✓ | ✓ | 33 lines each — body comparison TODO before extraction |
| `_build_pexels_queries` | ✓ | ✓ | ✓ | **substantively divergent** — channel-specific query constants embedded in logic |
| `apply_overlay` | ✓ | ✓ | ✗ | SR + CW only; FD doesn't have this |
| `_render_story` | ✓ | ✓ | ✓ | small divergence (21–23 lines) |
| `execute` | ✓ | ✓ | ✓ | orchestration — likely highly shared |

### `scoring.py` — method-by-method presence

| Method | SR | CW | FD | Notes |
|---|---|---|---|---|
| `__init__` | ✓ | ✓ | ✓ | similar shape |
| `_ensure_config` | ✓ | ✓ | ✓ | similar shape |
| `_score_magnitude` | ✓ | ✓ | ✓ | candidate for extraction |
| `_score_novelty` | ✓ | ✓ | ✓ | candidate for extraction |
| `_score_timeliness` | ✓ | ✗ | ✓ | SR + FD only — CW uses `_score_recency` instead |
| `_score_engagement_potential` | ✓ | ✗ | ✓ | SR + FD only — CW uses `_score_community_signal` instead |
| `_score_recency` | ✗ | ✓ | ✗ | CW only (sports cares about "last 24h matters") |
| `_score_community_signal` | ✗ | ✓ | ✗ | CW only (sports cares about social buzz) |
| `score_item` | ✓ | ✓ | ✓ | orchestration |
| `execute` | ✓ | ✓ | ✓ | orchestration |
| `_is_wrong_niche` | ✓ | ✗ | ✗ | SR-specific |

The scoring inventory reveals the real shape: **SR and FD share a
scoring axis set; CW genuinely uses different axes.** This is a real
product difference (sports rewards "happened recently and people are
talking about it"; movies/anime reward "timely + likely to engage").

## Proposed sequencing

### PR 1 — `BaseVisualRenderStrategy` skeleton (no migration)

* Add `genlab-core/src/genlab_core/strategies/base_visual_render.py`.
* Inherits from existing `VisualRenderStrategy(ABC)` (interfaces.py).
* Concrete: `_get_whisper_config` (full implementation — apparently
  identical across channels, verify with `diff` first).
* Abstract / NotImplementedError: every other method.
* No channel migration in this PR.
* **Test:** `BaseVisualRenderStrategy` can be subclassed with stub
  implementations and `_get_whisper_config` works against a sample
  niche config.

**Risk:** very low. Doesn't change any channel behavior.

### PR 2 — Migrate SpliceReel as pilot

* `MovieVisualRenderStrategy` inherits from `BaseVisualRenderStrategy`.
* Delete the shared body of `_get_whisper_config` (now inherited).
* All SR tests must still pass. **No behavior change is the gate.**

**Risk:** medium. If `_get_whisper_config` was *almost* identical
across channels (small per-niche tweak hidden in the body), pilot
catches it.

### PR 3 — Migrate ClutchWire + FrameDrift

* Same as PR 2 but for the other two channels.
* If PR 2 surfaced any incompatibility, fold it into the base via a
  hook-method override before this PR.

### PR 4 — Extract `_compose_frame` body to base (if truly shared)

* **Precondition:** body-level diff between channels' `_compose_frame`
  shows zero substantive divergence (only log-prefix / niche-name
  differences extractable via class attribute).
* If precondition fails, skip — `_compose_frame` stays per-channel.

### PR 5 — Extract `BaseScoringStrategy` (narrower scope)

Scoring is 70% divergent and CW uses different axes. Only methods
clearly shared by all 3:

* `_score_magnitude` — extract if bodies are sufficiently similar
* `_score_novelty` — same
* The orchestration (`score_item`, `execute`) likely shared in
  shape but with channel-specific axis assembly

SR + FD share a second set (`_score_timeliness`,
`_score_engagement_potential`) — could become a
`BaseTimeBasedScoringStrategy(BaseScoringStrategy)` second-tier base
that CW does NOT inherit from.

**Risk:** high if pushed in one PR. Split into PR 5a (extract
magnitude+novelty into base), PR 5b (SR+FD second-tier base),
PR 5c (migrate CW with the narrower base only).

## What this design phase deliberately does NOT decide

* **Hook-method API for `_build_pexels_queries`.** The query
  construction is substantively divergent per channel. Whether to
  extract it as an abstract method, a class attribute, or leave
  it per-channel needs a body-level diff that PR 2 will surface.

* **`apply_overlay` in FD.** SR + CW have it; FD does not. Whether
  to add it as abstract (forcing FD to implement) or keep as a
  default-`pass` base method needs product input — does anime want
  text overlays in the future?

* **The `BaseScoringStrategy` second-tier shape.** Whether the
  SR/FD-shared axes deserve a separate class or just sit as
  optional-override methods on the main base depends on whether
  a future 6th channel would more likely share CW's shape or
  SR/FD's.

These decisions need code-level inspection that doesn't belong
in a design doc.

## Test strategy across the sequence

Every PR ends with **the migrated channels' full test suites
pass without modification**. A single regressed test = the
extraction missed a channel-specific intent. Roll back, document
the surfaced difference, retry with a hook-method override.

## Regression pin for this design doc

`genlab-core/tests/deploy/test_r70_part2_design_doc_present.py`
asserts:

1. This file exists at its canonical path.
2. The file references the measured divergence numbers (55% and
   70%) so a future contributor can re-measure and notice if the
   numbers have shifted (which would change the right extraction
   approach).

If anyone deletes this doc without doing R-70 part 2, the pin
surfaces the gap.
