"""SAM2 + birefnet: the matte the worker actually produces.

RENDER-01 Part 5 section 2.

RUNS IN AN ISOLATED VENV
------------------------
`~/genlab-worker/.venv`, never the project venv. Rule #31: a torch-adjacent
install without an index pin once replaced `torch==2.12.1+cpu` with the whole
NVIDIA CUDA stack on a GPU-less box. Here the isolation is structural -- this
module's imports (`torch`, `sam2`, `rembg`) do not exist in the project venv at
all, so importing it from the wrong interpreter fails loudly instead of quietly
dragging a second torch into the pipeline's environment.

WHY OFF-BOX AT ALL
------------------
Measured: 138 minutes for a 96-frame segment on the 4 GB VPS, with OOM. On this
laptop's MPS it is ~1.8 s/frame. Craft never blocks a publish, so when the
laptop is shut prod records `craft_skipped=worker_unavailable` and renders
legacy -- the design, not a degraded mode.

WHAT IT DOES
------------
`build_mattes` (already in-tree, already pinned) owns the algorithm: annotation
frames chosen around cuts, whole-body seeds from the image predictor, SAM2 video
propagation between them. This module supplies the three callables it needs and
nothing else, so the sequencing stays in one place rather than being re-described
here -- the Port 4 lesson.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from genlab_core.action.clicks import derive_clicks
from genlab_core.action.colour_seed import match as colour_match
from genlab_core.action.matte import build_mattes, crop_rect_for, warp_to_crop

logger = logging.getLogger(__name__)

CHECKPOINT = Path(
    os.environ.get(
        "GENLAB_SAM2_CHECKPOINT",
        Path.home() / "genlab-worker" / "checkpoints" / "sam2.1_hiera_base_plus.pt",
    )
)
MODEL_CFG = os.environ.get("GENLAB_SAM2_CFG", "configs/sam2.1/sam2.1_hiera_b+.yaml")

#: A seed mask covering more than this share of the FRAME is background or a
#: merged pair, not one fighter. From the approved `u5_vote.silhouettes`.
MAX_SEED_FRAME_FRAC = 0.60


def _container_fps(clip: str) -> float | None:
    """The clip's own rate, as a float. Only used to report a mismatch."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "csv=p=0",
            clip,
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        num, den = out.split("/")
        return float(num) / float(den) if float(den) else None
    except (ValueError, ZeroDivisionError):
        return None


def _decode_frames(
    clip: str,
    n: int | None = None,
    *,
    start_s: float = 0.0,
    duration_s: float | None = None,
    fps: float | None = None,
) -> list[np.ndarray]:
    """RGB frames from a clip, via ffmpeg rawvideo.

    Decoding to a numpy array rather than to a directory of JPEGs: SAM2's video
    predictor accepts either, and a JPEG round-trip re-quantises every frame
    before the matte sees it.

    `fps` IS NOT OPTIONAL IN PRACTICE. Every index in the plan is converted to a
    time with the JOB's fps -- `frames_for` does `round(start_s * fps)` -- so a
    decode at the container's own rate silently means something else by "frame
    n". Measured on the UFC-05 span: the container is 59.94 fps, the job says
    30, and candidate 24.9 s resolved to frame 81, which is 23.55 s. Every
    window landed 1.35 s early and covered 1.6 s of footage instead of 3.2 s.
    Nothing errored; the mattes came back 96/96.
    """
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            clip,
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    w, h = (int(x) for x in probe.split(",")[:2])
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-v", "error"]
    if start_s:
        # -ss BEFORE -i seeks to the nearest KEYFRAME, which silently shifts
        # every index; after -i it decodes and discards, which is slower and
        # EXACT. An index off by one frame picks the wrong finish.
        cmd += ["-i", clip, "-ss", f"{start_s:.3f}"]
    else:
        cmd += ["-i", clip]
    if duration_s:
        cmd += ["-t", f"{duration_s:.3f}"]
    if fps:
        native = _container_fps(clip)
        if native and abs(native - fps) > 0.01:
            logger.info(
                "[sam2] %s is %.2f fps, decoding at %.2f — one frame index is 1/%.0f s",
                clip,
                native,
                fps,
                fps,
            )
        cmd += ["-vf", f"fps={fps}"]
    cmd += ["-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    total = len(raw) // (w * h * 3)
    frames = np.frombuffer(raw[: total * w * h * 3], np.uint8).reshape(total, h, w, 3)
    return list(frames[:n] if n else frames)


#: How many foreground planes to keep. Two windows' worth: enough that the
#: finish's stride-3 walk and the vote's samples both hit warm, bounded so the
#: span cannot accumulate.
FG_CACHE_FRAMES = 200


def _foreground_fn(frames: list[np.ndarray]):
    """birefnet-general through rembg. u2net and isnet segmented the RING ROPES
    on the ACTION footage; birefnet is the one that works."""
    from PIL import Image
    from rembg import new_session, remove

    session = new_session("birefnet-general")
    cache: dict[int, np.ndarray] = {}

    def fg(i: int) -> np.ndarray:
        if i not in cache:
            cut = remove(Image.fromarray(frames[i]), session=session)
            cache[i] = np.asarray(cut)[:, :, 3].astype(np.float32) / 255.0
            # BOUNDED. A full-res alpha plane is ~8 MB; an unbounded cache over
            # a 546-frame span is 4 GB of float32 on a machine that spent this
            # session between 6 and 18 GB into swap. Oldest-first eviction suits
            # the access pattern — every phase walks frames forward.
            while len(cache) > FG_CACHE_FRAMES:
                cache.pop(next(iter(cache)))
        return cache[i]

    fg.cache = cache
    return fg


class _HalfFrames:
    """Half-resolution view of a decoded span, materialised per access.

    birefnet on 960x540 costs roughly a quarter of 1920x1080, and the finish is
    a centroid TREND over about a second — it does not need the extra pixels.
    Nothing is precomputed: the finish samples every third frame, so halving the
    whole span up front would allocate three times what gets read.
    """

    def __init__(self, frames):
        self._f = frames

    def __getitem__(self, i):
        return self._f[i][::2, ::2]

    def __len__(self):
        return len(self._f)


def _predictors(device: str):
    import torch
    from sam2.build_sam import build_sam2, build_sam2_video_predictor
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    if not CHECKPOINT.exists():
        raise FileNotFoundError(
            f"SAM2 checkpoint missing at {CHECKPOINT}. Fetch it with:\n"
            "  curl -sSL -o ~/genlab-worker/checkpoints/sam2.1_hiera_base_plus.pt \\\n"
            "    https://dl.fbaipublicfiles.com/segment_anything_2/092824/"
            "sam2.1_hiera_base_plus.pt"
        )
    dev = torch.device(device if device != "mps" or torch.backends.mps.is_available() else "cpu")
    image = SAM2ImagePredictor(build_sam2(MODEL_CFG, str(CHECKPOINT), device=dev))
    video = build_sam2_video_predictor(MODEL_CFG, str(CHECKPOINT), device=dev)
    return image, video, dev


def mattes_for(job: dict, *, device: str = "mps"):
    """The worker's entry point. Returns ``(masks_by_frame, MatteReport)``."""
    clip = job.get("clip_path") or job.get("clip")
    if not clip or not Path(clip).exists():
        raise FileNotFoundError(f"clip not readable: {clip!r}")

    frames = _decode_frames(clip, job.get("n_frames"), fps=float(job.get("fps") or 0) or None)
    if len(frames) < 2:
        raise ValueError(f"{clip} decoded to {len(frames)} frame(s)")
    logger.info("[sam2] %d frames from %s", len(frames), clip)

    fg = _foreground_fn(frames)
    image_pred, video_pred, dev = _predictors(device)
    subject_hue = float(job.get("subject_hue", 0.0))
    seed_spec = job.get("subject_colour") or {
        "hue_deg": subject_hue,
        "hue_tol": 45.0,
        "sat_min": 0.25,
        "val_min": 0.20,
    }

    def silhouette_fn(i: int) -> dict[float, np.ndarray]:
        """Whole-body masks for this frame, keyed by garment hue.

        Clicks are DERIVED, never eyeballed, by the promoted `derive_clicks` --
        re-describing that derivation for a new call site dropped a constraint
        twice before it was promoted to code.
        """
        rgb, foreground = frames[i], fg(i)
        # `min_person_px` is required, not optional: below it the seed is a
        # scrap of matching hue rather than a body, and clicking it sends SAM2
        # after cage padding. Scaled to the frame so it transfers across sizes.
        min_px = int(0.0008 * rgb.shape[0] * rgb.shape[1])
        clicks = derive_clicks(rgb, foreground, seed_spec, min_px)
        if clicks is None:
            logger.info("[sam2] frame %d: subject colour not present — no seed", i)
            return {}
        pos, neg = clicks.positive, clicks.negative
        image_pred.set_image(rgb)
        pts = [pos] + ([neg] if neg is not None else [])
        labels = [1] + ([0] if neg is not None else [])
        masks, _, _ = image_pred.predict(
            point_coords=np.asarray(pts, np.float32),
            point_labels=np.asarray(labels, np.int32),
            multimask_output=True,
        )
        # The LARGEST mask, not the highest-scoring one: the top-scored mask is
        # routinely a torso or a sleeve, and the seed must be a whole body.
        # The LARGEST PLAUSIBLE mask, not the highest-scoring one and not the
        # largest outright. SAM2 returns subpart / part / whole, and a click on
        # the trunks scores the TRUNKS highest -- the whole-body masks are its
        # lowest-scored and are the only ones that separate a standing fighter
        # from a downed one. But "largest" alone takes a merged blob covering
        # most of the frame whenever SAM2 offers one, which is what the approved
        # `u5_vote.silhouettes` guards against by discarding anything over 60% of
        # the FRAME (distinct from is_garment_not_person's 60% of the
        # FOREGROUND) and taking the largest of the rest.
        areas = np.array([float((m > 0.5).mean()) for m in masks])
        plausible = np.where(areas <= MAX_SEED_FRAME_FRAC)[0]
        pick = (
            int(plausible[np.argmax(areas[plausible])]) if len(plausible) else int(np.argmin(areas))
        )
        best = masks[pick]
        # NO garment check here. `build_mattes` already runs
        # `is_garment_not_person` on this mask and RECORDS the rejection in
        # MatteReport.rejected_garment. A duplicate here returned {} instead,
        # which build_mattes reads as "no silhouette on this frame" and counts
        # nowhere -- so eight rejected seeds showed up as annotations=1 with an
        # EMPTY rejected_garment list, and the report said nothing was wrong.
        # A guard that hides its own firing is worse than no guard.
        return {subject_hue: best.astype(np.float32)}

    def propagate_fn(seeds: dict[int, np.ndarray]) -> dict[int, np.ndarray]:
        import torch

        state = video_pred.init_state(video_path=_frames_dir(frames))
        for f, mask in seeds.items():
            video_pred.add_new_mask(
                state, frame_idx=f, obj_id=1, mask=torch.as_tensor(mask > 0.5, device=dev)
            )
        out: dict[int, np.ndarray] = {}
        for idx, _ids, logits in video_pred.propagate_in_video(state):
            out[idx] = (logits[0] > 0).cpu().numpy().astype(np.float32)[0]
        return out

    # SAM2 RUNS ON THE NATIVE FRAME, and the masks are warped into reel space
    # afterwards. Running it on the crop instead was the single divergence
    # behind IoU 0.95 against the archived UFC-05 mattes: the approved pipeline
    # mattes 1920x1080 and warps, and warping its native mattes through
    # `crop_rect_for` reproduces its portrait mattes at IoU 1.0000 on 96/96.
    # The tracker also wants the context — a body leaving the crop is still in
    # the frame, and on the crop it simply vanishes.
    masks, report = build_mattes(
        len(frames),
        cuts=tuple(job.get("cuts") or ()),
        foreground_fn=fg,
        silhouette_fn=silhouette_fn,
        propagate_fn=propagate_fn,
        subject_hue=subject_hue,
    )
    plan = job.get("crop_plan")
    if plan:
        warped = {}
        for f, m in masks.items():
            row = plan.get(str(f), plan.get(f)) if isinstance(plan, dict) else None
            if row is None:
                warped[f] = m
                continue
            rect = crop_rect_for(
                mag=float(row["mag"]),
                cx=float(row["cx"]),
                cy=float(row["cy"]),
                src_h=int(row.get("src_h", m.shape[0])),
                src_w=m.shape[1],
            )
            warped[f] = warp_to_crop(m, rect)
        masks = warped
        logger.info("[sam2] warped %d masks into reel space", len(masks))
    else:
        logger.warning(
            "[sam2] no crop_plan in the job — masks stay in NATIVE space. Every "
            "effect reads them in CROP space, so a caller that forgets this gets "
            "a silently wrong matte (measured once at 1.4%% area where 28%% was "
            "correct)."
        )
    logger.info("[sam2] %d masks, report=%s", len(masks), report)
    return masks, report


def _frames_dir(frames: list[np.ndarray]) -> str:
    """SAM2's video predictor wants a directory of JPEGs; write one once."""
    import tempfile

    from PIL import Image

    d = tempfile.mkdtemp(prefix="sam2_frames_")
    for i, f in enumerate(frames):
        Image.fromarray(f).save(f"{d}/{i:05d}.jpg", quality=95)
    return d


def plan_backends(job: dict, *, device: str = "mps") -> dict:
    """The four callables `worker.plan.plan()` needs, wired to real models.

    Built here rather than in worker/plan.py so that module stays free of torch
    and rembg — it is the sequencing, and its pins replay the archive without a
    GPU. Frames are decoded ONCE and sliced per candidate: re-decoding for each
    of three windows was measured at most of the plan's wall time.
    """
    import numpy as np

    clip = job.get("clip_path") or job.get("clip") or ""
    if not clip or not Path(clip).exists():
        raise FileNotFoundError(f"clip not readable: {clip!r}")

    fps = float(job.get("fps", 30.0))

    # DECODE ONLY THE SPAN THE CANDIDATES COVER. `_decode_frames` reads the
    # whole clip into memory: the UFC source is 754 s of 1920x1080, which is
    # ~140 GB of frames. Candidates are a handful of 3.2 s windows scattered
    # across it, so the span from the earliest to the latest — plus the window
    # length — is all that is ever indexed.
    cands = job.get("candidates") or []
    if cands:
        starts = [float(c.get("start_s", 0.0)) for c in cands]
        longest = max(int(c.get("frames", 96)) for c in cands)
        lo_s = max(min(starts) - 1.0, 0.0)
        span_s = (max(starts) - lo_s) + longest / fps + 2.0
        all_frames = _decode_frames(clip, start_s=lo_s, duration_s=span_s, fps=fps)
        frame_offset = int(round(lo_s * fps))
    else:
        all_frames = _decode_frames(clip, fps=fps)
        frame_offset = 0
    fg = _foreground_fn(all_frames)
    image_pred, video_pred, dev = _predictors(device)
    seed_spec = dict(job.get("subject_hint") or {})

    def frames_for(start_s: float, n: int):
        # Indices are into the DECODED span, not the clip, so the offset is
        # subtracted here rather than leaking into every caller.
        lo = int(round(start_s * fps)) - frame_offset
        lo = max(lo, 0)
        return list(range(lo, min(lo + n, len(all_frames))))

    def silhouette_fn(i: int) -> dict[float, np.ndarray]:
        """One SAM2 image call; largest plausible mask; negative on the other."""
        if not isinstance(i, int) or not 0 <= i < len(all_frames):
            return {}
        rgb, foreground = all_frames[i], fg(i)
        min_px = int(0.0008 * rgb.shape[0] * rgb.shape[1])
        clicks = derive_clicks(rgb, foreground, seed_spec, min_px)
        if clicks is None:
            return {}
        image_pred.set_image(rgb)
        pts = [clicks.positive] + ([clicks.negative] if clicks.negative else [])
        labels = [1] + ([0] if clicks.negative else [])
        masks, _, _ = image_pred.predict(
            point_coords=np.asarray(pts, np.float32),
            point_labels=np.asarray(labels, np.int32),
            multimask_output=True,
        )
        areas = np.array([float((m > 0.5).mean()) for m in masks])
        ok = np.where(areas <= MAX_SEED_FRAME_FRAC)[0]
        pick = int(ok[np.argmax(areas[ok])]) if len(ok) else int(np.argmin(areas))
        return {float(seed_spec.get("hue_deg", 0.0)): masks[pick].astype(np.float32)}

    # The window the plan settled on. `build_mattes` indexes frames
    # WINDOW-RELATIVE (0..n-1) while the vote indexes them ABSOLUTE into the
    # decoded span, so the two disagree unless something reconciles them. Left
    # unreconciled, the mattes are computed on the START of the span rather than
    # on the chosen window — a silent wrong answer, not an error.
    chosen: dict = {"offset": 0, "n": 0}

    def select_window(start_s: float, n: int) -> None:
        chosen["offset"] = max(int(round(start_s * fps)) - frame_offset, 0)
        chosen["n"] = n

    def propagate_fn(seeds: dict) -> dict:
        import torch

        # Propagate over the WINDOW, not the span. The span covers every
        # candidate — 546 frames here against the window's 96 — and at ~4 s a
        # frame that is 36 minutes of tracking thrown away.
        off, n = chosen["offset"], (chosen["n"] or len(all_frames))
        window = all_frames[off : off + n]
        state = video_pred.init_state(video_path=_frames_dir(window))
        for f, mask in seeds.items():
            video_pred.add_new_mask(
                state, frame_idx=f, obj_id=1, mask=torch.as_tensor(mask > 0.5, device=dev)
            )
        out = {
            idx: (logits[0] > 0).cpu().numpy().astype(np.float32)[0]
            for idx, _ids, logits in video_pred.propagate_in_video(state)
        }
        # DROP THE STATE BEFORE RETURNING. init_state holds image embeddings for
        # every frame of the window; the caller's next phase is ~96 fresh image
        # -predictor calls. Left resident, that pushed a 19 GB machine to 18 GB
        # of swap and per-frame cost from ~15 s to minutes — the phase ran over
        # an hour with no output. Nothing downstream reads `state`.
        del state
        if hasattr(torch, "mps"):
            torch.mps.empty_cache()
        return out

    # THE OPPONENT IS DERIVED, NOT TRACKED. `silhouette_fn` returns exactly one
    # entry — {subject_hue: mask} — so any caller asking it for a second body
    # gets nothing, silently. These two backends are what the finish subtracts
    # with instead: a half-res foreground covering BOTH bodies, and a coarse
    # colour seed covering the subject. Neither touches SAM2.
    half_fg = _foreground_fn(_HalfFrames(all_frames))

    def coarse_foreground_fn(i: int) -> np.ndarray:
        if not isinstance(i, int) or not 0 <= i < len(all_frames):
            return np.zeros((2, 2), np.float32)
        return half_fg(i)

    def seed_mask_fn(i: int) -> np.ndarray:
        """Coarse SUBJECT mask: the garment hue, scoped inside the foreground.

        Unscoped, this selected the cage padding. Scoped, it is loose and
        holey — which is fine, because it is dilated and subtracted, never
        rendered."""
        if not isinstance(i, int) or not 0 <= i < len(all_frames):
            return np.zeros((2, 2), np.float32)
        return colour_match(all_frames[i][::2, ::2], seed_spec, foreground=half_fg(i))

    def half_silhouette_fn(i: int) -> dict[float, np.ndarray]:
        """The vote's silhouette, at half resolution.

        The vote compares garment AREAS between two bodies. Halving resolution
        halves both, so the ratio that decides it is unchanged — at a quarter of
        the SAM2 image cost. The MATTES still come from the full-res path; this
        is only for the decision.
        """
        if not isinstance(i, int) or not 0 <= i < len(all_frames):
            return {}
        rgb, foreground = all_frames[i][::2, ::2], half_fg(i)
        min_px = int(0.0008 * rgb.shape[0] * rgb.shape[1])
        clicks = derive_clicks(rgb, foreground, seed_spec, min_px)
        if clicks is None:
            return {}
        image_pred.set_image(rgb)
        pts = [clicks.positive] + ([clicks.negative] if clicks.negative else [])
        labels = [1] + ([0] if clicks.negative else [])
        masks, _, _ = image_pred.predict(
            point_coords=np.asarray(pts, np.float32),
            point_labels=np.asarray(labels, np.int32),
            multimask_output=True,
        )
        areas = np.array([float((m > 0.5).mean()) for m in masks])
        ok = np.where(areas <= MAX_SEED_FRAME_FRAC)[0]
        pick = int(ok[np.argmax(areas[ok])]) if len(ok) else int(np.argmin(areas))
        return {float(seed_spec.get("hue_deg", 0.0)): masks[pick].astype(np.float32)}

    def quarter_track_fn(start_s: float, n: int, seed_frame: int, seed_mask) -> dict:
        """Track the subject at QUARTER resolution across the window.

        The fallback for when the colour seed collapses. A centroid does not
        need a precise edge, and at 1/4 scale the propagation is ~1/16 the
        pixels — cheap enough to be a fallback rather than a second plan.

        The seed comes from the vote's own mask, so the tracker and the vote
        cannot disagree about which fighter this is.
        """
        import torch

        lo = max(int(round(start_s * fps)) - frame_offset, 0)
        window = [f[::4, ::4] for f in all_frames[lo : lo + n] if f is not None]
        if len(window) < 3:
            logger.warning("[sam2] quarter-res track: %d frames available", len(window))
            return {}
        m = np.asarray(seed_mask, np.float32)
        m = m[
            :: max(m.shape[0] // window[0].shape[0], 1), :: max(m.shape[1] // window[0].shape[1], 1)
        ]
        m = m[: window[0].shape[0], : window[0].shape[1]]
        state = video_pred.init_state(video_path=_frames_dir(window))
        video_pred.add_new_mask(
            state, frame_idx=int(seed_frame), obj_id=1, mask=torch.as_tensor(m > 0.5, device=dev)
        )
        out = {
            idx: (logits[0] > 0).cpu().numpy().astype(np.float32)[0]
            for idx, _ids, logits in video_pred.propagate_in_video(state)
        }
        del state
        if hasattr(torch, "mps"):
            torch.mps.empty_cache()
        logger.info("[sam2] quarter-res track returned %d masks", len(out))
        return out

    def release_span(start_s: float, n: int) -> None:
        """Drop everything outside the chosen window before propagation.

        The vote and the finish are done with the rest of the span by then, and
        propagation is about to hold image embeddings for every frame of the
        window on top of whatever is still resident.
        """
        lo = max(int(round(start_s * fps)) - frame_offset, 0)
        hi = lo + n
        freed = 0
        for k in range(len(all_frames)):
            if not lo <= k < hi and all_frames[k] is not None:
                all_frames[k] = None
                freed += 1
        for cache in (getattr(fg, "cache", {}), getattr(half_fg, "cache", {})):
            for k in [k for k in cache if not lo <= k < hi]:
                cache.pop(k, None)
        logger.info(
            "[sam2] released %d of %d span frames outside the window", freed, len(all_frames)
        )

    def rss_mb() -> int:
        """CURRENT resident set, in MB.

        `ru_maxrss` is a HIGH-WATER MARK: it only rises, so a threshold on it
        can fire once and never clear. It reported 7843 MB three phase
        boundaries running while the release had just freed 141 frames. Peak is
        still logged, labelled as peak, because it is what predicts an OOM.
        """
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = int(peak / (1024 * 1024)) if sys.platform == "darwin" else int(peak / 1024)
        try:
            import psutil
        except ImportError:
            logger.warning("[sam2] psutil missing — current RSS unavailable, reporting peak")
            return peak_mb
        cur_mb = int(psutil.Process().memory_info().rss / (1024 * 1024))
        logger.info("[sam2] RSS current %d MB / peak %d MB", cur_mb, peak_mb)
        return cur_mb

    def window_silhouette_fn(i: int) -> dict:
        """Window-relative index -> the ABSOLUTE frame the vote also used.

        `build_mattes` counts 0..n-1 within the window; the vote counts frames
        of the decoded span. Without this shim the mattes are built on the START
        of the span instead of the chosen window, which is a wrong answer with
        no error attached to it.
        """
        return silhouette_fn(chosen["offset"] + int(i))

    def window_foreground_fn(i: int):
        return fg(chosen["offset"] + int(i))

    return {
        "frames_for": frames_for,
        "silhouette_fn": silhouette_fn,  # absolute — the vote's space
        "foreground_fn": fg,
        "propagate_fn": propagate_fn,
        "select_window": select_window,
        "coarse_foreground_fn": coarse_foreground_fn,
        "half_silhouette_fn": half_silhouette_fn,
        "release_span": release_span,
        "quarter_track_fn": quarter_track_fn,
        "rss_mb": rss_mb,
        "seed_mask_fn": seed_mask_fn,
        "window_silhouette_fn": window_silhouette_fn,
        "window_foreground_fn": window_foreground_fn,
    }
