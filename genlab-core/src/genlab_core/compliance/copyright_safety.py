"""Copyright safety primitives (PR #568, 2026-06-25).

Phase A step 3 — the most critical compliance check for our use case.
We publish reels using third-party YouTube clips (gaming highlights,
movie trailers, anime scenes, etc.) under fair-use framing
(transformation: 1080×1920 reframe + logo overlay + AI commentary in
caption). YouTube's Content ID is the most aggressive enforcement
system we face:

  * Automated audio + video fingerprint matching at upload time
  * Match → demonetization for rights-holder (mild), geographic
    block (medium), or copyright strike (severe — 3 strikes =
    channel termination)
  * Meta DMCA detection is similar but slower (~24h)
  * TikTok bans aggressively on music, less on video

Defenses (in increasing order of impact):
  1. Source attribution in caption/description — establishes fair-use
     intent ("Footage: @originalcreator") even when transformation
     doesn't reach the rights holder's threshold
  2. Transformation already present — logo overlay, AI commentary,
     1080×1920 reframe, ≤60s duration
  3. Perceptual-hash deny-list (future PR) — block reels whose phash
     is within Hamming distance N of a previously-flagged clip

This PR ships items #1 + the registered policy check for missing
attribution. Item #3 is deferred to a follow-up that needs a phash
column on blueprints + Content ID claim ingestion.

## Public surface

  derive_source_url(video_id, source) -> str | None
    Translate (video_id, source) to a canonical URL. YouTube only
    today — others (twitch, tiktok, reddit) return None pending
    per-platform implementations. The URL form for YouTube is
    `https://youtube.com/watch?v={video_id}` (canonical, shortest,
    and what Content ID's automated systems index against).

  check_copyright_attribution(blueprint, platform, niche_id)
      -> ComplianceDecision
    Registered into policy_gate at module import time. Fires
    'warn' (Phase A observation-only) when a YouTube-source
    blueprint has no derivable source URL AND no source_url /
    source_creator field. Other platforms get 'allow' by default
    pending future per-platform implementations.

  format_youtube_attribution(blueprint) -> str
    The caption-append text for YouTube descriptions. Idempotent
    (substring match), empty if no source URL derivable. PR #568
    wires this into payload_builder's YouTube branch.

## Registration side-effect

Importing this module REGISTERS the copyright_attribution_check
into policy_gate. The publisher's first call to
check_publish_policy(...) AFTER this module is imported will run
this check + log copyright_flag events to compliance_events.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from genlab_core.compliance.events import ComplianceDecision
from genlab_core.compliance.policy_gate import register_check

logger = logging.getLogger(__name__)

# Source-name → URL-template mapping. Keys match the `source` column
# values produced by upstream fetchers. Adding a source = adding an
# entry here.
_SOURCE_URL_TEMPLATES: dict[str, str] = {
    "youtube_trending": "https://youtube.com/watch?v={video_id}",
    "youtube_search": "https://youtube.com/watch?v={video_id}",
    "youtube_playlist": "https://youtube.com/watch?v={video_id}",
    "youtube_rss": "https://youtube.com/watch?v={video_id}",
    "youtube": "https://youtube.com/watch?v={video_id}",
}


def derive_source_url(video_id: str | None, source: str | None) -> str | None:
    """Derive a canonical source URL from (video_id, source).

    Returns the URL when:
      * video_id is non-empty
      * source maps to a known URL template (YouTube family today)

    Returns None for:
      * Empty video_id
      * Unknown source (twitch, tiktok, reddit — pending future
        per-platform templates)
      * Either input is None

    The check_copyright_attribution gate uses None to fire WARN
    (no derivable attribution); the YouTube description wire uses
    None to skip the attribution append.
    """
    if not video_id or not source:
        return None
    template = _SOURCE_URL_TEMPLATES.get(source.lower())
    if not template:
        return None
    return template.format(video_id=video_id)


def format_youtube_attribution(blueprint: Mapping) -> str:
    """Return the caption-append text for a YouTube description, or ''.

    Idempotent — caller can pass an already-attributed caption + the
    helper will return the same suffix; substring match in the
    caller's wire prevents double-append.

    The format is intentionally short ("Footage: <url>") + on its own
    line — Content ID's text-classifier (and copyright rights-holders'
    review staff) prefer clear, parseable attribution over verbose
    legal language. The URL points to the canonical creator's video,
    establishing fair-use intent under the four-factor analysis.
    """
    url = derive_source_url(
        blueprint.get("video_id") if isinstance(blueprint, Mapping) else None,
        blueprint.get("source") if isinstance(blueprint, Mapping) else None,
    )
    if not url:
        return ""
    return f"\n\nFootage: {url}"


def format_source_attribution(blueprint: Mapping) -> str:
    """Return a human-readable creator credit line, or ''.

    Prefers the source uploader's channel name when known::

        \U0001f3ac Original: @{channel} — {url}

    Falls back to URL-only when no channel field is populated::

        \U0001f3ac Original: {url}

    Returns "" when no source URL is derivable.

    This is the audience-facing sibling of
    ``format_youtube_attribution``. Where that helper produces the
    compact ``"Footage: <url>"`` line optimised for Content ID's
    text-classifier, this one produces a viewer-readable credit line
    intended for every platform caption (FB, IG, Threads, YT
    description). Reads ``source_channel_title`` first, then falls
    back to ``channel_name`` / ``channel_title`` for compatibility
    with the fetcher's field naming.
    """
    if not isinstance(blueprint, Mapping):
        return ""
    url = derive_source_url(
        blueprint.get("video_id"),
        blueprint.get("source"),
    )

    # PR #TwitchAttribution (2026-07-11, post-Markanimation Layer 5
    # finding): the Twitch fetcher emits source="twitch_clips" with
    # a pre-formed ``source_url`` / ``video_url`` field (Twitch clip
    # URLs contain the broadcaster slug + clip slug — not derivable
    # from a single ID like YouTube). The YouTube-family template
    # dispatcher returns None for twitch_clips, which used to zero
    # the credit line for the entire gaming niche (Layer 5 showed
    # 0% attribution on 2026-07-11 despite gaming publishing 8 posts
    # from Twitch). This fallback reads the pre-formed URL directly
    # so Twitch-sourced posts get "\U0001F3AC Original: @broadcaster
    # — {clip_url}" without needing per-broadcaster URL templates.
    if not url:
        source = (blueprint.get("source") or "").lower()
        if "twitch" in source:
            candidate = (
                blueprint.get("video_url")
                or blueprint.get("source_url")
                or blueprint.get("canonical_url")
                or ""
            )
            candidate = str(candidate).strip()
            if candidate and candidate.startswith(("http://", "https://")):
                url = candidate

    if not url:
        return ""
    channel = (
        blueprint.get("source_channel_title")
        or blueprint.get("channel_name")
        or blueprint.get("channel_title")
        # Twitch fetcher uses "broadcaster" — same intent, different name.
        # Ordered last so YouTube's source_channel_title still wins when
        # both fields are somehow populated on the same blueprint.
        or blueprint.get("broadcaster")
        or ""
    )
    channel = str(channel).strip()
    if channel:
        return f"\U0001f3ac Original: @{channel} — {url}"
    return f"\U0001f3ac Original: {url}"


def check_copyright_attribution(
    blueprint: Mapping,
    platform: str,
    niche_id: str,
) -> ComplianceDecision:
    """Registered policy_gate check for missing source attribution.

    Fires:
      * 'allow' for non-YouTube platforms (their copyright enforcement
        is less aggressive; future PRs may add per-platform checks)
      * 'allow' when blueprint has a derivable source URL
      * 'allow' when blueprint has an explicit source_url field
        (operator-overrides)
      * 'warn' when YouTube publish AND no derivable source URL AND
        no source_url field → 'missing_source_attribution' reason

    Phase A is observation-only — the gate currently logs 'warn' which
    doesn't block publish. PR #570's enforcement toggle (per-niche
    flag) will let operators flip this to 'block' once they're
    confident the false-positive rate is low.
    """
    if not isinstance(blueprint, Mapping):
        return ComplianceDecision(decision="allow")

    # Non-YouTube platforms get 'allow' today. Future per-platform
    # implementations (Meta DMCA-style checks) can specialise.
    if (platform or "").lower() != "youtube":
        return ComplianceDecision(decision="allow")

    derived = derive_source_url(
        blueprint.get("video_id"),
        blueprint.get("source"),
    )
    explicit = (blueprint.get("source_url") or "").strip()

    if derived or explicit:
        return ComplianceDecision(decision="allow")

    # PR #Layer3 (2026-07-11, post-Markanimation): the policy gate can
    # now escalate from 'warn' to 'block' via an env-flag opt-in. Default
    # remains 'warn' so shipping this PR is a no-op until an operator
    # sets ``GENLAB_ATTRIBUTION_LAYER3_ENFORCE=1``. YouTube is the only
    # platform that flips today because Content ID enforces at upload
    # time (instant strike risk); Meta's 24h Rights Manager sweeps are
    # slower and covered separately by fb_survival_check monitoring.
    #
    # Rollout gate: enable after 1-2 weeks of observation via the Layer 5
    # attribution_health dashboard card, once ``attribution_present_pct``
    # holds ≥98% per niche. Setting the flag before then risks blocking
    # the daily YT publish if any Layer 1/2 escape hatch fires.
    _enforce_layer3 = os.environ.get("GENLAB_ATTRIBUTION_LAYER3_ENFORCE", "0") == "1"
    _decision = "block" if _enforce_layer3 else "warn"

    return ComplianceDecision(
        decision=_decision,
        reasons=["missing_source_attribution"],
        metadata={
            "video_id_present": bool(blueprint.get("video_id")),
            "source": blueprint.get("source") or "",
            "layer3_enforce": _enforce_layer3,
        },
    )


# Register the check at module import time. Idempotent — re-importing
# the module just overrides with the same fn (per PR #566's
# register_check contract).
register_check("copyright_attribution_check", check_copyright_attribution)
