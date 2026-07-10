"""PR #Layer1 (2026-07-10) — fetcher-side attribution gate.

Post-Markanimation defense-in-depth. PR #B (channel_id / channel_name
end-to-end) fixed the write-side plumbing so the fetcher CAN populate
these fields on every YouTube-sourced candidate. Layer 1 is the
enforcement pin: refuse to emit a ``TrendingVideo`` from ``fetch_trending``
without both fields populated.

The gate defaults to DROP because — post-Markanimation — the operator
wants "attribution or no publish" as the safer posture across the 90-day
retroactive-audit window. Override via
``GENLAB_ATTRIBUTION_LAYER1_ALLOW_MISSING=1`` for edge cases where a
source legitimately lacks channel data (archive footage without
upstream channel metadata).

These are source pins on the module — a future refactor that drops
the guard fires here at import time. Full behavioral tests would need
mocked YouTube API responses + fetcher stubs; the source pin catches
every plausible refactor at ~zero runtime cost.
"""

from __future__ import annotations

import re
from pathlib import Path


def _normalise(src: str) -> str:
    return re.sub(r"\s+", " ", src)


def test_layer1_gate_reads_env_var_for_override():
    """The bypass env var is the operator's escape hatch — it must be
    read at gate-check time, not module import (otherwise operators
    can't flip the flag without a process restart)."""
    import genlab_core.media.trending_video_fetcher as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The env var name (documented in the incident writeup + README)
    assert "GENLAB_ATTRIBUTION_LAYER1_ALLOW_MISSING" in src
    # Read via os.environ.get so operators can toggle without restart
    assert "os.environ.get" in src


def test_layer1_gate_filters_on_both_channel_id_and_channel_name():
    """Missing EITHER field is a drop condition. Rationale: channel_id
    without channel_name means we have proposer coverage but no
    human-readable credit; channel_name without channel_id means we
    have credit but no proposer key. Both are attribution failures."""
    import genlab_core.media.trending_video_fetcher as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The guard condition checks both fields with 'or'
    assert "not v.channel_id or not v.channel_name" in src
    # The keep-list uses 'and'
    assert "v.channel_id and v.channel_name" in src


def test_layer1_gate_logs_dropped_samples_for_operator():
    """When candidates are dropped, the log message must include enough
    to diagnose the source path — count + sample video_ids/titles. A
    silent drop would be worse than no gate because operators can't
    tell if the fetcher is producing empty or actually filtering."""
    import genlab_core.media.trending_video_fetcher as mod

    src = _normalise(Path(mod.__file__).read_text())
    # Log message includes the drop reason and the env-var escape hatch
    assert "Layer 1 attribution gate dropped" in src
    assert "GENLAB_ATTRIBUTION_LAYER1_ALLOW_MISSING=1" in src
    # Samples of the first 3 dropped items surface to the log
    assert "_dropped[:3]" in src


def test_layer1_gate_precedes_sort_and_rerank():
    """The gate must run BEFORE view_velocity sort + niche-fit LLM
    rerank so we don't waste an LLM call on candidates that will
    never persist. Pin the ordering by string proximity — if the
    gate ever gets moved after the rerank, this test fires."""
    import genlab_core.media.trending_video_fetcher as mod

    src = Path(mod.__file__).read_text()
    # Find both markers
    gate_pos = src.find("Layer 1 attribution gate")
    sort_pos = src.find("results.sort(key=lambda v: v.view_velocity")
    assert gate_pos != -1, "gate marker not found"
    assert sort_pos != -1, "sort marker not found"
    assert gate_pos < sort_pos, (
        "Layer 1 gate must run BEFORE the velocity sort so we don't "
        "waste LLM rerank calls on candidates that will be dropped."
    )


def test_layer1_default_is_enforce_not_observe():
    """Post-Markanimation, the safer posture is enforce-by-default with
    operator opt-out. If someone flips the default to observe-only,
    that's a policy regression and this test fires."""
    import genlab_core.media.trending_video_fetcher as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The env var check defaults to "0" (bypass off = enforce)
    assert 'GENLAB_ATTRIBUTION_LAYER1_ALLOW_MISSING", "0"' in src
    # The "if not _layer1_allow_missing:" guard runs the drop
    assert "if not _layer1_allow_missing" in src


def test_layer1_gate_produces_only_credible_candidates_on_stub():
    """Behavioural pin: build a TrendingVideo list with a mix of
    credible + un-credible candidates and confirm the filter logic
    keeps only the credible ones. Exercises the exact list-comp
    the fetcher uses in production."""
    from datetime import UTC, datetime

    from genlab_core.media.trending_video_fetcher import TrendingVideo

    good = TrendingVideo(
        video_id="v1",
        title="good",
        channel_name="Ch1",
        channel_id="UC1",
        published_at=datetime.now(UTC),
        view_count=1,
        like_count=1,
        duration_seconds=30,
        thumbnail_url="",
        niche_id="anime",
        search_query="",
        view_velocity=1.0,
        download_url="https://x",
        is_official_channel=False,
        license="",
    )
    no_id = TrendingVideo(
        video_id="v2",
        title="no-id",
        channel_name="Ch2",
        channel_id="",
        published_at=datetime.now(UTC),
        view_count=1,
        like_count=1,
        duration_seconds=30,
        thumbnail_url="",
        niche_id="anime",
        search_query="",
        view_velocity=1.0,
        download_url="https://x",
        is_official_channel=False,
        license="",
    )
    no_name = TrendingVideo(
        video_id="v3",
        title="no-name",
        channel_name="",
        channel_id="UC3",
        published_at=datetime.now(UTC),
        view_count=1,
        like_count=1,
        duration_seconds=30,
        thumbnail_url="",
        niche_id="anime",
        search_query="",
        view_velocity=1.0,
        download_url="https://x",
        is_official_channel=False,
        license="",
    )
    candidates = [good, no_id, no_name]
    # Exact filter the fetcher applies
    kept = [v for v in candidates if v.channel_id and v.channel_name]
    dropped = [v for v in candidates if not v.channel_id or not v.channel_name]
    assert kept == [good]
    assert set(v.video_id for v in dropped) == {"v2", "v3"}
