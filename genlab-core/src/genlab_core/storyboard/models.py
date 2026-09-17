"""The storyboard: what the renderer will do, decided before a frame is drawn.

ARCH-02 §2, in its smallest useful form. Every decision the craft treatments
make -- which treatment, which window, which subject, where the beats fall, what
happens on each shot -- is settled here, typed, and persisted on the blueprint.
The renderer then EXECUTES it rather than deciding as it goes.

Two things this buys that a renderer-decides-inline design cannot:

* **Gates that run on the plan.** Shot count, longest static run, hook onset,
  plate-once, flash-at-finish are all properties of the PLAN. Checking them
  before rendering turns a 12-minute render plus a rejection into a sub-second
  rejection. Every one of these was learned by rendering something and then
  measuring that it was wrong.
* **A record of why.** Each decision carries the measurement that produced it,
  so a reel that comes out wrong can be traced to the number that misled,
  instead of to a guess about which stage misbehaved.

Persisted to ``blueprints.extra['storyboard']``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Scope(StrEnum):
    """A REEL is the published artifact; a SEGMENT is one window inside it.

    The distinction is not cosmetic. Cut density is a REEL property: the ACTION
    window selector requires ZERO CUTS, so a segment is one continuous shot by
    construction and gating it on cuts/s would reject every correctly-chosen
    window. The approved hit3s is exactly that -- 3.2s, one shot, on purpose.
    """

    REEL = "REEL"
    SEGMENT = "SEGMENT"


class Treatment(StrEnum):
    TALK = "TALK"
    ACTION = "ACTION"
    STILL = "STILL"


class ClassifierVerdict(BaseModel):
    """Why this clip got this treatment. Signals, not just the label."""

    treatment: Treatment
    speech_ratio: float = Field(ge=0.0, le=1.0)
    motion_energy: float = Field(ge=0.0)
    face_persistence: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class WindowPlan(BaseModel):
    """The segment, and the three numbers that chose it."""

    start_s: float = Field(ge=0.0)
    duration_s: float = Field(gt=0.0)
    motion: float
    cuts: int = Field(ge=0)
    subject_hold: float = Field(ge=0.0, le=1.0)
    source_rect: tuple[int, int, int, int] | None = None  # chrome removed
    finish_frame: int | None = None


class SubjectPlan(BaseModel):
    """Who the effects read. Decided once, held for the window."""

    hue_deg: float | None = None
    votes: int = Field(ge=0)
    vote_frames: int = Field(ge=0)
    unanimous: bool = False
    reason: str = ""

    @property
    def margin(self) -> float:
        return self.votes / self.vote_frames if self.vote_frames else 0.0


class BeatGrid(BaseModel):
    """Bed first, then beats, then events. ACTION is music-led."""

    bpm: float = Field(gt=0.0)
    first_beat_s: float = Field(ge=0.0)
    period_frames: float = Field(gt=0.0)
    fps: int = Field(gt=0)

    def beat_frames(self, n_frames: int) -> list[int]:
        out, f = [], self.first_beat_s * self.fps
        while f < n_frames:
            out.append(int(round(f)))
            f += self.period_frames
        return out


class EffectEvent(BaseModel):
    kind: str
    frame: int = Field(ge=0)
    detail: str = ""
    on_beat: bool = False


class Shot(BaseModel):
    """One continuous piece of the segment, with its own magnification."""

    index: int = Field(ge=0)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    magnification: float = Field(gt=0.0)
    centre_x: float = Field(ge=0.0, le=1.0)
    centre_y: float = Field(ge=0.0, le=1.0)
    source_frame: int | None = None

    @property
    def n_frames(self) -> int:
        return max(self.end_frame - self.start_frame + 1, 0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_frame < self.start_frame:
            raise ValueError(f"shot {self.index}: end {self.end_frame} < start {self.start_frame}")
        return self


class Storyboard(BaseModel):
    """The whole plan. The renderer executes this and decides nothing."""

    version: Literal[1] = 1
    niche_id: str
    scope: Scope = Scope.REEL
    blueprint_id: str = ""
    fps: int = Field(default=30, gt=0)
    total_frames: int = Field(gt=0)
    classifier: ClassifierVerdict
    kit: str = ""
    window: WindowPlan | None = None
    subject: SubjectPlan | None = None
    beats: BeatGrid | None = None
    shots: list[Shot] = Field(default_factory=list)
    events: list[EffectEvent] = Field(default_factory=list)
    full_bleed_floor: float | None = None
    grade: dict[str, float] = Field(default_factory=dict)
    drawing: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.total_frames / self.fps

    def treatment_is(self, t: Treatment) -> bool:
        return self.classifier.treatment is t

    def events_of(self, kind: str) -> list[EffectEvent]:
        return [e for e in self.events if e.kind == kind]
