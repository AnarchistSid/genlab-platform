"""Source-discovery proposer (foundation: PR #586, logic: PR after #586).

Ranks SOURCE channels (the YouTube channels we pulled clips from)
by per-clip engagement, so operators can decide which under-tracked
channels are worth adding to ``config/sources.yaml``.

## Public surface

  propose_source_channels(
      niche_id, *, window_days=30, min_clips=2, top_n=10,
  ) -> list[ChannelSuggestion]

  ChannelSuggestion dataclass — frozen, JSON-serialisable.

## When to call this

The proposer reads ``blueprints JOIN analytics`` and needs both
the foundation data (PR #586) AND engagement data. Until source
channels accumulate in production via the producer wire, this
function returns ``[]`` — its query passes through the
``WHERE source_channel_id IS NOT NULL`` filter and that filter is
the data-availability gate.
"""

from genlab_core.source_discovery.proposer import (
    ChannelSuggestion,
    propose_source_channels,
)

__all__ = ["ChannelSuggestion", "propose_source_channels"]
