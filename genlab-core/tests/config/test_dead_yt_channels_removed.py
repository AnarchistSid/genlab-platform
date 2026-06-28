"""Regression pin: dead YouTube channel IDs must stay out of
``genlab-core/config/shared_sources.yaml``.

Background
----------
DEEP_AUDIT_2026-03-26.md (line 1011) flagged the sports YouTube
channel ``UCqQo7ewe87aYAe7ub5cYxDg`` (formerly listed as "House of
Highlights") as returning HTTP 404 from its RSS feed:

    https://www.youtube.com/feeds/videos.xml?channel_id=UCqQo7ewe87aYAe7ub5cYxDg

Verified again on 2026-06-28 — still 404. The handle
``@HouseofHighlights`` now redirects to a different channel
("Creator League", ``UC5qUhMoqke0mnJtgVoEn0aw``), so the original
ID is unrecoverable. Entry was removed via
``chore/deep-audit-2026-03-ops-hygiene-sweep``.

This pin guards against a future copy-paste re-adding the dead ID,
which would re-introduce the silent zero-entries fetch the audit
called out.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_SOURCES_YAML = REPO_ROOT / "genlab-core" / "config" / "shared_sources.yaml"


# IDs that have been verified dead (RSS feed returns 404) and must NOT
# be re-added. Add new entries here as future audits confirm them.
KNOWN_DEAD_YT_CHANNEL_IDS: tuple[str, ...] = (
    "UCqQo7ewe87aYAe7ub5cYxDg",  # formerly "House of Highlights" (sports)
)


@pytest.mark.parametrize("dead_id", KNOWN_DEAD_YT_CHANNEL_IDS)
def test_dead_yt_channel_id_absent_from_shared_sources(dead_id: str) -> None:
    """Pin that a known-dead YouTube channel ID is not present in any
    source URL inside ``shared_sources.yaml``."""

    assert SHARED_SOURCES_YAML.exists(), (
        f"shared_sources.yaml not found at {SHARED_SOURCES_YAML}; "
        "test must run from a checked-out repo."
    )

    raw = SHARED_SOURCES_YAML.read_text(encoding="utf-8")

    # Raw substring guard (catches the ID even inside a comment, which
    # is exactly the wrong place to revive it from).
    assert dead_id not in _strip_explainer_comments(raw), (
        f"Dead YouTube channel ID {dead_id!r} reappeared in "
        f"{SHARED_SOURCES_YAML.relative_to(REPO_ROOT)}. "
        "Its RSS feed returns HTTP 404 — re-adding it silently breaks "
        "the source pool. See DEEP_AUDIT_2026-03-26.md L1011."
    )

    # Parsed guard (defends against YAML structure drift).
    parsed = yaml.safe_load(raw) or {}
    sources = parsed.get("sources", [])
    assert isinstance(sources, list), (
        f"Expected top-level 'sources' to be a list in "
        f"{SHARED_SOURCES_YAML.name}, got {type(sources).__name__}."
    )
    offending = [s for s in sources if isinstance(s, dict) and dead_id in (s.get("url") or "")]
    assert not offending, (
        f"Dead YouTube channel ID {dead_id!r} found in sources: "
        f"{[s.get('name') for s in offending]}"
    )


def _strip_explainer_comments(yaml_text: str) -> str:
    """Strip YAML comment lines so an explainer comment that names the
    dead ID (to explain why it's gone) doesn't trip the substring
    guard. Only full-line comments are stripped; in-line comments
    after a key:value are preserved (they're still operational
    content)."""

    out_lines: list[str] = []
    for line in yaml_text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)
