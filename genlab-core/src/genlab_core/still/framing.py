"""Per-shot framing, decided from boxes and checked before anything renders.

A 9:16 crop of a 16:9 source keeps 31.6% of the frame width. The banded 4:5
plate keeps 44%. Only a letterboxed 16:9 keeps all of it, at a third of the
screen. No single crop is right for every shot, so framing is a per-shot
decision made from the content and approved on a sheet.

The v13 full-bleed reel used the image model's focal POINT as its anchor. A
point says where to centre a crop; it cannot say how wide the content is, so a
two-fighter shot got centred on one fighter and the other fell off the edge.
Boxes are what the fit test needs, and they are what this module takes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 16:9 source, 9:16 delivery.
SRC_AR = 16 / 9
OUT_AR = 9 / 16
FIT_MARGIN = 0.06          # the content box must clear the crop by this much
PAN_MIN_SHOT_S = 1.2       # shorter than this, a pan reads as a lurch
FACE_RETENTION_MIN = 1.0   # faces are never cut
CONTENT_RETENTION_MIN = 0.92

# Tried in order: the tightest window that holds the content wins.
WIDEN_LADDER = (("4:5", 4 / 5), ("1:1", 1.0), ("16:9", 16 / 9))


def union(boxes: list[list[float]]) -> list[float] | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def content_box(subjects: list[dict], beat_names: set[str] | None = None
                ) -> tuple[list[float] | None, list[list[float]]]:
    """The union of every face box, plus the bodies of the beat's characters.

    Faces of everyone, bodies of whoever the beat is about: a bystander's head
    must not be sliced, but their body is not what the shot is for. In
    "Tanjiro vs Akaza" both fighters are the beat, so both bodies count.
    """
    faces = [s["face"] for s in subjects if s.get("face")]
    bodies = [s["body"] for s in subjects
              if s.get("body") and (beat_names is None or s["name"] in beat_names)]
    return union(faces + bodies), faces


def _crop_for(cb: list[float], ar: float) -> tuple[float, float, float, float]:
    """The delivery window of aspect `ar`, positioned on the content box.

    The window's SIZE is fixed by the aspect, not by the content: a delivery
    crop is always full source height, so its width is `ar / SRC_AR` of the
    frame. Only its position is free.

    Sizing it to the content instead was the first version's bug, and the
    packet contains its own check -- 9:16 keeps 31.6% of the width and 4:5
    keeps 44%. A function returning 20% for both is not measuring a delivery
    crop.

        9:16 -> 0.5625 / 1.7778 = 0.316     4:5  -> 0.8 / 1.7778 = 0.450
        1:1  -> 1.0    / 1.7778 = 0.563     16:9 -> 1.000
    """
    w = min(1.0, ar / SRC_AR)
    h = 1.0
    cx = (cb[0] + cb[2]) / 2
    x = min(max(cx - w / 2, 0.0), 1.0 - w)
    return x, 0.0, w, h


def fits(cb: list[float], ar: float = OUT_AR, margin: float = FIT_MARGIN) -> bool:
    """Does the content box fit a window of this aspect, with margin to spare?"""
    _, _, w, _ = _crop_for(cb, ar)
    need_w = (cb[2] - cb[0]) * (1 + margin)
    return need_w <= w + 1e-9


def retention(crop: tuple[float, float, float, float], cb: list[float],
              faces: list[list[float]]) -> tuple[float, float]:
    """(face_retention, content_retention) for one crop.

    face_retention is the WORST face's fraction inside the crop, not the mean:
    one sliced face is a sliced face however many others are whole.
    """
    x, y, w, h = crop

    def inside(b: list[float]) -> float:
        ix = max(0.0, min(b[2], x + w) - max(b[0], x))
        iy = max(0.0, min(b[3], y + h) - max(b[1], y))
        area = (b[2] - b[0]) * (b[3] - b[1])
        return (ix * iy) / area if area > 0 else 1.0

    fr = min((inside(f) for f in faces), default=1.0)
    cr = inside(cb) if cb else 1.0
    return fr, cr


MUST_RETENTION_MIN = 1.00      # faces + the beat's torsos: never cut
SHOULD_RETENTION_MIN = 0.80    # full bodies incl. limbs and weapons
LADDER = (("9:16", OUT_AR), ("4:5", 4 / 5), ("1:1", 1.0), ("16:9", SRC_AR))


def tiers(subjects: list[dict], beat_names: set[str] | None = None
          ) -> tuple[list[float] | None, list[float] | None, list[list[float]]]:
    """(must_keep, should_keep, faces).

    Two tiers, because one box conflated two different questions. A single
    content box built from full bodies made 13 of 16 shots widen to 16:9 -- a
    fighter mid-swing has a body spanning the frame, so nothing tighter ever
    fit, and the reel became letterboxed 16:9 everywhere. What must never be
    cut (faces, and the trunk of whoever the beat is about) is a much smaller
    box than what we would LIKE to keep (limbs, weapons, the arc of a swing).
    """
    faces = [x["face"] for x in subjects if x.get("face")]
    torsos = [x.get("torso") or x.get("face") for x in subjects
              if (beat_names is None or x["name"] in beat_names)
              and (x.get("torso") or x.get("face"))]
    bodies = [x["body"] for x in subjects
              if x.get("body") and (beat_names is None or x["name"] in beat_names)]
    return union(faces + torsos), union(bodies), faces


def _best_x(w: float, must: list[float], should: list[float] | None
            ) -> float | None:
    """Window position: contains `must`, then maximises `should` coverage.

    Centring on the must-keep box throws away should-keep content for no
    reason when the window has room to slide. The feasible range is every x
    that still contains must-keep; within it, pick the one that holds the most
    of the body box.
    """
    lo = max(0.0, must[2] - w)
    hi = min(1.0 - w, must[0])
    if lo > hi + 1e-9:
        return None                      # must-keep does not fit this aspect
    if not should:
        return (lo + hi) / 2
    best, best_cov = None, -1.0
    steps = 64
    for k in range(steps + 1):
        x = lo + (hi - lo) * k / steps
        cov = max(0.0, min(should[2], x + w) - max(should[0], x))
        if cov > best_cov:
            best, best_cov = x, cov
    return best


@dataclass
class ShotFraming:
    index: int
    mode: str                       # fullbleed | pan | widen
    crop: tuple[float, float, float, float]
    crop_end: tuple[float, float, float, float] | None = None   # pan target
    aspect: str = "9:16"
    face_retention: float = 1.0
    content_retention: float = 1.0
    reason: str = ""
    content: list[float] | None = None          # the must-keep box
    should: list[float] | None = None           # the should-keep box
    faces: list[list[float]] = field(default_factory=list)
    torso_retention: float = 1.0

    @property
    def ok(self) -> bool:
        return (self.face_retention >= MUST_RETENTION_MIN - 1e-9
                and self.content_retention >= SHOULD_RETENTION_MIN - 1e-9)


LOOK_GAIN = 0.03           # calibrated; see `decide`


def pick_aspect(faces: list[list[float]], n_action: int = 0,
                n_subjects: int = 0) -> str:
    """The window SIZE, from what the shot IS.

    Sized by the characters IN THE ACTION, not by how many faces happen to be
    visible: shot 7 is a two-fighter exchange with one of them turned away, so
    it has one visible face and still needs 4:5. Counting faces made it 9:16
    and cut the second fighter out of his own shot.

        nothing in the action, no face -> 16:9   (a true wide shot)
        one face filling >= 0.35       -> 1:1    (an extreme close-up)
        two or more in the action      -> 4:5
        otherwise                      -> 9:16

    16:9 on anything with a character in it is a finding, not an outcome.
    """
    # 16:9 means "there is nobody here". A character with no VERIFIED face is
    # still a character: shot 1 has Akaza with a torso box, a rejected face and
    # in_action false, and went to 16:9 -- against a hand-marked 9:16. A
    # rejected face box is a gap in what we measured, not a wide shot.
    if n_subjects == 0 and not faces:
        return "16:9"
    if faces and len(faces) == 1 and n_action <= 1 and \
            max(f[2] - f[0] for f in faces) >= 0.35:
        return "1:1"
    return "4:5" if n_action >= 2 else "9:16"


def facing_from_geometry(nose: list[float] | None, head: list[float] | None,
                         dead_zone: float = 0.012) -> str:
    """Which way the face points, in SCREEN coordinates, from a landmark.

    The model's own `facing` label was unusable in two different ways: it
    named a side without a convention anyone could check (its "left" put the
    window on the wrong side of the nose), and its `facial_region` box came
    back concentric with the head box, so no offset could be recovered from
    the boxes either. A nose point can be checked against the head box by
    arithmetic, in the same coordinates the crop is computed in.
    """
    if not nose or not head:
        return "camera"
    off = nose[0] - (head[0] + head[2]) / 2
    if off <= -dead_zone:
        return "left"
    if off >= dead_zone:
        return "right"
    return "camera"


def _place(w: float, faces: list[list[float]], look: float = 0.0,
           steps: int = 400) -> float:
    """Position: cover the most facial width; tie-break toward centre+look."""
    best, best_cov, best_d = 0.0, -1.0, 9.0
    for k in range(steps + 1):
        x = (1.0 - w) * k / steps
        cov = sum(max(0.0, min(f[2], x + w) - max(f[0], x)) for f in faces)
        d = abs((x + w / 2) - (0.5 + look))
        if cov > best_cov + 1e-6 or (abs(cov - best_cov) <= 1e-6 and d < best_d):
            best, best_cov, best_d = x, cov, d
    return best


def decide(index: int, subjects: list[dict], shot_s: float,
           beat_names: set[str] | None = None,
           act_first: str | None = None) -> ShotFraming:
    """Framing for one SHOT, from verified boxes.

    Looking room goes TOWARD the side the face points at on screen -- the room
    is in front of the nose, not behind the head. The previous version applied
    it the other way and left less space in front of Akaza's nose than the
    hand-marked window did.
    """
    faces = [x["facial_region"] for x in subjects if x.get("facial_region")]
    torsos = [x.get("torso") for x in subjects
              if x.get("torso") and (beat_names is None or x["name"] in beat_names)]
    bodies = [x["body"] for x in subjects
              if x.get("body") and (beat_names is None or x["name"] in beat_names)]
    n_action = sum(1 for x in subjects if x.get("in_action"))
    must, should, torso_u = union(faces), union(bodies), union(torsos)

    name = pick_aspect(faces, n_action, len(subjects))
    w = min(1.0, dict(LADDER)[name] / SRC_AR)

    look = 0.0
    if len(faces) == 1:
        s0 = next(x for x in subjects if x.get("facial_region"))
        side = facing_from_geometry(s0.get("nose"), s0.get("face"))
        look = -LOOK_GAIN if side == "left" else (LOOK_GAIN if side == "right" else 0.0)

    # Place on faces; with none verified, fall back to the torsos, which is
    # the tightest thing still known to be the character.
    heads = [x["face"] for x in subjects if x.get("face")]
    anchors = (faces or heads or [t for t in torsos if t]
               or [b for b in bodies if b])
    x = _place(w, anchors, look)
    crop = (x, 0.0, w, 1.0)

    def cover(box):
        if not box:
            return 1.0
        ix = max(0.0, min(box[2], x + w) - max(box[0], x))
        iy = max(0.0, min(box[3], 1.0) - max(box[1], 0.0))
        area = (box[2] - box[0]) * (box[3] - box[1])
        return (ix * iy) / area if area > 0 else 1.0

    fr = min((cover(f) for f in faces), default=1.0)
    mode = "fullbleed" if name == "9:16" else "widen"
    sf = ShotFraming(index, mode, crop, aspect=name, face_retention=fr,
                     content_retention=cover(should), content=must,
                     should=should, faces=faces,
                     reason=f"{name}; {n_action} in action, {len(faces)} face(s)"
                            + (f", looking room {look:+.2f}" if look else ""))
    sf.torso_retention = cover(torso_u)
    return sf


def lock_loop(plans: list[ShotFraming]) -> list[ShotFraming]:
    """The first and last shots share one window, so the loop does not jump.

    The reel loops, so its last frame cuts straight to its first. Two different
    windows on the same material make that cut read as a jump. The wider of the
    two wins -- it is the one that holds both shots' content.
    """
    if len(plans) < 2:
        return plans
    a, b = plans[0], plans[-1]
    keep = a if a.crop[2] >= b.crop[2] else b
    for p in (plans[0], plans[-1]):
        p.crop = keep.crop
        p.aspect = keep.aspect
        p.mode = keep.mode
        p.reason += " | loop-locked with the other end"
    return plans


CASCADE = "/Users/anarchistsid/GenLab/.media/models/lbpcascade_animeface.xml"


def detect_anime_faces(frame_gray, cascade_path: str = CASCADE) -> list[list[float]]:
    """Anime face boxes from an LBP cascade, as 0-1 fractions.

    A cross-check on the image model's face boxes, which are approximately
    right and not precise: measured against this detector they sit up to 0.35
    of the frame away, and a must-keep retention of 1.00 computed from a box
    that is not on the face means nothing. No torch, so rule #31 holds.
    """
    import cv2

    cas = cv2.CascadeClassifier(cascade_path)
    if cas.empty():
        return []
    h, w = frame_gray.shape[:2]
    eq = cv2.equalizeHist(frame_gray)
    found = cas.detectMultiScale(eq, scaleFactor=1.05, minNeighbors=4,
                                 minSize=(max(24, w // 40), max(24, h // 40)))
    return [[x / w, y / h, (x + bw) / w, (y + bh) / h] for x, y, bw, bh in found]


def _iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def merge_faces(vlm_faces: list[list[float]], detected: list[list[float]],
                iou_match: float = 0.15) -> list[list[float]]:
    """Detected boxes win where they overlap a model box; unmatched model boxes
    are kept. The detector is precise but misses (7 of 16 shots); the model is
    approximate but nearly always present. No box at all is the one outcome
    that lets a crop slice a head."""
    out = list(detected)
    for v in vlm_faces:
        if not any(_iou(v, d) >= iou_match for d in detected):
            out.append(v)
    return out


# --- clean-plate anchoring, for shots where no detector fires ---------------
# Demon Slayer SDBOxgbvZyY, the ceiling reveal: neither the face nor the head
# detector fires on a small figure high in frame, and TWO spatial methods gave
# the wrong answer. A saturation centroid and a column-variance profile both
# locked onto the doorframe and shelving -- static, high-contrast furniture --
# and returned cx 0.506 for a subject actually at 0.691. Differencing against a
# CLEAN PLATE of the same room, a frame from before the subject enters, found
# him immediately: cx 0.849 -> 0.529, median 0.691, against a hand mark of
# 0.685.
#
# The distinction is that a spatial statistic measures whatever is most
# textured, which in a furnished room is the furniture. A temporal difference
# measures what CHANGED, which is the subject and nothing else. It needs a
# static background and a plate frame, and it fails loudly when it has neither.

def clean_plate_anchor(plate_gray, frames_gray, threshold: float = 18.0,
                       min_changed: float = 0.004) -> dict:
    """Horizontal anchor for a subject the detectors miss, from what moved.

    ``plate_gray`` is the background alone; ``frames_gray`` the shot's frames,
    all the same shape. Returns the per-frame centres and their median, or an
    explicit refusal when too little changed -- which is what a static shot, a
    wrong plate, or a moving camera all look like, and none of them should
    silently produce a number.
    """
    import numpy as np

    cols, frac = [], []
    for f in frames_gray:
        mask = (np.abs(f.astype(float) - plate_gray.astype(float)) > threshold)
        frac.append(float(mask.mean()))
        c = mask.sum(axis=0)
        if c.sum() <= 0:
            continue
        cols.append(float((c * np.arange(len(c))).sum() / c.sum() / len(c)))
    present = [c for c, fr in zip(cols, frac) if fr >= min_changed]
    if not present:
        return {"ok": False, "cx": None, "n_present": 0,
                "why": ("nothing changed against the plate above %.1f%%: either the background "
                        "is not static, the plate is from the wrong shot, or the camera moves. "
                        "No anchor is returned." % (min_changed * 100))}
    return {"ok": True, "cx": float(np.median(present)),
            "cx_first": present[0], "cx_last": present[-1],
            "n_present": len(present), "max_changed": max(frac),
            "why": "median of the per-frame centres of changed pixels"}
