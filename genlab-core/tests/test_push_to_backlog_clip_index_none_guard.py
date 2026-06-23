"""Regression pin: PushToBacklog clip_index lookup must None-guard each step.

PR #502 (2026-06-23) fixes a latent ``NoneType`` crash at the clip_index
chain in ``push_to_backlog.execute()``. The original code:

    clip_index = context.get("clip_index", {})
    clip_entry = clip_index.get("clips", {}).get(story_id, {})

assumed every level of the dict was either absent or non-None. In reality
some fetchers initialise ``context["clip_index"] = {}`` then leave
``clip_index["clips"]`` unset on early-exit paths, OR they set
``clip_index = None`` defensively. When that happens:

    None.get("clips", {})           # AttributeError: 'NoneType'.get
    {"clips": None}.get("clips", {}).get(story_id, {})
                  └─→ returns None ─┘
                                   └─→ None.get(...) crashes

The fix coerces None → {} at each step:

    clip_index = context.get("clip_index") or {}
    clip_entry = (clip_index.get("clips") or {}).get(story_id) or {}

Same `dict.get(k) or default` pattern shipped in PR #499 for video_gate.

Pinning here because the bug is silent — the surrounding try/except at
``push_to_backlog.py:1517`` would log a generic story-push failure
instead of surfacing the structural None problem.
"""

from __future__ import annotations

from pathlib import Path


def test_clip_index_lookup_uses_or_coalesce_at_each_step() -> None:
    """The chain must coerce None → {} at every dict access."""
    import genlab_core.pipeline.stages.push_to_backlog as ptb_mod

    src = Path(ptb_mod.__file__).read_text()
    # Pin the safe shape (PR #502)
    assert 'clip_index = context.get("clip_index") or {}' in src, (
        "context['clip_index'] lookup must use ``or {}`` coalesce — see PR #502 docstring."
    )
    assert '(clip_index.get("clips") or {}).get(story_id) or {}' in src, (
        "clip_index['clips'] lookup must use ``or {}`` coalesce at "
        "each step — see PR #502 docstring."
    )

    # Negative pin: the OLD unsafe shape must NOT come back
    assert 'clip_index = context.get("clip_index", {})' not in src, (
        "OLD unsafe shape 'context.get(\"clip_index\", {})' came back — "
        "fetchers can set clip_index=None, the default-arg only fires "
        "when the key is ABSENT, not when value is None."
    )
    assert 'clip_index.get("clips", {}).get(story_id' not in src, (
        "OLD unsafe shape 'clip_index.get(\"clips\", {}).get(story_id)' "
        "came back — if clips=None, the chain crashes."
    )


def test_video_id_extraction_uses_or_coalesce() -> None:
    """``video_id`` initial read should also use ``or`` instead of default.

    Same class of bug: ``story.get("video_id", "")`` returns None when
    a fetcher sets ``video_id=None`` explicitly. Downstream ``if not
    video_id`` happens to work (None is falsy) but any later ``video_id
    [:N]`` slice would crash. Safer to coerce up front.
    """
    import genlab_core.pipeline.stages.push_to_backlog as ptb_mod

    src = Path(ptb_mod.__file__).read_text()
    assert 'video_id = story.get("video_id") or ""' in src, (
        "video_id extraction must use ``or`` coalesce — see PR #502."
    )
