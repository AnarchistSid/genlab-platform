"""Kit lookup. Config, never a branch in the renderer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).parent


@dataclass(frozen=True)
class KitSpec:
    family: str
    sports: tuple[str, ...]
    moment: str
    effects: tuple[str, ...]
    palette: dict[str, Any]
    subject: dict[str, Any]
    face_protected: bool
    beat: dict[str, Any]
    audio: dict[str, Any]
    source_preference: tuple[str, ...]
    rights: dict[str, Any]
    status: str
    notes: str = ""

    @property
    def second_person_is_noise(self) -> bool:
        """Combat: the other fighter is noise to be excluded from the matte.
        Racing: the other car IS the moment -- never place a negative on it."""
        return bool(self.subject.get("second_person_is_noise", True))

    @property
    def footage_allowed(self) -> bool:
        """Some rights holders are hostile enough that the template must not
        touch their footage at all, however good the effect would look."""
        return bool(self.rights.get("footage_allowed", True))


def _load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def load_kit(family: str) -> KitSpec:
    p = _HERE / f"{family}.yaml"
    if not p.exists():
        raise KeyError(f"no kit for family {family!r} ({p})")
    d = _load_yaml(p)
    return KitSpec(
        family=d["family"],
        sports=tuple(d.get("sports", ())),
        moment=d.get("moment", ""),
        effects=tuple(d.get("effects", ())),
        palette=d.get("palette", {}),
        subject=d.get("subject", {}),
        face_protected=bool(d.get("face_protected", True)),
        beat=d.get("beat", {}),
        audio=d.get("audio", {}),
        source_preference=tuple(d.get("source_preference", ())),
        rights=d.get("rights", {}),
        status=d.get("status", "planned"),
        notes=d.get("notes", ""),
    )


KIT_BY_FAMILY = {p.stem: p for p in sorted(_HERE.glob("*.yaml"))}

_SPORT_TO_FAMILY: dict[str, str] = {}
for _fam in KIT_BY_FAMILY:
    for _s in _load_yaml(_HERE / f"{_fam}.yaml").get("sports", ()):
        _SPORT_TO_FAMILY[_s.lower()] = _fam


def family_for_niche(sport: str) -> str | None:
    """Map a sport name to its family. Unknown sports return None rather than
    guessing -- a wrong kit is worse than no kit."""
    return _SPORT_TO_FAMILY.get((sport or "").strip().lower())
