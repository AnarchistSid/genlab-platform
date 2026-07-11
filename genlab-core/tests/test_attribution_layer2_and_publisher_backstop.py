"""PR #Layer2-Publisher (2026-07-10) — defense-in-depth attribution.

Two enforcement points ship together in this PR because they solve
adjacent facets of the same failure:

  * **Layer 2 (persist gate)** — ``push_to_backlog`` refuses to write
    a YouTube-sourced blueprint without ``source_channel_id``. Complements
    Layer 1's fetcher gate (PR #763): even if a fetcher path
    synthesizes a story with missing channel data, this catches at
    persistence.

  * **Publisher backstop** — ``payload_builder`` appends
    ``format_source_attribution`` output to captions for FB / IG /
    Threads before the platform-specific sub-model is built. Parallel
    to the existing ``format_youtube_attribution`` wire for YT.
    Guarantees credit lands even when PR #761's writer-side
    ``content["source_attribution"]`` wasn't populated (LLM refusal,
    empty channel_name, pre-fix legacy blueprints).

Both are source pins on the modules — a future refactor that drops
either surfaces here at import time.
"""

from __future__ import annotations

import re
from pathlib import Path


def _normalise(src: str) -> str:
    return re.sub(r"\s+", " ", src)


# ── Layer 2: push_to_backlog persist gate ──────────────────────────


def test_layer2_persist_gate_reads_env_var_for_bypass():
    """Operator escape hatch — the bypass env var must be read via
    os.environ.get at gate-check time so operators can toggle
    without process restart."""
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "GENLAB_ATTRIBUTION_LAYER2_ALLOW_MISSING" in src
    assert "os.environ.get" in src


def test_layer2_persist_gate_refuses_yt_source_without_channel_id():
    """The gate condition must be YouTube-sourced AND missing
    source_channel_id AND not bypassed. Any weakening of the
    condition (dropping the source check, permitting empty channel
    ids) triggers here."""
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The three-part guard is present
    assert "_yt_sourced" in src
    assert "_layer2_allow_missing" in src
    assert 'not fields.get("source_channel_id")' in src


def test_layer2_persist_gate_precedes_client_create():
    """Ordering pin: the gate must run BEFORE
    ``client.blueprints.create()`` — otherwise the blueprint is
    persisted first and the guard is a no-op."""
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = Path(mod.__file__).read_text()
    gate_pos = src.find("Layer 2 attribution gate")
    create_pos = src.find("client.blueprints.create(fields, typecast=True)")
    assert gate_pos != -1
    assert create_pos != -1
    assert gate_pos < create_pos, (
        "Layer 2 gate must precede client.blueprints.create — "
        "otherwise the blueprint persists before the guard fires."
    )


def test_layer2_gate_exempts_non_youtube_sources():
    """Twitch/reddit/other sources have no derivable source URL today
    (see copyright_safety._SOURCE_URL_TEMPLATES) so requiring
    channel_id on them would misfire. The gate condition explicitly
    checks ``"youtube" in _source`` — if this narrows in the future,
    the exemption is intentional and this test forces the discussion."""
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert '"youtube" in _source' in src


def test_layer2_gate_logs_warning_with_diagnostic_context():
    """When the gate refuses a blueprint, the log message must
    include niche, source, and the bypass instruction so operators
    can diagnose without digging."""
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "Layer 2 attribution gate refusing" in src
    assert "GENLAB_ATTRIBUTION_LAYER2_ALLOW_MISSING=1" in src


# ── Publisher backstop: payload_builder appends attribution ────────


def test_publisher_backstop_appends_for_fb_ig_threads():
    """The publisher-side backstop must apply to Meta platforms
    (FB / IG / Threads). YouTube already has its own attribution
    wire via format_youtube_attribution in build_platform_specific.
    Twitter is excluded due to the 280-char budget."""
    import genlab_core.publishing.payload_builder as mod

    src = _normalise(Path(mod.__file__).read_text())
    # Guard clause matches exactly the 3 target platforms
    assert 'platform in ("facebook", "instagram", "threads")' in src
    # The credit line is appended to caption
    assert "caption = caption.rstrip() + " in src


def test_publisher_backstop_uses_format_source_attribution():
    """The backstop must call format_source_attribution (not
    reinvent the format inline) so the credit line stays consistent
    with PR #761's writer-side wire. Same helper, same idempotent
    substring shape, no divergence at merge time."""
    import genlab_core.publishing.payload_builder as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "format_source_attribution" in src
    assert "from genlab_core.compliance.copyright_safety import" in src


def test_publisher_backstop_is_idempotent_via_substring_guard():
    """If the credit line is already in the caption (writer already
    added it), the backstop must be a no-op — no double-appending.
    Substring match on the trimmed source attribution string is the
    check."""
    import genlab_core.publishing.payload_builder as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The idempotence guard
    assert "_src_attr.strip() not in caption" in src


def test_publisher_backstop_reads_extra_jsonb_fallback():
    """PR #B (2026-07-10) stores source_channel_title in blueprint
    extra JSONB (not a promoted column). The backstop must read the
    fallback path from extra so it works on backfilled blueprints
    that don't have source_channel_title in top-level fields."""
    import genlab_core.publishing.payload_builder as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The extra-fallback lookup
    assert '_extra_container.get("source_channel_title")' in src


def test_publisher_backstop_runs_before_platform_specific():
    """The backstop appends to the caption variable BEFORE the
    caption is passed to ``build_platform_specific``. Reversal would
    mean the credit gets applied to a stale caption copy in FB /
    IG / Threads sub-models but not to the final send."""
    import genlab_core.publishing.payload_builder as mod

    src = Path(mod.__file__).read_text()
    backstop_pos = src.find("Layer-Publisher (2026-07-10")
    platform_specific_pos = src.find(
        "platform_specific = build_platform_specific(fields, platform, caption, hook)"
    )
    assert backstop_pos != -1
    assert platform_specific_pos != -1
    assert backstop_pos < platform_specific_pos, (
        "Publisher backstop must run before build_platform_specific "
        "so the caption passed downstream carries the credit."
    )


def test_format_source_attribution_exists_on_this_branch():
    """This branch adds ``format_source_attribution`` to
    copyright_safety. Sibling to format_youtube_attribution. Both
    must remain live on the module so downstream imports don't
    break at either PR's merge."""
    from genlab_core.compliance.copyright_safety import (
        format_source_attribution,
        format_youtube_attribution,
    )

    assert callable(format_source_attribution)
    assert callable(format_youtube_attribution)
    # Behavioural smoke: the new helper produces the expected format
    out = format_source_attribution(
        {
            "video_id": "abc",
            "source": "youtube_trending",
            "source_channel_title": "MAKI",
        }
    )
    assert "@MAKI" in out
    assert "youtube.com/watch?v=abc" in out
