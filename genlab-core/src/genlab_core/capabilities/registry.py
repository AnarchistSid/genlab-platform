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
  "$0.0714/credit", which is not a unit anyone can budget against; it
  advertised $0.07-$14.29 on a 3-second clip and settled at $1.29.
* A mid-run balance delta is a RESERVATION HOLD at the top of the estimate,
  not a charge. Reading it as spend overstates by an order of magnitude.

An app with no measured cost is ``unmeasured`` and cannot be selected for
production, however cheap its catalog entry looks.

## Rights

``owned_channels`` --- cleared for the five channels we publish.
``eval_only``      --- usable in evaluation, never selectable for production.
``unread``         --- terms not yet read; treated as eval_only at selection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    }
)

Rights = Literal["owned_channels", "eval_only", "unread"]


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
    constraints: dict = field(default_factory=dict)
    composite_safe: bool = True
    eval_only: bool = False
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
        notes="Draft mode is the measured unit. 250 s for 1 s of video — batch offline, never in a fire.",
    ),
    Capability(
        ref="topaz/astra",
        kind="upscale",
        rights="owned_channels",
        constraints={"scale": [1.0, 4.0], "probe_cap_s": 1},
        notes="Prices in CREDITS, so the catalog gives no per-unit figure. Capped probe per the >10x rule.",
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


def select(kind: str, *, production: bool = True) -> Capability:
    """The cheapest entry of ``kind`` that may actually be used.

    ``production=True`` (the default) excludes ``eval_only``, ``unread``
    rights, and anything unmeasured. There is no "fall back to the catalog
    price" path: an unmeasured app is unselectable, because the number that
    would rank it does not exist.
    """
    entries = for_kind(kind)
    usable = [c for c in entries if c.selectable_for_production] if production else list(entries)
    if not usable:
        why = ", ".join(
            f"{c.ref}(measured={c.measured}, eval_only={c.eval_only}, rights={c.rights})"
            for c in entries
        ) or "no entries at all"
        raise Unselectable(f"no production-selectable entry for kind {kind!r}: {why}")
    return usable[0]
