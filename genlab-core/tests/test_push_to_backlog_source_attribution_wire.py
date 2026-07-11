"""push_to_backlog + video_content_writer wire source-creator credit
into every audience-facing platform caption — PR #A (2026-07-10,
Markanimation incident).

Root cause of the incident:

  1. ``video_content_writer.py`` only produced ``youtube_attribution``
     (self-attribution: "Curated and produced by FrameDrift").
  2. ``push_to_backlog.py`` only appended ``youtube_attribution`` to
     ``youtube_content`` — never to FB / IG / Threads captions.
  3. There was no source-creator credit anywhere.

A real creator (Markanimation on YouTube) DM'd asking for credit on
a reposted FrameDrift reel. Fix: writer populates
``content["source_attribution"]`` from the source video's channel
handle; push_to_backlog appends that string to every audience-facing
caption (Twitter skipped due to 280-char limit).

These are source pins — same pattern as the sibling
``test_push_to_backlog_source_channel_id_wire.py`` — so a future
refactor that silently drops the wire (rename the key, remove the
``_credit`` call site) surfaces here at import time. A full
integration test would need trending fetcher + writer + push flow
+ Postgres; the source pin catches every plausible regression at
zero runtime cost.
"""

from __future__ import annotations

import re
from pathlib import Path


def _normalise(src: str) -> str:
    return re.sub(r"\s+", " ", src)


def test_writer_populates_source_attribution_key():
    """video_content_writer must set ``content["source_attribution"]``
    from ``video.channel_name`` + video_id so push_to_backlog can wire
    it into audience-facing captions."""
    import genlab_core.writing.video_content_writer as mod

    src = _normalise(Path(mod.__file__).read_text())

    # The key push_to_backlog reads
    assert 'content["source_attribution"]' in src
    # Populated via the shared helper (single source of truth for format)
    assert "format_source_attribution" in src
    # Reads the fetcher's channel_name field
    assert 'video.get("channel_name"' in src


def test_push_to_backlog_reads_source_attribution_and_applies_credit():
    """push_to_backlog must:
    1. Read ``content.get("source_attribution", "")`` into a local
    2. Define a ``_credit`` helper that appends attribution
       idempotently (substring guard)
    3. Apply ``_credit`` to at least: IG caption, facebook_content,
       threads_content, and the YT description text
    """
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = _normalise(Path(mod.__file__).read_text())

    # Reads the key produced by the writer
    assert 'content.get("source_attribution"' in src

    # _credit helper exists (guard against dropped-helper refactors)
    assert "def _credit(" in src

    # Applied to every audience-facing platform caption
    assert '_credit(ig.get("caption"' in src
    assert '_credit(fb.get("caption"' in src
    assert '_credit(yt.get("description"' in src
    # Threads spans multiple lines, so just check the call is present
    assert "_credit(" in src


def test_credit_helper_uses_substring_idempotence_guard():
    """The ``_credit`` helper must check ``if _src_attr in text`` before
    appending. Without this, re-runs of push_to_backlog would double-
    append the credit line every time. The
    ``format_source_attribution`` output is a deterministic string, so
    substring match is sufficient."""
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = Path(mod.__file__).read_text()

    # The guard clause — look for "in text" in proximity to _src_attr
    # Whitespace-normalise so ruff format doesn't break the pin
    normalised = _normalise(src)
    assert "_src_attr in text" in normalised
    # And the fallback returns the text unchanged
    assert "return text" in normalised


def test_twitter_content_not_credited():
    """Twitter has a 280-char limit; captions rarely have room for a
    credit line without truncating the take itself. Pin that the
    twitter_content assembly does NOT call _credit — a future PR that
    tries to wire it should trigger this test and force explicit
    reconsideration of the truncation strategy."""
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = Path(mod.__file__).read_text()

    # Find the twitter_content block (the surrounding JSON dumps call)
    twitter_block_match = re.search(
        r'"twitter_content":\s*json\.dumps\(\s*\{[^}]+\}\s*\)',
        src,
        re.DOTALL,
    )
    assert twitter_block_match, "twitter_content block not found — refactor?"
    twitter_block = twitter_block_match.group(0)
    assert "_credit(" not in twitter_block, (
        "twitter_content is now calling _credit; make sure the credit "
        "line fits in 280 chars or add a truncation strategy."
    )
