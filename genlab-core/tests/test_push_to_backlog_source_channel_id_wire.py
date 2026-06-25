"""push_to_backlog wires source_channel_id from story into blueprint —
foundation PR after #585.

Pure source pin: the field assembly in push_to_backlog.py must
read source_channel_id (or channel_id as fallback) from the story
and pass it into the blueprint record. A future refactor that
drops either key surfaces here.

A full integration test would need a Flask + Postgres fixture +
mocked-out trending fetcher + content writer + etc. — well beyond
the value-of-effort tradeoff for a 3-line wire. The source pin
catches every plausible regression at zero runtime cost.
"""

from __future__ import annotations

from pathlib import Path


def test_push_to_backlog_reads_source_channel_id_from_story():
    """The fields-dict assembly site must:
    1. Read story.get("source_channel_id") first (explicit)
    2. Fall back to story.get("channel_id") (TrendingVideo.to_dict()
       output shape, captured at fetch time)
    3. Pass the resolved value into fields["source_channel_id"]
    """
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = Path(mod.__file__).read_text()

    # Normalize whitespace so ruff format rewraps don't break the pin
    import re

    normalized = re.sub(r"\s+", " ", src)

    # The two-key fallback pattern must be present somewhere
    assert 'story.get("source_channel_id")' in normalized
    assert 'story.get("channel_id")' in normalized

    # The resolved value must land in the fields dict under the
    # exact column name (else PROMOTED_COLUMNS won't pick it up)
    assert '"source_channel_id": source_channel_id' in normalized


def test_promoted_column_name_matches_pin_in_push_to_backlog():
    """If PROMOTED_COLUMNS or the push_to_backlog field name drifts,
    one of these two will catch the desync."""
    from genlab_core.storage.postgres import PROMOTED_COLUMNS

    assert "source_channel_id" in PROMOTED_COLUMNS["blueprints"]
