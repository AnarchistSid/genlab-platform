"""Pin: writer's + renderer's LLM refusal prefix lists MUST stay in sync.

Codified 2026-07-14 after audit found the writer's list had 10 entries
while renderer's had 15 — writer could emit a hook that only the
render gate would catch, wasting the LLM budget for that story. Prior
docstring on ``rendering/pre_render_quality.py:78`` claimed "test file
pins parity" but no such test existed. This is that file.

Invariant: the two ``_LLM_REFUSAL_PREFIXES`` tuples are equal (same
strings, same order). If they need to drift, do it deliberately — add
a whitelist here documenting the reason, but the default expectation
is strict equality.
"""

from __future__ import annotations

from genlab_core.rendering.pre_render_quality import (
    _LLM_REFUSAL_PREFIXES as RENDER_PREFIXES,
)
from genlab_core.writing.video_content_writer import (
    _LLM_REFUSAL_PREFIXES as WRITER_PREFIXES,
)


def test_llm_refusal_prefixes_are_equal_across_writer_and_renderer():
    """The two prefix tuples must contain the SAME strings.

    Rationale: if the writer misses a prefix that renderer catches, we
    burn LLM budget writing a hook the render gate then rejects. If
    the writer catches a prefix that renderer doesn't, we lose the
    render-gate safety net for that shape. Both directions are bad.
    """
    render_set = set(RENDER_PREFIXES)
    writer_set = set(WRITER_PREFIXES)

    only_in_render = render_set - writer_set
    only_in_writer = writer_set - render_set

    assert not only_in_render, (
        f"Renderer has {len(only_in_render)} prefix(es) writer doesn't: "
        f"{sorted(only_in_render)}. Add them to "
        f"genlab_core.writing.video_content_writer._LLM_REFUSAL_PREFIXES."
    )
    assert not only_in_writer, (
        f"Writer has {len(only_in_writer)} prefix(es) renderer doesn't: "
        f"{sorted(only_in_writer)}. Add them to "
        f"genlab_core.rendering.pre_render_quality._LLM_REFUSAL_PREFIXES."
    )


def test_prefix_tuples_are_non_empty():
    """Both lists must be non-empty — an empty list disables the gate."""
    assert len(RENDER_PREFIXES) > 0, "Renderer prefix list is empty"
    assert len(WRITER_PREFIXES) > 0, "Writer prefix list is empty"


def test_prefixes_are_lowercase():
    """Both lists compare against ``text.lower().startswith(prefix)`` —
    all prefixes MUST be already lowercase or the check silently misses.
    """
    for p in RENDER_PREFIXES:
        assert p == p.lower(), f"Renderer prefix not lowercase: {p!r}"
    for p in WRITER_PREFIXES:
        assert p == p.lower(), f"Writer prefix not lowercase: {p!r}"
