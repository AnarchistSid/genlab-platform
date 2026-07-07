"""Top-creator watchlist config loader (A.1, 2026-07-07).

Loads ``genlab-core/config/top_creators.yaml`` — the per-niche list of
YouTube channel IDs whose UPLOAD TIMING serves as a leading indicator
for what topics are about to trend. See the YAML file's header for
selection criteria and cost model.

## Design non-goals

* **Not a style-copy pipeline.** The bandit trains on GenLab's own
  reward tuples; copying a top creator's hook doesn't transfer their
  audience. See the "correlation ≠ causation" note in
  ``[[monetization-gap-analysis-2026-07-07]]`` and adjacent memory.
* **Not a scraper.** The consumer runner (A.2) uses the standard
  YouTube Data API — no headless-browser scraping, no undocumented
  endpoints. Playlist ``uploads`` is a public API surface.
* **Not niche-inferring.** Channels are hand-tagged to niches by the
  operator; the module never tries to guess a creator's niche from
  video content.

## Fail-open contract

Missing config file → return empty channels dict. This mirrors the
discipline established by ``catalog_loader.py`` and the L3 sprint's
gitignored-config pattern — the module SHOULD be safe to import
regardless of whether the YAML is provisioned yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "top_creators.yaml"

# Config schema version this loader understands. Bumping this in the
# YAML without updating the loader will produce a warning + empty
# result. See tests/intel/test_top_creators_config.py for the
# version-mismatch pin.
_SUPPORTED_VERSION = 1

# The 5 niches Gen Lab operates. Config entries for unknown niches
# are dropped with a warning — better than silently registering
# watchers for a niche the pipeline doesn't publish to.
_KNOWN_NICHES = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})


@dataclass(frozen=True)
class TopCreator:
    """One entry in a niche's watchlist.

    Frozen because these get held in memory across the daily poll —
    accidental mutation would drift the config away from the YAML.
    """

    channel_id: str
    label: str
    notes: str

    def is_valid(self) -> bool:
        """A well-formed YouTube channel ID starts with 'UC' and is
        24 characters long. Reject anything else at load time so the
        runner never issues bogus API calls."""
        return (
            isinstance(self.channel_id, str)
            and self.channel_id.startswith("UC")
            and len(self.channel_id) == 24
        )


def load_top_creators(
    config_path: Path | None = None,
) -> dict[str, list[TopCreator]]:
    """Load the per-niche watchlist.

    Args:
        config_path: Optional override. Defaults to
            ``genlab-core/config/top_creators.yaml``.

    Returns:
        Dict of ``{niche_id: [TopCreator, ...]}``. Empty dict when the
        file is missing OR the version doesn't match. Empty niche
        lists when a niche has no valid channels.

    All error paths log at WARNING and return the empty result — no
    exceptions escape to the caller.
    """
    path = config_path or _DEFAULT_CONFIG_PATH
    if not path.is_file():
        logger.debug(
            "[top_creators] config not found at %s — returning empty",
            path,
        )
        return {}

    try:
        import yaml

        raw = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[top_creators] YAML parse failed: %s", exc)
        return {}

    version = raw.get("version")
    if version != _SUPPORTED_VERSION:
        logger.warning(
            "[top_creators] config version %r doesn't match supported %r "
            "— returning empty. Update the loader OR bump the YAML to match.",
            version,
            _SUPPORTED_VERSION,
        )
        return {}

    result: dict[str, list[TopCreator]] = {}
    for niche_id, niche_data in (raw.get("niches") or {}).items():
        if niche_id not in _KNOWN_NICHES:
            logger.warning(
                "[top_creators] dropping unknown niche %r (not in %s)",
                niche_id,
                sorted(_KNOWN_NICHES),
            )
            continue

        creators: list[TopCreator] = []
        for entry in (niche_data or {}).get("channels") or []:
            if not isinstance(entry, dict):
                continue
            creator = TopCreator(
                channel_id=str(entry.get("channel_id") or "").strip(),
                label=str(entry.get("label") or "").strip(),
                notes=str(entry.get("notes") or "").strip(),
            )
            if not creator.is_valid():
                logger.warning(
                    "[top_creators] dropping invalid channel_id %r under niche %s",
                    creator.channel_id,
                    niche_id,
                )
                continue
            creators.append(creator)

        result[niche_id] = creators

    return result


def total_creator_count(watchlist: dict[str, list[TopCreator]]) -> int:
    """Convenience — total across all niches. Used by observability
    (Mission Control card + preflight readiness scanner)."""
    return sum(len(creators) for creators in watchlist.values())


__all__ = ["TopCreator", "load_top_creators", "total_creator_count"]
