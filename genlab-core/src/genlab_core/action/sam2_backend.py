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
from pathlib import Path

import numpy as np

from genlab_core.action.clicks import derive_clicks
from genlab_core.action.matte import build_mattes

logger = logging.getLogger(__name__)

CHECKPOINT = Path(
    os.environ.get(
        "GENLAB_SAM2_CHECKPOINT",
        Path.home() / "genlab-worker" / "checkpoints" / "sam2.1_hiera_base_plus.pt",
    )
)
MODEL_CFG = os.environ.get("GENLAB_SAM2_CFG", "configs/sam2.1/sam2.1_hiera_b+.yaml")


def _decode_frames(clip: str, n: int | None = None) -> list[np.ndarray]:
    """RGB frames from a clip, via ffmpeg rawvideo.

    Decoding to a numpy array rather than to a directory of JPEGs: SAM2's video
    predictor accepts either, and a JPEG round-trip re-quantises every frame
    before the matte sees it.
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
    raw = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            clip,
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
    ).stdout
    total = len(raw) // (w * h * 3)
    frames = np.frombuffer(raw[: total * w * h * 3], np.uint8).reshape(total, h, w, 3)
    return list(frames[:n] if n else frames)


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
        return cache[i]

    return fg


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

    frames = _decode_frames(clip, job.get("n_frames"))
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
        # The LARGEST mask, not the highest-scoring one: the top-scored mask is
        # routinely a torso or a sleeve, and the seed must be a whole body.
        best = max(masks, key=lambda m: float((m > 0.5).sum()))
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

    masks, report = build_mattes(
        len(frames),
        cuts=tuple(job.get("cuts") or ()),
        foreground_fn=fg,
        silhouette_fn=silhouette_fn,
        propagate_fn=propagate_fn,
        subject_hue=subject_hue,
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
