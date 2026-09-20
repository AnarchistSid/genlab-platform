"""Ask an off-box worker for SAM2 mattes, and never let the answer block a publish.

WHY OFF-BOX (measured on prod, 2026-09-17, 2 cores / 3.8 GB / no GPU):

    SAM2 propagation   20.66 s/frame  ->  138 min for a 384-frame reel
    budget                                 15 min
    birefnet foreground                    OOM-KILLED at 2,931 MB, box idle

The matte step does not fit. It runs on a machine with a GPU (the Mac's MPS did
96 frames in 2.2 min, ~9 min extrapolated for 384) and the pipeline asks for it.

THE RULE THAT MATTERS MORE THAN THE SPEED:

    worker unreachable or timed out  ->  that fire renders LEGACY

Craft is additive. FFmpeg producing a publishable reel standalone is the
standing guarantee (CLAUDE.md render-path rule), and a renderer that can be
blocked by an absent laptop would convert that guarantee into a dependency. So
every failure here returns None, promptly, with a reason -- never an exception,
never an unbounded wait.

PROTOCOL. Deliberately files over SSH rather than a broker: Redis on prod is
bound to 127.0.0.1, and reaching it from the Mac would mean exposing a port or
tunnelling. The worker POLLS, so nothing needs to connect inward to a laptop
behind NAT.

    <root>/queued/<job>.json     pipeline writes; worker claims
    <root>/running/<job>.json    worker claims by moving it (atomic rename)
    <root>/done/<job>.json       worker writes result + mask paths
    <root>/failed/<job>.json     worker writes a reason it could not finish
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: The queue, in ONE place. The library defaulted to /opt/genlab/.matte_jobs
#: while the worker CLI and its launchd plist both used
#: /opt/genlab/.runtime/mattes -- so `worker_alive()` stat'd a directory that
#: has never existed, returned False, and every craft attempt was skipped
#: `worker_unavailable` with a live worker heartbeating 34 seconds away.
#: The worker CLI imports this now rather than repeating the literal.
DEFAULT_ROOT = Path(os.environ.get("GENLAB_MATTE_QUEUE", "/opt/genlab/.runtime/mattes"))
# §2: 20 minutes. The measured Mac run is ~9 min for 384 frames, so this is
# roughly 2x headroom -- long enough to absorb a slow pull, short enough that a
# dead worker costs one fire rather than the publish window.
DEFAULT_TIMEOUT_S = 1200
POLL_INTERVAL_S = 5.0
# Older than this and a worker is not running, whatever the queue looks like.
WORKER_STALE_AFTER_S = 180


class SkipReason:
    """Values recorded as ``craft_skipped`` in the run report."""

    WORKER_UNAVAILABLE = "worker_unavailable"
    TIMEOUT = "worker_timeout"
    FAILED = "worker_failed"
    QUEUE_UNWRITABLE = "queue_unwritable"
    #: The job named a subject colour without the whole HSV spec. The worker
    #: REFUSES rather than defaulting: defaults are the failure mode. UFC-05's
    #: archive recorded `hue_deg` alone, the worker supplied sat_min 0.25 /
    #: val_min 0.20 as defaults, and on a DARK navy garment those floors put the
    #: seed on the red cage instead -- 8 of 9 annotation frames rejected as
    #: garment and half the clip left unmasked. A partial spec is not a spec.
    SEED_SPEC_INCOMPLETE = "seed_spec_incomplete"


#: A subject colour spec must carry ALL FOUR fields. Hue alone is not a colour:
#: navy and sky blue share a hue and differ only in value, and it was exactly
#: that distinction that broke the UFC-05 matte.
SEED_SPEC_FIELDS = ("hue_deg", "hue_tol", "sat_min", "val_min")


def seed_spec_complete(spec: dict | None) -> bool:
    return bool(spec) and all(isinstance(spec.get(k), int | float) for k in SEED_SPEC_FIELDS)


@dataclass(frozen=True)
class HSVSpec:
    """The subject's garment colour. ALL FOUR fields, always.

    Hue alone is not a colour: navy and sky blue share a hue and differ only in
    value, and that is exactly the distinction that broke the UFC-05 matte — the
    archive recorded `hue_deg` alone, the worker defaulted sat/val, and on a dark
    navy garment the value floor excluded the subject so the seed landed on the
    red cage. Eight of nine annotation frames were rejected and half the clip
    came back unmasked.

    The worker refuses a partial spec at runtime; this type refuses to express
    one at all, which is the earlier and cheaper place to say no.
    """

    hue_deg: float
    hue_tol: float
    sat_min: float
    val_min: float

    def __post_init__(self) -> None:
        for name in ("hue_deg", "hue_tol", "sat_min", "val_min"):
            v = getattr(self, name)
            if v is None or isinstance(v, bool) or not isinstance(v, int | float):
                raise ValueError(f"HSVSpec.{name} must be a number, got {v!r}")
        if not 0.0 <= self.hue_deg < 360.0:
            raise ValueError(f"hue_deg out of range: {self.hue_deg}")
        if self.hue_tol <= 0:
            raise ValueError(f"hue_tol must be positive: {self.hue_tol}")

    def as_dict(self) -> dict:
        return {
            "hue_deg": float(self.hue_deg),
            "hue_tol": float(self.hue_tol),
            "sat_min": float(self.sat_min),
            "val_min": float(self.val_min),
        }


@dataclass(frozen=True)
class CropRow:
    """One output frame's crop, in NATIVE pixels.

    `src_h` is the chrome-cropped source height and is NOT optional: without it
    `crop_rect_for` cannot place the rect, and UFC-05's archive shipped a plan
    that omitted it while a second, unarchived plan carried it.
    """

    out: int
    mag: float
    cx: float
    cy: float
    src_h: int

    def as_dict(self) -> dict:
        return {
            "out": int(self.out),
            "mag": float(self.mag),
            "cx": float(self.cx),
            "cy": float(self.cy),
            "src_h": int(self.src_h),
        }


@dataclass(frozen=True)
class CropPlan:
    """Per-frame crop geometry. Masks are computed on the NATIVE frame and
    warped into reel space with this; a matte in the wrong space is silently
    wrong (measured once at 1.4% area where 28% was correct)."""

    rows: tuple[CropRow, ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("CropPlan needs at least one row")

    def as_dict(self) -> dict:
        return {str(r.out): r.as_dict() for r in self.rows}


@dataclass(frozen=True)
class MatteRequest:
    clip_path: str
    frames_dir: str
    #: REQUIRED for a matte request, OPTIONAL for a plan request.
    #:
    #: A matte job that cannot name the subject's colour or the crop geometry is
    #: one the worker will refuse, so it is refused here. A PLAN job is the job
    #: that DISCOVERS those things: deriving the seed needs a foreground matte,
    #: which needs rembg, which is in the worker's venv and not on the VPS. The
    #: worker derives it from the first candidate's own first frame.
    subject_spec: HSVSpec | None
    crop_plan: CropPlan
    annotations: list[dict] = field(default_factory=list)
    cuts: list[int] = field(default_factory=list)
    niche_id: str = ""
    blueprint_id: str = ""
    #: Ask the worker for the PLAN as well as the mattes, in one round trip:
    #: the silhouette vote, the chosen window, the finish frame. Twenty SAM2
    #: image calls do not fit the 2-core VPS (measured: 138 min for 96 frames,
    #: with OOM), so the decisions that need SAM2 are made where SAM2 lives —
    #: and the stage never posts a second job for them.
    plan: bool = False
    #: THE PLAN JOB'S OWN INPUTS. A plan-mode request describes windows to
    #: CHOOSE BETWEEN; the crop plan is empty because the geometry is derived
    #: from the window the worker picks. Each candidate carries its measured
    #: motion_score -- the worker refuses a job whose candidates lack it rather
    #: than ranking on a default.
    candidates: list[dict] = field(default_factory=list)
    fps: float = 30.0

    def as_job(self, job_id: str) -> dict:
        """The payload the worker receives — whole spec, whole plan."""
        return {
            "job_id": job_id,
            "clip_path": self.clip_path,
            "frames_dir": self.frames_dir,
            "n_frames": len(self.crop_plan.rows),
            "subject_colour": self.subject_spec.as_dict() if self.subject_spec else {},
            "crop_plan": self.crop_plan.as_dict(),
            "cuts": list(self.cuts),
            "niche_id": self.niche_id,
            "blueprint_id": self.blueprint_id,
            "plan": bool(self.plan),
            "subject_hint": self.subject_spec.as_dict() if self.subject_spec else {},
            "candidates": [dict(c) for c in self.candidates],
            "fps": float(self.fps),
        }


@dataclass(frozen=True)
class MatteResult:
    job_id: str
    mask_dir: str
    frames: int
    seconds: float

    @property
    def ok(self) -> bool:
        return self.frames > 0


def _dirs(root: Path) -> dict[str, Path]:
    return {n: root / n for n in ("queued", "running", "done", "failed")}


def worker_alive(root: Path | None = None, *, now: float | None = None) -> bool:
    """Has a worker checked in recently?

    Asked BEFORE queueing, so an absent worker costs a heartbeat read rather
    than the full timeout. A missing heartbeat is not an error -- it is the
    normal state when the laptop is closed.
    """
    root = Path(root or DEFAULT_ROOT)
    hb = root / "worker.heartbeat"
    try:
        age = (now or time.time()) - hb.stat().st_mtime
    except OSError:
        return False
    return age <= WORKER_STALE_AFTER_S


def request_matte(
    req: MatteRequest,
    *,
    root: Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    poll_s: float = POLL_INTERVAL_S,
    sleep=time.sleep,
    now=time.time,
) -> tuple[MatteResult | None, str]:
    """Queue a matte job and wait for it. Returns ``(result, reason)``.

    ``result`` is None on every failure path and ``reason`` is one of
    ``SkipReason``. This function does not raise: the caller is a render stage
    whose fallback is the legacy renderer, and an exception there would take a
    publishable reel down with it.
    """
    root = Path(root or DEFAULT_ROOT)
    d = _dirs(root)

    if not worker_alive(root, now=now()):
        logger.warning(
            "[matte] no worker heartbeat in %ds — rendering legacy this fire", WORKER_STALE_AFTER_S
        )
        return None, SkipReason.WORKER_UNAVAILABLE

    job_id = f"{req.niche_id or 'job'}-{uuid.uuid4().hex[:10]}"
    payload = {
        "job_id": job_id,
        "clip_path": req.clip_path,
        "frames_dir": req.frames_dir,
        "annotations": req.annotations,
        "cuts": req.cuts,
        "niche_id": req.niche_id,
        "blueprint_id": req.blueprint_id,
        "queued_at": now(),
    }
    try:
        for p in d.values():
            p.mkdir(parents=True, exist_ok=True)
        tmp = d["queued"] / f".{job_id}.tmp"
        tmp.write_text(json.dumps(payload))
        tmp.rename(d["queued"] / f"{job_id}.json")  # atomic: no half-read job
    except OSError as exc:
        logger.warning("[matte] cannot write the queue at %s (%s) — rendering legacy", root, exc)
        return None, SkipReason.QUEUE_UNWRITABLE

    logger.info("[matte] queued %s (timeout %.0fs)", job_id, timeout_s)
    deadline = now() + timeout_s
    while now() < deadline:
        done = d["done"] / f"{job_id}.json"
        failed = d["failed"] / f"{job_id}.json"
        if done.exists():
            try:
                r = json.loads(done.read_text())
                res = MatteResult(
                    job_id=job_id,
                    mask_dir=r["mask_dir"],
                    frames=int(r.get("frames", 0)),
                    seconds=float(r.get("seconds", 0.0)),
                )
            except (OSError, ValueError, KeyError) as exc:
                logger.warning("[matte] %s completed with an unreadable result (%s)", job_id, exc)
                return None, SkipReason.FAILED
            logger.info("[matte] %s done: %d frames in %.1fs", job_id, res.frames, res.seconds)
            return (res, "") if res.ok else (None, SkipReason.FAILED)
        if failed.exists():
            reason = ""
            try:
                reason = json.loads(failed.read_text()).get("reason", "")
            except (OSError, ValueError):
                pass
            logger.warning("[matte] %s failed on the worker: %s", job_id, reason[:120])
            return None, SkipReason.FAILED
        sleep(poll_s)

    logger.warning(
        "[matte] %s timed out after %.0fs — rendering legacy this fire", job_id, timeout_s
    )
    return None, SkipReason.TIMEOUT
