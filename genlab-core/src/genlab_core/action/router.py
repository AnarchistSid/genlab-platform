"""Which treatment a candidate gets. Provenance decides; the classifier watches.

RENDER-01 Part 5 section 3.

WHY PROVENANCE AND NOT THE CLASSIFIER
-------------------------------------
The content classifier was measured on a 27-clip corpus of live production
fetches and tops out at **18/27 = 67%** after an exhaustive grid search over all
five of its thresholds and both orderings. Three scalars -- motion, speech,
faces -- do not separate ACTION from TALK on real material: an F1 onboard scores
0.88 levels/s because the camera is smooth, a tech review with b-roll scores
7.12, and the ACTION range 0.88-16.76 sits INSIDE the TALK range 0.10-7.12.

Provenance already carries the answer and carries it for free. The fetcher knows
whether a candidate came back from a highlights query, whether the channel is
official, whether there is any footage at all. That metadata is not a guess
about the pixels; it is what the pixels ARE.

So: provenance routes, the classifier rides along and is scored against it. Its
agreement rate per fire is the corpus growing on live material, labelled by
provenance -- which is exactly the labelled data it needs before it can become a
learned model on frames rather than three scalars.

THE CLASSIFIER DOES NOT ROUTE. If that ever changes it should be because its
agreement rate earned it, visible in the numbers this module reports.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from genlab_core.action.kits.registry import family_for_niche

logger = logging.getLogger(__name__)

#: A candidate with no footage cannot be ACTION or TALK whatever else it looks
#: like -- the caption engine and motion-on-stills carry it.
NO_FOOTAGE = ("", None)

#: Provenance that means "someone is talking to a camera". Matched against the
#: title and the source URL, not against the pixels.
TALK_PATTERNS = (
    r"\bpress\s*conference\b",
    r"\bpresser\b",
    r"\binterview\b",
    r"\bexclusive:",
    r"\breacts?\b",
    r"\bspeaks?\b",
    r"\bexplains?\b",
    r"\bbreaks? down\b",
    r"\bq\s*&\s*a\b",
    r"\bpost-?match\b",
    r"\bpost-?game\b",
    r"\bpreview\b",
    r"\banalysis\b",
    r"\bfirst look\b",
    r"\breview\b",
)

#: Reddit-sourced items are overwhelmingly reaction/commentary clips rather than
#: broadcast footage, and the fetcher records the host.
TALK_HOSTS = ("reddit.com", "v.redd.it", "redd.it")

SOURCE_SCORE_FLOOR = 0.45


@dataclass
class RouteDecision:
    treatment: str
    reason: str
    kit_family: str | None = None
    source_score: float | None = None
    classifier_verdict: str | None = None
    classifier_agrees: bool | None = None
    signals: dict = field(default_factory=dict)

    def as_storyboard_note(self) -> dict:
        """What gets persisted beside the plan, so a fire can be re-read later."""
        return {
            "routed_treatment": self.treatment,
            "routed_by": "provenance",
            "route_reason": self.reason,
            "kit_family": self.kit_family,
            "source_score": self.source_score,
            "classifier_verdict": self.classifier_verdict,
            "classifier_agrees": self.classifier_agrees,
            "classifier_signals": self.signals,
        }


def _looks_like_talk(candidate: dict) -> str | None:
    url = (candidate.get("source_url") or candidate.get("download_url") or "").lower()
    for host in TALK_HOSTS:
        if host in url:
            return f"source host {host}"
    title = (candidate.get("title") or "").lower()
    for pat in TALK_PATTERNS:
        if re.search(pat, title):
            return f"title matches /{pat}/"
    return None


def route(
    candidate: dict,
    *,
    source_score: float | None = None,
    classifier_verdict: str | None = None,
    classifier_signals: dict | None = None,
    sport: str | None = None,
) -> RouteDecision:
    """Decide the treatment from PROVENANCE. The classifier is advisory only."""
    signals = dict(classifier_signals or {})

    def finish(treatment: str, reason: str) -> RouteDecision:
        agrees = None if classifier_verdict is None else (classifier_verdict == treatment)
        d = RouteDecision(
            treatment=treatment,
            reason=reason,
            kit_family=family_for_niche(sport or candidate.get("sport") or ""),
            source_score=source_score,
            classifier_verdict=classifier_verdict,
            classifier_agrees=agrees,
            signals=signals,
        )
        logger.info(
            "[router] %s -> %s (%s); classifier said %s (%s)",
            candidate.get("video_id", "?"),
            treatment,
            reason,
            classifier_verdict or "n/a",
            "agrees" if agrees else ("DISAGREES" if agrees is False else "n/a"),
        )
        return d

    # 1. No footage is decisive and comes first: nothing about a title or a
    #    score can make a blueprint with no clip into ACTION.
    clip = candidate.get("clip_path") or candidate.get("download_url")
    if clip in NO_FOOTAGE or candidate.get("footage_free"):
        return finish("STILL", "no footage — caption engine carries it")

    # 2. Talk provenance beats highlight flags: a post-match interview posted by
    #    an official channel is still someone at a microphone.
    talk_why = _looks_like_talk(candidate)
    if talk_why:
        return finish("TALK", talk_why)

    # 3. ACTION needs BOTH the highlight flag and a source the template can use.
    #    is_highlight alone let in wide hard-cam footage that the ACTION template
    #    cannot frame; source_score is the measurement that catches it.
    if candidate.get("is_highlight"):
        if source_score is None:
            return finish("ACTION", "is_highlight (source unscored)")
        if source_score >= SOURCE_SCORE_FLOOR:
            return finish("ACTION", f"is_highlight and source_score {source_score:.2f}")
        return finish(
            "STILL",
            f"is_highlight but source_score {source_score:.2f} < {SOURCE_SCORE_FLOOR} — "
            "the template cannot frame this clip",
        )

    # 4. Footage that is neither flagged nor talk-shaped. STILL is the safe
    #    landing: its treatment works on any footage, where ACTION on a lectern
    #    produces a fight that is not there.
    return finish("STILL", "no highlight flag and no talk provenance")


def agreement_report(decisions: list[RouteDecision]) -> dict:
    """Classifier-vs-provenance agreement for one fire.

    This is the number that decides whether the classifier ever earns the
    routing job, and it accumulates labelled live material as a side effect.
    """
    scored = [d for d in decisions if d.classifier_agrees is not None]
    if not scored:
        return {"n": 0, "agreement": None, "by_treatment": {}}
    by: dict[str, dict[str, int]] = {}
    for d in scored:
        row = by.setdefault(d.treatment, {"n": 0, "agree": 0})
        row["n"] += 1
        row["agree"] += int(bool(d.classifier_agrees))
    return {
        "n": len(scored),
        "agreement": round(sum(d.classifier_agrees for d in scored) / len(scored), 4),
        "by_treatment": by,
        "disagreements": [
            {
                "video_id": d.signals.get("video_id"),
                "routed": d.treatment,
                "classifier": d.classifier_verdict,
                "reason": d.reason,
            }
            for d in scored
            if not d.classifier_agrees
        ][:20],
    }
