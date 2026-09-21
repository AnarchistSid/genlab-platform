"""One entry per belt app we use, selected by KIND.

ARCH-02 §3 / CONTENT-FIRST-01 §1. Niche YAML names a *kind* --- ``still``,
``card``, ``clip_extend``, ``restyle``, ``upscale``, ``tts``, ``music``,
``sfx``, ``cutout``, ``inspect`` --- and this resolves it to the cheapest
entry that passes. Nothing downstream names an app.

Why it exists: six apps were in use out of a hundred-plus, because every
call site hardcoded its own ref and its own cost. Two registries already
existed with this exact shape (``media/hook_thumbnail_models.py``,
``media/pruna_video_client_models.py``), each with a *hardcoded* cost that
nobody had billed.

## Cost is measured, never catalogued

``cost_per_unit_usd`` comes from ``belt task cost`` after settlement, and
lives in ``measured_costs.json`` beside this file with its task id and date
--- not as a literal in Python. Two reasons:

* The catalog is not a per-unit price. ``topaz/astra`` is quoted at
  "$0.0714/credit", which is not a unit anyone can budget against.
* A mid-run balance delta is a RESERVATION HOLD at the top of the estimate,
  not a charge. Measured on this account while probing ``topaz/astra``,
  2026-09-21:

      balance before  $108.05
      balance during   $93.62     -> an apparent $14.43
      belt task cost              -> $0.42858 charged

  A 33.7x overstatement. Reading the balance would have reported the single
  probe as costing three times the entire $5 probe cap. Nothing here reads a
  balance; cost comes from ``belt task cost`` once ``charged`` is present.

An app with no measured cost is ``unmeasured`` and cannot be selected for
production, however cheap its catalog entry looks.

## Rights

``owned_channels`` --- cleared for the five channels we publish.
``eval_only``      --- usable in evaluation, never selectable for production.
``unread``         --- terms not yet read; treated as eval_only at selection.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from genlab_core.capabilities import inputs as _in

_COSTS_PATH = Path(__file__).with_name("measured_costs.json")

Kind = Literal[
    "still",
    "card",
    "clip_extend",
    "restyle",
    "upscale",
    "tts",
    "music",
    "sfx",
    "cutout",
    "inspect",
    "thumbnail",
    "video_gen",
]

KINDS: frozenset[str] = frozenset(
    {
        "still",
        "card",
        "clip_extend",
        "restyle",
        "upscale",
        "tts",
        "music",
        "sfx",
        "cutout",
        "inspect",
        "thumbnail",
        "video_gen",
    }
)

Rights = Literal["owned_channels", "eval_only", "unread"]

#: Where a call happens. "fire" is inside a pipeline run and is latency-bound.
_CONTEXTS: frozenset[str] = frozenset({"fire", "batch"})


class UnknownKind(KeyError):
    """A niche YAML named a kind that does not exist.

    Loud on purpose. A typo that silently resolved to nothing would look
    exactly like "this niche has no capability configured", which is the
    failure mode the whole registry exists to end.
    """


class Unselectable(RuntimeError):
    """A kind exists but nothing in it may be used for production."""


@dataclass(frozen=True)
class Capability:
    ref: str
    kind: str
    rights: str
    #: Short id for logging and bandit arm attribution. Carried over from the
    #: absorbed registries, where the arm id is `{kind}:{model_id}` and joins
    #: engagement metrics back to the model that produced the asset.
    model_id: str = ""
    #: Per-app input shaping. Each app names the same idea differently
    #: (`image_size` / `size` / `resolution`), which is the one thing the two
    #: absorbed registries held that could not be expressed as data.
    build_input: Callable[..., dict] | None = None
    #: What the catalog claims. ADVISORY ONLY — never used for selection or
    #: budgeting. Kept so a measured figure can be compared against it, which
    #: is how pruna/p-video's 4x draft-price gap was found.
    catalog_price: str = ""
    constraints: dict = field(default_factory=dict)
    composite_safe: bool = True
    eval_only: bool = False
    #: Too slow to sit inside a pipeline fire. Latency is a selection
    #: constraint, not a footnote: pruna/p-video-edit billed 250 s for ONE
    #: second of video, so a 30 s reel is two hours. `select(context="fire")`
    #: cannot return these; `context="batch"` can.
    batch_only: bool = False
    notes: str = ""

    # ── measured, from measured_costs.json ──────────────────────────────
    @property
    def _measured(self) -> dict | None:
        return _measured_costs().get(self.ref)

    @property
    def cost_per_unit_usd(self) -> float | None:
        m = self._measured
        return None if m is None else m.get("cost_per_unit_usd")

    @property
    def unit(self) -> str:
        m = self._measured
        return "" if m is None else str(m.get("unit", ""))

    @property
    def latency_s(self) -> float | None:
        m = self._measured
        return None if m is None else m.get("latency_s")

    @property
    def measured(self) -> bool:
        return self.cost_per_unit_usd is not None

    @property
    def selectable_for_production(self) -> bool:
        return self.measured and not self.eval_only and self.rights == "owned_channels"


@lru_cache(maxsize=1)
def _measured_costs() -> dict[str, dict]:
    if not _COSTS_PATH.exists():
        return {}
    return json.loads(_COSTS_PATH.read_text())


_REGISTRY: tuple[Capability, ...] = (
    Capability(
        ref="pruna/flux-2-klein-4b",
        kind="still",
        rights="owned_channels",
        constraints={"aspect_ratio": ["1:1", "9:16", "16:9"], "max_megapixels": 4},
        notes="4B params; the STILL default for anime/movies beats.",
    ),
    Capability(
        ref="bytedance/seedream-5-pro",
        kind="card",
        rights="owned_channels",
        constraints={"size": ["1K", "2K"], "text_rendering": True},
        notes="Renders legible text and infographics; 2K doubles the price, 1K is the unit.",
    ),
    Capability(
        ref="pixverse/extend",
        kind="clip_extend",
        rights="owned_channels",
        constraints={"max_input_s": 30, "max_px": 1920, "max_mb": 50, "duration_s": [5]},
        composite_safe=False,
        notes="Extends a sourced clip. PRE-composite only (rule 5).",
    ),
    Capability(
        ref="pruna/p-video-edit",
        kind="restyle",
        rights="owned_channels",
        constraints={"max_input_s": 15, "draft": True},
        composite_safe=False,
        batch_only=True,
        notes="Draft mode is the measured unit. 250 s for 1 s of video — a 30 s reel is two hours.",
    ),
    Capability(
        ref="topaz/astra",
        kind="upscale",
        rights="owned_channels",
        constraints={"scale": [1.0, 4.0], "probe_cap_s": 1},
        batch_only=True,
        notes=(
            "Prices in CREDITS, so the catalog gives no per-unit figure. Measured "
            "202 s for ONE second — batch_only is applied here by the same rule as "
            "p-video-edit, from the measurement rather than from the spec, which "
            "named only p-video-edit. Revert if a fire should be allowed to wait."
        ),
    ),
    Capability(
        ref="elevenlabs/sound-effects",
        kind="sfx",
        rights="owned_channels",
        constraints={"duration_s": [0.5, 22]},
    ),
    Capability(
        ref="elevenlabs/music",
        kind="music",
        rights="owned_channels",
        constraints={"duration_s": [5, 600]},
        notes="Bed for STILL craft, ducked 6-10 dB under narration.",
    ),
    Capability(
        ref="inworld/text-to-speech-2",
        kind="tts",
        rights="owned_channels",
        constraints={"max_chars": 2000, "encodings": ["MP3", "LINEAR16"]},
        notes="Already the head of the measured TTS cascade in prod.",
    ),
    Capability(
        ref="falai/dia-tts",
        kind="tts",
        rights="eval_only",
        eval_only=True,
        notes="Evaluation only — never selectable for production, so not probed.",
    ),
    Capability(
        ref="infsh/birefnet",
        kind="cutout",
        rights="owned_channels",
        constraints={"return_mask": True},
        notes="Measured free. The matting model that works where u2net/isnet segmented ring ropes.",
    ),
    # ── absorbed from media/hook_thumbnail_models.py (Part 33 §1) ───────────
    # ORDER IS LOAD-BEARING. pick_deterministic hashes to an index into this
    # sequence, so reordering re-attributes every blueprint's bandit arm.
    # `for_kind` sorts by COST and must never be used for the rotation.
    Capability(
        ref="pruna/flux-dev", kind="thumbnail", rights="owned_channels",
        model_id="flux", build_input=_in._build_flux_input,
        catalog_price="$0.005/image",
        notes="The baseline. Multi-model is OFF in prod, so this is the only thumbnail model live.",
    ),
    Capability(
        ref="openai/gpt-image-2", kind="thumbnail", rights="owned_channels",
        model_id="gpt-image-2", build_input=_in._build_gpt_image_input,
        catalog_price="$0.006/image",
    ),
    Capability(
        ref="xai/grok-imagine-image", kind="thumbnail", rights="owned_channels",
        model_id="grok-imagine", build_input=_in._build_grok_input,
        catalog_price="$0.020/image",
    ),
    Capability(
        ref="bytedance/seedream-4-5", kind="thumbnail", rights="owned_channels",
        model_id="seedream-4-5", build_input=_in._build_seedream_input,
        catalog_price="$0.040/image",
    ),
    Capability(
        ref="google/gemini-3-pro-image-preview", kind="thumbnail", rights="owned_channels",
        model_id="gemini-3-pro-image", build_input=_in._build_gemini_pro_input,
        catalog_price="$0.134/image (premium tier)",
    ),
    Capability(
        ref="falai/reve", kind="thumbnail", rights="owned_channels",
        model_id="reve", build_input=_in._build_reve_input,
        catalog_price="$0.040/image",
    ),
    # ── absorbed from media/pruna_video_client_models.py ────────────────────
    Capability(
        ref="pruna/p-video", kind="video_gen", rights="owned_channels",
        model_id="pruna-p-video", build_input=_in._build_pruna_input,
        catalog_price="720p $0.02/sec ($0.005/sec draft)",
        notes=(
            "The baseline, live for anime backfill. MEASURED $0.02000 for 1 s at "
            "720p draft — 4x the catalog's draft rate, which is exactly why "
            "catalog_price is advisory and never used to select or to budget."
        ),
    ),
    Capability(
        ref="alibaba/wan-2-7-t2v", kind="video_gen", rights="owned_channels",
        model_id="alibaba-wan-2-7", build_input=_in._build_wan_input,
        catalog_price="$0.10/sec at 720p",
    ),
    Capability(
        ref="klingai/video-v2-6", kind="video_gen", rights="owned_channels",
        model_id="kling-v2-6", build_input=_in._build_kling_input,
        catalog_price="$0.21-$1.68/video",
    ),
    Capability(
        ref="bytedance/seedance-2-0-fast", kind="video_gen", rights="owned_channels",
        model_id="seedance-2-0-fast", build_input=_in._build_seedance_input,
        catalog_price="$0.0056/1K tokens",
    ),
    Capability(
        ref="google/veo-3", kind="video_gen", rights="owned_channels",
        model_id="veo-3", build_input=_in._build_veo_input,
        catalog_price="$0.20/sec 720p no-audio",
    ),
)


def for_kind(kind: str) -> tuple[Capability, ...]:
    """Every entry of a kind, cheapest measured first. Raises on a bad kind."""
    if kind not in KINDS:
        raise UnknownKind(
            f"unknown capability kind {kind!r}; known kinds are {sorted(KINDS)}. "
            "A niche YAML named something this registry does not provide."
        )
    entries = [c for c in _REGISTRY if c.kind == kind]
    return tuple(
        sorted(entries, key=lambda c: (not c.measured, c.cost_per_unit_usd or 0.0, c.ref))
    )


def select(kind: str, *, production: bool = True, context: str = "fire") -> Capability:
    """The cheapest entry of ``kind`` that may actually be used here.

    ``production=True`` (the default) excludes ``eval_only``, ``unread``
    rights, and anything unmeasured. There is no "fall back to the catalog
    price" path: an unmeasured app is unselectable, because the number that
    would rank it does not exist.

    ``context`` is the latency budget, not a preference. ``"fire"`` (the
    default) is inside a pipeline run and cannot see ``batch_only`` entries;
    ``"batch"`` is an offline job and can. Defaulting to ``"fire"`` means a
    caller that never thought about latency gets the safe answer.
    """
    if context not in _CONTEXTS:
        raise ValueError(f"context must be one of {sorted(_CONTEXTS)}, got {context!r}")
    entries = for_kind(kind)
    usable = [c for c in entries if c.selectable_for_production] if production else list(entries)
    if context == "fire":
        usable = [c for c in usable if not c.batch_only]
    if not usable:
        why = ", ".join(
            f"{c.ref}(measured={c.measured}, eval_only={c.eval_only}, "
            f"rights={c.rights}, batch_only={c.batch_only})"
            for c in entries
        ) or "no entries at all"
        raise Unselectable(
            f"no {context}-selectable entry for kind {kind!r}: {why}"
        )
    return usable[0]


#: Bandit arm-id prefix per kind. Per-kind because the two absorbed registries
#: used different conventions, and the reward router joins on the exact
#: string: ``transformation_reward_router.route_dimension_reward`` reads
#: ``blueprint.arm_ids_by_dimension`` at 48 h collection. Changing a prefix
#: orphans every arm that already has history.
_ARM_PREFIX: dict[str, str] = {
    "thumbnail": "hook_thumbnail_model",
    "video_gen": "video_backfill_model",
}

#: Canary flag per kind. OFF means the rotation always returns the baseline —
#: the zero-regression behaviour both absorbed modules had, and the state in
#: prod today for both.
_MULTI_MODEL_FLAG: dict[str, str] = {
    "thumbnail": "GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED",
    "video_gen": "GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED",
}


def in_registration_order(kind: str) -> tuple[Capability, ...]:
    """Entries of a kind in DECLARATION order.

    Deliberately distinct from ``for_kind``, which sorts by cost. The
    deterministic rotation hashes to an index into this sequence, so using
    the cost-sorted view would silently re-map every blueprint to a different
    model and orphan bandit arms that already carry history. Two views of one
    table, because the two questions are not the same question.
    """
    if kind not in KINDS:
        raise UnknownKind(f"unknown capability kind {kind!r}; known: {sorted(KINDS)}")
    return tuple(c for c in _REGISTRY if c.kind == kind)


def multi_model_enabled(kind: str) -> bool:
    """Read the canary flag at call time, per kind."""
    flag = _MULTI_MODEL_FLAG.get(kind)
    if not flag:
        return False
    return (os.environ.get(flag) or "").strip().lower() in ("1", "true", "yes", "on")


def pick_deterministic(kind: str, seed_text: str, niche_id: str) -> Capability:
    """Same ``(seed_text, niche)`` -> same entry. The absorbed rotation, unchanged.

    This is NOT ``select``. ``select`` answers "what is cheapest for this
    job"; this answers "which arm does this blueprint belong to", and that
    must stay stable across retries so re-renders are idempotent and so 48 h
    reward joins back to the model that actually produced the asset.
    """
    entries = in_registration_order(kind)
    if not entries:
        raise Unselectable(f"no entries for kind {kind!r}")
    if not multi_model_enabled(kind):
        return entries[0]
    h = hashlib.sha256(f"{niche_id}::{seed_text}".encode()).digest()
    return entries[int.from_bytes(h[:2], "big") % len(entries)]


def arm_id_for(cap: Capability) -> str:
    """``<prefix>__<model_id>`` — the reward router joins on this exact string."""
    prefix = _ARM_PREFIX.get(cap.kind)
    if not prefix:
        raise UnknownKind(f"kind {cap.kind!r} has no bandit arm prefix")
    return f"{prefix}__{cap.model_id}"


def extract_url(output: dict, *, kind: str = "") -> str | None:
    """Shape-tolerant URL extraction. Apps disagree on the response key."""
    keys = (
        ("video", "video_output", "output", "videos")
        if kind == "video_gen"
        else ("image", "image_output", "output", "images")
    )
    for key in keys:
        val = output.get(key)
        if not val:
            continue
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                for k in ("url", "image_url", "image", "video"):
                    if isinstance(first.get(k), str):
                        return first[k]
        elif isinstance(val, str):
            return val
    return None
