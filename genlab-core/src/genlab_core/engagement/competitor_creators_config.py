"""Per-niche competitor-creator BLOCKLIST for outbound engagement.

Layer 4 audit round 4 policy (2026-07-17). Direct-competitor channels
in the same niche should NEVER receive outbound replies from us:

- Looks like poaching (trying to siphon audience from a business we
  compete with)
- Triggers spam-report flags at higher rates than replies to
  unaffiliated creators
- Damages potential collaboration relationships

The YouTube Data API doesn't cleanly expose "is this creator monetized"
without OAuth — the original audit language ("skip monetized-competitor
videos") requires a proxy signal. Operator-curated channel blocklist is
the cleanest proxy: explicit intent, zero heuristic false-positives, and
composable with future automated competitor detection.

## Mirror of top_creators_config.py

Same shape as ``genlab_core.intel.top_creators_config`` but semantically
INVERSE: top_creators is a POSITIVE-signal watchlist (poll these for
trend signals); competitor_creators is a NEGATIVE-signal blocklist
(skip outbound engagement on these).

## Config file

``genlab-core/config/competitor_creators.yaml`` — starts empty. Operator
adds channel IDs as they identify direct competitors:

    version: 1
    niches:
      ai_creators:
        channels:
          - channel_id: UC_example_competitor_id
            label: Example AI channel
            notes: Runs a similar tutorial format; direct traffic overlap

## Load behavior

- File missing → returns empty result → filter is a no-op (fail-open)
- Config version mismatch → returns empty (fail-safe, log WARNING)
- Unknown niche IDs → dropped with WARNING
- Invalid channel_ids → dropped silently (well-formed check per top_creators)

Called at OutboundTargeting time (per-poll, once per niche) — no hot-path
overhead. Load cost is negligible (<200 entries expected).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_KNOWN_NICHES = {"ai_creators", "gaming", "sports", "movies", "anime"}
_SUPPORTED_VERSION = 1

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "competitor_creators.yaml"


@dataclass(frozen=True)
class CompetitorCreator:
    """One entry in a niche's competitor blocklist.

    Frozen to prevent accidental mutation drift between YAML + runtime
    state (matches TopCreator pattern).
    """

    channel_id: str
    label: str
    notes: str

    def is_valid(self) -> bool:
        """Well-formed YouTube channel IDs start with 'UC' and are
        24 characters long. Reject anything else at load time so the
        blocklist never quietly fails to match a typo'd channel ID."""
        return (
            isinstance(self.channel_id, str)
            and self.channel_id.startswith("UC")
            and len(self.channel_id) == 24
        )


def load_competitor_creators(
    config_path: Path | None = None,
) -> dict[str, set[str]]:
    """Load the per-niche competitor blocklist.

    Returns dict of ``{niche_id: {channel_id, ...}}``. Set (not list)
    for O(1) membership check — the filter fires on every discovered
    video candidate, so lookup cost matters.

    Empty dict when:
    - Config file missing
    - Config version doesn't match _SUPPORTED_VERSION
    - Every niche has empty channels list

    Empty niche entries when a specific niche has no configured
    competitors (default state at ship time).

    All error paths log at WARNING and return empty — no exceptions
    escape to the caller (matches top_creators_config fail-open pattern).
    """
    path = config_path or _DEFAULT_CONFIG_PATH
    if not path.is_file():
        logger.debug(
            "[competitor_creators] config not found at %s — filter is no-op",
            path,
        )
        return {}

    try:
        import yaml

        raw = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[competitor_creators] YAML parse failed: %s", exc)
        return {}

    version = raw.get("version")
    if version != _SUPPORTED_VERSION:
        logger.warning(
            "[competitor_creators] config version %r doesn't match supported %r "
            "— returning empty. Update the loader OR bump the YAML to match.",
            version,
            _SUPPORTED_VERSION,
        )
        return {}

    result: dict[str, set[str]] = {}
    for niche_id, niche_data in (raw.get("niches") or {}).items():
        if niche_id not in _KNOWN_NICHES:
            logger.warning(
                "[competitor_creators] dropping unknown niche %r (not in %s)",
                niche_id,
                sorted(_KNOWN_NICHES),
            )
            continue

        channels: set[str] = set()
        for entry in (niche_data or {}).get("channels") or []:
            if not isinstance(entry, dict):
                continue
            creator = CompetitorCreator(
                channel_id=str(entry.get("channel_id", "")),
                label=str(entry.get("label", "")),
                notes=str(entry.get("notes", "")),
            )
            if creator.is_valid():
                channels.add(creator.channel_id)
            else:
                logger.warning(
                    "[competitor_creators] dropping invalid entry for niche %r: %r "
                    "(channel_id must be 24-char UC-prefixed)",
                    niche_id,
                    entry.get("channel_id"),
                )
        result[niche_id] = channels

    return result


def is_competitor(
    niche_id: str,
    channel_id: str,
    blocklist: dict[str, set[str]] | None = None,
) -> bool:
    """Return True iff ``channel_id`` is on the ``niche_id`` competitor list.

    Convenience wrapper — callers can also just do direct set membership
    check on the blocklist dict.
    """
    if not blocklist:
        return False
    niche_channels = blocklist.get(niche_id, set())
    return channel_id in niche_channels


__all__ = [
    "CompetitorCreator",
    "is_competitor",
    "load_competitor_creators",
]
