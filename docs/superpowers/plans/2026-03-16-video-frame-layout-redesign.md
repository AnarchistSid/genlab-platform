# Video Frame Layout Redesign — Match Evolving AI Reference

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the video frame layout so all 5 channels match the Evolving AI reference: larger black bars, logo+hook positioned at ~25% from top, video centered in the middle 50%, clean bottom bar.

**Architecture:** Update the locked constants in `frame_compositor.py` (the single source of truth for frame layout), update all 5 `visuals.yaml` configs to match, and ensure `render_text_overlays.py` defers to `frame_compositor.py` instead of burning its own overlays at y=50.

**Tech Stack:** FFmpeg drawtext/overlay filters, Python, YAML config

**Reference screenshots:** `/Users/anarchistsid/Desktop/Screenshot 2026-03-16 at 7.43.19 PM.png` through `7.45.22 PM.png` — Evolving AI's layout on Instagram reels.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `genlab-core/src/genlab_core/media/frame_compositor.py` | Modify | Update locked layout constants for all 3 cases |
| `genlab-core/src/genlab_core/pipeline/stages/render_text_overlays.py` | Modify | Skip overlay if frame_compositor already burned hook |
| `ClutchWire/config/visuals.yaml` | Modify | Update frame_layout section |
| `SpliceReel/config/visuals.yaml` | Modify | Update frame_layout section |
| `FrameDrift/config/visuals.yaml` | Modify | Update frame_layout section |
| `genlab-core/tests/media/test_frame_compositor.py` | Modify | Update expected values |

---

## New Layout Constants (derived from Evolving AI reference)

```
Canvas: 1080 x 1920 (unchanged)

LANDSCAPE (source w/h >= 1.33):
  y=0–310:    Solid black (310px top bar)
  y=310–370:  Logo(60px circle, x=45) + channel name(24px) + handle(17px)
  y=380–460:  Hook text (font 44px bold white, max 2 lines, left-aligned x=45)
  y=460–466:  Accent line (6px, channel accent color)
  y=466–1074: VIDEO 1080x608 — ZERO overlays on video
  y=1074–1920: Solid black (846px bottom bar)

PORTRAIT (source w/h <= 0.75):
  y=0–310:    Solid black top bar
  y=310–370:  Logo(60px) + channel name + handle  [same as landscape]
  y=380–460:  Hook text (same as landscape)
  y=460–466:  Accent line
  y=466–1466: VIDEO scaled to fit 1080x1000 (centered, black letterbox if needed)
  y=1466–1920: Solid black (454px bottom bar)
  NOTE: Portrait video NO LONGER fills canvas. Always sandwiched.

SQUARE (source w/h 0.75–1.33):
  y=0–310:    Solid black top bar
  y=310–370:  Logo(60px) + channel name + handle
  y=380–460:  Hook text
  y=460–466:  Accent line
  y=466–1546: VIDEO 1080x1080 (centered in this region)
  y=1546–1920: Solid black (374px bottom bar)
```

Key changes from current:
- **Portrait no longer fills canvas** — always uses sandwich layout
- **Logo moved from y=12 to y=310** (~16% down, matching reference)
- **Hook moved from y=80 zone to y=380** (~20% down)
- **Hook font increased from 28px to 44px**
- **Top bar expanded from 80px to 310px** (matches reference's ~25% black bar)
- **Bottom bar expanded** proportionally

---

## Chunk 1: Update frame_compositor.py constants + tests

### Task 1: Update layout constants in frame_compositor.py

**Files:**
- Modify: `genlab-core/src/genlab_core/media/frame_compositor.py:62-119`

- [ ] **Step 1: Read current file**
Read `frame_compositor.py` fully before editing.

- [ ] **Step 2: Update the locked landscape constants (lines 72-80)**
```python
# Layout A: Landscape
L_TOP_BAR_H = 310          # was 0 (no explicit top bar)
L_NAME_ROW_Y = 310         # was L_NAME_ROW_H = 80
L_NAME_ROW_H = 60          # logo + name row height
L_HOOK_ZONE_Y = 380        # was 80
L_HOOK_ZONE_H = 80         # was 570 (now compact — 2 lines max)
L_ACCENT_Y = 460           # was 650
L_ACCENT_H = 6             # unchanged
L_VIDEO_Y = 466            # was 656
L_VIDEO_H = 608            # unchanged
L_BOTTOM_H = 846           # was 656
```

- [ ] **Step 3: Update portrait constants (lines 82-89)**
Portrait now uses sandwich layout, not fill_canvas:
```python
# Layout B: Portrait (NOW sandwiched — no more fill_canvas)
P_TOP_BAR_H = 310
P_NAME_ROW_Y = 310
P_NAME_ROW_H = 60
P_HOOK_ZONE_Y = 380
P_HOOK_ZONE_H = 80
P_ACCENT_Y = 460
P_ACCENT_H = 6
P_VIDEO_Y = 466
P_VIDEO_H = 1000           # max video height for portrait source
P_BOTTOM_H = 454
# Remove P_OVERLAY_H, P_OVERLAY_OPACITY, P_LOGO_SIZE, P_LOGO_X, P_LOGO_Y
```

- [ ] **Step 4: Update square constants (lines 91-99)**
```python
# Layout C: Square
S_TOP_BAR_H = 310
S_NAME_ROW_Y = 310
S_NAME_ROW_H = 60
S_HOOK_ZONE_Y = 380
S_HOOK_ZONE_H = 80
S_ACCENT_Y = 460
S_ACCENT_H = 6
S_VIDEO_Y = 466
S_VIDEO_H = 1080
S_BOTTOM_H = 374
```

- [ ] **Step 5: Update shared text constants (lines 101-119)**
```python
# Shared text — ALL CASES use same branding position now
LOGO_SIZE = 60             # was 56
LOGO_X = 45                # was 36
LOGO_Y = 310 + 0           # dynamic: NAME_ROW_Y + vertical center offset
NAME_FONT_SIZE = 24        # was 32
NAME_X = 120               # was 108 (logo_x + logo_size + gap)
NAME_Y = 322               # was 24
HANDLE_FONT_SIZE = 17      # was 22
HANDLE_X = 120             # was 108
HANDLE_Y = 346             # was 50
HANDLE_OPACITY = 0.70      # unchanged
HOOK_FONT_SIZE = 44        # was 28
HOOK_LINE_H = 52           # was 38
HOOK_MAX_LINES = 2         # was 3
HOOK_MAX_CHARS_LINE = 35   # was 32
SHADOW_OFFSET = 2          # unchanged
SHADOW_OPACITY = 0.50      # unchanged
HOOK_X = 45                # left margin for hook text
```

- [ ] **Step 6: Update the docstring at top of file**
Update the ASCII art layout diagram (lines 8-33) to match new positions.

- [ ] **Step 7: Update the portrait compose method**
Find the portrait rendering method. Remove the `fill_canvas` + dark overlay approach. Replace with sandwich layout (same structure as landscape but with P_VIDEO_H=1000).

- [ ] **Step 8: Run existing tests**
Run: `uv run --package genlab-core pytest genlab-core/tests/media/test_frame_compositor.py -x -v`
Expected: Some failures due to changed constants — that's expected, fix in next task.

- [ ] **Step 9: Update tests to match new constants**
Update all assertions that reference old constant values (L_VIDEO_Y=656, P_OVERLAY_H=220, etc.).

- [ ] **Step 10: Run tests again**
Run: `uv run --package genlab-core pytest genlab-core/tests/media/test_frame_compositor.py -x -v`
Expected: All pass.

- [ ] **Step 11: Commit**
```bash
git add genlab-core/src/genlab_core/media/frame_compositor.py genlab-core/tests/media/test_frame_compositor.py
git commit -m "feat(rendering): redesign frame layout — match Evolving AI reference

Portrait videos now use sandwich layout (not fill_canvas).
Logo+hook moved to 25% from top. Hook font 44px. Bottom bar expanded."
```

### Task 2: Update render_text_overlays.py to skip when frame_compositor handles hook

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/stages/render_text_overlays.py`

- [ ] **Step 1: Read current file**

- [ ] **Step 2: Add skip logic**
In `execute()`, check if the story's media was rendered by frame_compositor (which already burns hook text). If `media.get("compositor") == "frame_compositor"`, skip the overlay — it's already there.

```python
# After resolving hook_text
if media.get("compositor") == "frame_compositor":
    # frame_compositor already burned logo + hook — don't double-overlay
    skipped += 1
    continue
```

- [ ] **Step 3: Run tests**
Run: `uv run --package genlab-core pytest genlab-core/tests/ -k "overlay or render" -x -v`

- [ ] **Step 4: Commit**
```bash
git commit -m "fix(rendering): skip text overlay when frame_compositor already burned hook"
```

### Task 3: Update all visuals.yaml configs

**Files:**
- Modify: `ClutchWire/config/visuals.yaml`
- Modify: `SpliceReel/config/visuals.yaml`
- Modify: `FrameDrift/config/visuals.yaml`
- Also check: `CriticalRush/niches/gaming/config/visuals.yaml`, `Content Scraper/config/visuals.yaml`

- [ ] **Step 1: Update frame_layout section in all 5 configs**
Update `top_bar.height`, `top_bar.logo_y`, `hook_text.font_size`, `layout_cases.native_portrait` to remove `fill_canvas`, update `hook_y` values. Match the new constants.

- [ ] **Step 2: Update legacy top-level keys**
Change `hook_font_size: 32` → `hook_font_size: 44`, `top_bar_height_pct: 0.12` → `top_bar_height_pct: 0.24`.

- [ ] **Step 3: Commit each submodule, then parent**

### Task 4: Render a test video and verify visually

- [ ] **Step 1: Run a quick render test**
Pick one existing source clip from `.tmp/runs/anime_20260316_040002/clips/` and render it through the updated compositor.

- [ ] **Step 2: Open the output and compare to reference screenshots**
Verify logo position, hook text size and position, video placement, black bar proportions.

- [ ] **Step 3: If incorrect, adjust constants and re-render**

---

## Chunk 2: Consolidation Plan (separate document)

See `docs/superpowers/plans/2026-03-16-env-consolidation.md`
