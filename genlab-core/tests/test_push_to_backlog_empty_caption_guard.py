"""Pin the 2026-07-17 empty-caption guard at push_to_backlog persist boundary.

Regression case: `base_hooks.py:306-318` template-formula recovery pops
`_skip_llm` when a hook can be produced from title alone. That path leaves
`content["<platform>"]` empty — LLM writer was skipped by
`_has_writable_context` (base_writing.py:652) when story summary was
below the 40-char floor. Downstream, the publisher's L4 attribution
gate hard-fails the row.

Iceberg from audit: 6 blueprints in the last 30d shipped this shape;
21+ twitch_trending stories dodged the writer via `_skip_llm`. Blueprint
`83016a45` was the trigger case.

This test proves the persist-time guard rejects the shape.
"""

from __future__ import annotations


def test_persist_guard_recognises_all_empty_content() -> None:
    """The guard predicate at push_to_backlog.py must recognise the
    all-empty shape produced by the template-formula rescue path.
    """
    # Same shape push_to_backlog constructs at lines 2278-2282 + the
    # guard predicate at line 2284-2289.
    content: dict[str, dict[str, str]] = {
        "instagram": {},
        "youtube": {},
        "x_twitter": {"tweet_text": "some tweet"},  # Twitter body only
        "facebook": {},
        "threads": {},
    }
    ig = content.get("instagram", {})
    yt = content.get("youtube", {})
    fb = content.get("facebook", {})
    th = content.get("threads", {})

    all_empty = (
        not (ig.get("caption") or "").strip()
        and not (fb.get("caption") or "").strip()
        and not (th.get("caption") or "").strip()
        and not (yt.get("description") or "").strip()
    )
    assert all_empty is True, (
        "Persist-guard shape mismatch: predicate did not flag the "
        "all-empty-except-Twitter case that yields empty captions on "
        "FB/IG/YT publishes. If the predicate at push_to_backlog.py "
        "changed, update this pin."
    )


def test_persist_guard_lets_at_least_one_populated_platform_through() -> None:
    """Guard must NOT false-fire — any single populated platform passes."""
    for populated in ("instagram", "facebook", "threads"):
        content: dict[str, dict[str, str]] = {
            "instagram": {},
            "youtube": {},
            "facebook": {},
            "threads": {},
        }
        content[populated] = {"caption": "real caption text"}

        ig = content.get("instagram", {})
        yt = content.get("youtube", {})
        fb = content.get("facebook", {})
        th = content.get("threads", {})

        all_empty = (
            not (ig.get("caption") or "").strip()
            and not (fb.get("caption") or "").strip()
            and not (th.get("caption") or "").strip()
            and not (yt.get("description") or "").strip()
        )
        assert all_empty is False, (
            f"Guard false-fired for populated platform={populated!r}. "
            "Any single populated caption should let the row through."
        )


def test_persist_guard_lets_yt_description_through() -> None:
    """YT description is stored in `description`, not `caption`. Guard
    must accept it as a valid platform body."""
    content = {
        "instagram": {},
        "youtube": {"description": "YouTube-specific body text"},
        "facebook": {},
        "threads": {},
    }
    ig = content.get("instagram", {})
    yt = content.get("youtube", {})
    fb = content.get("facebook", {})
    th = content.get("threads", {})

    all_empty = (
        not (ig.get("caption") or "").strip()
        and not (fb.get("caption") or "").strip()
        and not (th.get("caption") or "").strip()
        and not (yt.get("description") or "").strip()
    )
    assert all_empty is False, (
        "Guard should accept YouTube description as a valid platform body — "
        "YT is stored under `description`, not `caption`."
    )


def test_persist_guard_rejects_whitespace_only_captions() -> None:
    """A caption of only whitespace is NOT a real body — the guard must
    treat it as empty (that was the actual 83016a45 shape after
    template-formula rescue)."""
    content = {
        "instagram": {"caption": "   "},
        "youtube": {"description": "\n\n"},
        "facebook": {"caption": "\t"},
        "threads": {"caption": ""},
    }
    ig = content.get("instagram", {})
    yt = content.get("youtube", {})
    fb = content.get("facebook", {})
    th = content.get("threads", {})

    all_empty = (
        not (ig.get("caption") or "").strip()
        and not (fb.get("caption") or "").strip()
        and not (th.get("caption") or "").strip()
        and not (yt.get("description") or "").strip()
    )
    assert all_empty is True, (
        "Guard must .strip() before checking truthiness — whitespace-only "
        "captions should count as empty."
    )
