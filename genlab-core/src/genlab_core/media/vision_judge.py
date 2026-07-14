"""Pre-publish frame quality gate using Claude vision (Lever G2).

Sibling of ``media/video_enrichment.py`` (Lever G1) but solves the
inverse problem:

- **G1 (enrichment)**: Gemini describes WHAT is in a video so the writer
  can build a story-grounded hook. Output is descriptive metadata.

- **G2 (this module, judge)**: Claude looks at a single chosen frame
  + the hook text and decides IS THIS PUBLISHABLE? Output is a verdict
  the dashboard can gate on or surface as a quality badge.

Why a separate module: enrichment is per-clip (~once per fetch), judge
is per-render (~once per blueprint). They have different cost profiles,
different failure modes (Gemini timeout vs Claude vision content-block),
and different consumers. Bundling would couple their release cadences.

Operator-facing value: today operators eyeball every rendered reel
before approval (cognitive load = highest-cost step in the pipeline).
G2's verdict lets the dashboard show "Vision judge: 7/10, logo
obscured" so the operator focuses review on flagged frames instead of
scanning all 5. Combined with Lever C (LLM-judge auto-approval), high-
quality frames + high gate confidence can eventually auto-publish.

Opt-in via ``GENLAB_VISION_JUDGE_ENABLED=1``. Default disabled = no
behavior change and zero baseline cost. Pattern matches Lever C / K /
O / G1 — opt-in env flag, fail-open None, pure-logic split, whitelisted
enum for downstream aggregation cleanliness.

Cost: ~1 Haiku 4.5 vision call per invocation (~$0.001 with the image
attached). Caller decides when to invoke (typically per blueprint at
render-complete time), so cost is bounded by daily publish cap = 1
reel × 5 niches = ~$0.005/day at full activation.

Run via:
    python -m genlab_core.media.vision_judge --help
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


# Whitelisted issue tags. Free-text LLM creativity would poison
# downstream aggregations ("47% of bombs had `logo_obscured`" only
# works if the set is closed). Categories chosen to map to operator
# remediations: rerender (logo/contrast), reframe (subject), retext
# (hook unreadable), retry-with-different-clip (mismatch/NSFW).
_KNOWN_ISSUES: Final[frozenset[str]] = frozenset(
    {
        "logo_obscured",
        "hook_unreadable",
        "low_contrast",
        "off_center_subject",
        "platform_ui_overlap",
        "thumbnail_clickbait_mismatch",
        "nsfw_risk",
    }
)

# Max image bytes accepted before we refuse to send to the API. Claude's
# vision endpoint accepts ~5MB; we cap at 4MB to keep request budget
# predictable and avoid rejection from the API side.
_MAX_FRAME_BYTES: Final[int] = 4 * 1024 * 1024


@dataclass(frozen=True)
class VisionVerdict:
    """Structured verdict from the vision judge layer.

    The ``quality_score`` is on a 0..10 scale so dashboards can map it
    to a familiar UI metaphor (5-star rating, traffic-light gauge).
    The ``issues`` list uses tags from ``_KNOWN_ISSUES`` only — unknown
    tags returned by the LLM are filtered out (not coerced) so aggregate
    queries stay clean.
    """

    blueprint_id: str
    quality_score: float  # 0..10
    issues: list[str]  # subset of _KNOWN_ISSUES
    recommendation: str  # one-sentence operator action
    confidence: float  # 0..1, LLM self-reported
    computed_at: str  # ISO timestamp


def is_enabled() -> bool:
    """Single source of truth for the opt-in flag.

    Centralizing the env read here so callers can short-circuit cheaply
    (``if not is_enabled(): return None``) without duplicating the
    "0"/"1" check. Tests that toggle the flag only need to mock this
    one function via monkeypatch.
    """
    from genlab_core.settings import env_true

    return env_true("GENLAB_VISION_JUDGE_ENABLED")


def load_frame_bytes(frame_path: str | Path) -> bytes | None:
    """Load image bytes from disk, enforcing the size cap.

    Pure I/O — split from ``judge_frame`` so the LLM call can be
    tested without writing a real image to disk. Returns None on
    missing file or oversized frame.
    """
    p = Path(frame_path)
    if not p.is_file():
        logger.debug("[vision_judge] frame missing: %s", p)
        return None
    try:
        data = p.read_bytes()
    except OSError as exc:
        logger.debug("[vision_judge] frame read failed: %s — %s", p, exc)
        return None
    if len(data) > _MAX_FRAME_BYTES:
        logger.debug(
            "[vision_judge] frame too large: %d bytes (cap %d)",
            len(data),
            _MAX_FRAME_BYTES,
        )
        return None
    return data


def build_judge_prompt(hook_text: str, niche_id: str) -> str:
    """Construct the user-prompt text that accompanies the image.

    Pure function (no I/O, no env reads). Splitting prompt construction
    out lets us test the prompt shape directly without mocking the
    Anthropic client. Niche-aware so the judge knows what 'good' looks
    like per niche (gaming clip vs movie trailer vs anime fight scene).
    """
    hook = (hook_text or "(no hook provided)")[:240]
    niche = (niche_id or "(unspecified)")[:32]
    return (
        f"You are judging the publish-readiness of a single video frame.\n\n"
        f"Niche: {niche}\n"
        f"Hook text overlaid on the frame: {hook}\n\n"
        "Inspect the attached image and answer four questions:\n"
        "  1. Is the channel logo visible AND uncropped?\n"
        "  2. Is the hook text legible (size, contrast vs background)?\n"
        "  3. Is the main subject centered + on-message for the niche?\n"
        "  4. Would the chosen frame survive Instagram/YouTube/TikTok's UI\n"
        "     overlays (bottom-third caption strip, top-right share button)?\n\n"
        "Return ONLY a JSON object:\n"
        "{\n"
        '  "quality_score": <number 0..10>,\n'
        '  "issues": [<subset of: "logo_obscured", "hook_unreadable",\n'
        '              "low_contrast", "off_center_subject",\n'
        '              "platform_ui_overlap", "thumbnail_clickbait_mismatch",\n'
        '              "nsfw_risk">],\n'
        '  "recommendation": "<one-sentence operator action>",\n'
        '  "confidence": <number 0..1>\n'
        "}\n\n"
        "Be strict — a 7/10 is publishable, a 5/10 should be regenerated."
    )


def judge_frame(
    frame_path: str | Path,
    blueprint_id: str,
    hook_text: str = "",
    niche_id: str = "",
) -> VisionVerdict | None:
    """Run the Claude vision judge on a single frame.

    Returns ``None`` when:
    - ``GENLAB_VISION_JUDGE_ENABLED`` is not "1"
    - No ANTHROPIC_API_KEY
    - Frame missing / unreadable / too large
    - LLM call fails (network, parsing, content-block, etc.)

    Fail-open by design: caller treats a None verdict as "no signal"
    and proceeds with the default operator-review flow. A failure here
    must NEVER block publishing or downstream learning.

    Cost: ~$0.001 per invocation (Haiku 4.5 with vision). Caller
    decides when to invoke.
    """
    if not is_enabled():
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    frame_bytes = load_frame_bytes(frame_path)
    if frame_bytes is None:
        return None

    # Best-effort media-type detection from extension. The Anthropic
    # SDK requires this for image source blocks — wrong type = 400.
    ext = Path(frame_path).suffix.lower().lstrip(".")
    media_type = {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    encoded = base64.standard_b64encode(frame_bytes).decode("ascii")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            temperature=0.0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded,
                            },
                        },
                        {
                            "type": "text",
                            "text": build_judge_prompt(hook_text, niche_id),
                        },
                    ],
                }
            ],
        )

        # Cost tracking — must never block the verdict
        try:
            from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

            record_anthropic_usage("claude-haiku-4-5-20251001", response)
        except Exception:
            pass

        raw = response.content[0].text.strip() if response.content else ""
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        parsed = json.loads(raw)

        # Quality score: clamp to [0, 10], coerce non-numeric to 0.0
        try:
            score = float(parsed.get("quality_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(10.0, score))

        # Issues: filter out unknown tags so aggregations stay clean.
        # Unknown tag != error — the LLM is allowed creativity but
        # downstream consumers see only the closed set.
        raw_issues = parsed.get("issues", []) or []
        if not isinstance(raw_issues, list):
            raw_issues = []
        issues = [str(t) for t in raw_issues if str(t) in _KNOWN_ISSUES]

        # Confidence: clamp to [0, 1]
        try:
            confidence = float(parsed.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        recommendation = str(parsed.get("recommendation", "")).strip()[:240]
        if not recommendation:
            recommendation = "no_recommendation"

        logger.info(
            "[vision_judge] bp=%s score=%.1f issues=%s conf=%.2f",
            blueprint_id,
            score,
            ",".join(issues) or "none",
            confidence,
        )

        return VisionVerdict(
            blueprint_id=blueprint_id,
            quality_score=round(score, 2),
            issues=issues,
            recommendation=recommendation,
            confidence=round(confidence, 3),
            computed_at=datetime.now(UTC).isoformat(),
        )
    except (json.JSONDecodeError, KeyError, AttributeError, IndexError) as exc:
        logger.debug("[vision_judge] malformed LLM response: %s", exc)
        return None
    except Exception as exc:
        # API error, network, content-block, etc. — fail open.
        logger.debug("[vision_judge] LLM call failed: %s", exc)
        return None


if __name__ == "__main__":
    # CLI entry — operators can sanity-check enable state without
    # writing Python. Programmatic use is via judge_frame() directly.
    import argparse

    parser = argparse.ArgumentParser(description="Vision judge for pre-publish frames (Lever G2)")
    parser.add_argument(
        "--enabled-check", action="store_true", help="Print whether opt-in env is set"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.enabled_check:
        print(f"GENLAB_VISION_JUDGE_ENABLED: {is_enabled()}")
        if not is_enabled():
            print("Set GENLAB_VISION_JUDGE_ENABLED=1 to activate the vision judge layer.")
        else:
            print(f"Known issue tags: {sorted(_KNOWN_ISSUES)}")
    else:
        print("Use --enabled-check to verify opt-in env state.")
        print("Programmatic usage:")
        print("  from genlab_core.media.vision_judge import judge_frame")
