"""2026-08-12 (F-QB-0708 pt 2): pin the LLM-output `Via {source}`
strip in video_content_writer.

Motivating audit finding: ~36% of recent captions contain a
standalone `Via r/subreddit` (or similar) line that the LLM writes
despite prompt instruction. Combined with the pipeline's
`🎬 Original:` append, this creates YouTube's inauthentic-content
template signature (F-QB-0708).

Two-layer fix:
  1. Prompt update (line 744-747 in video_content_writer.py) tells
     LLM explicitly NOT to write `Via {source}`.
  2. This post-process strip catches the LLM ignoring the prompt
     (belt-and-suspenders).

These pins ensure future refactors of the writer can't silently
regress the strip.
"""

from __future__ import annotations

from genlab_core.writing.video_content_writer import _strip_via_source_lines


class TestStripViaSourceLines:
    def test_strips_via_subreddit_line(self):
        text = (
            "Test hook: this is a great story about something interesting.\n"
            "\n"
            "Via r/movies\n"
            "\n"
            "#Movies #Cinema"
        )
        result = _strip_via_source_lines(text)
        assert "Via r/movies" not in result
        assert "#Movies #Cinema" in result
        assert "Test hook:" in result

    def test_strips_via_youtube_line(self):
        text = "Hook here\n\nVia YouTube\n\n#Reels"
        assert "Via YouTube" not in _strip_via_source_lines(text)

    def test_strips_case_insensitive(self):
        for variant in ("Via", "via", "VIA", "vIa"):
            text = f"Hook\n\n{variant} SomeSource\n\n#Tag"
            assert variant not in _strip_via_source_lines(text), (
                f"failed to strip variant {variant!r}"
            )

    def test_strips_multiple_variants(self):
        """LLM sometimes writes multiple attribution lines. Strip all."""
        text = "Hook\n\nVia r/movies\nVia YouTube\n\n#Tag"
        result = _strip_via_source_lines(text)
        assert "Via r/" not in result
        assert "Via YouTube" not in result

    def test_preserves_via_inside_sentence(self):
        """Only line-anchored `Via X` blocks match. `via` mid-sentence
        (e.g. "getting there via public transport") must NOT strip."""
        text = "Getting to the studio via public transport was easy.\n\n#City"
        assert "via public transport was easy" in _strip_via_source_lines(text)

    def test_empty_text_unchanged(self):
        assert _strip_via_source_lines("") == ""

    def test_no_via_unchanged(self):
        text = "Hook here\n\n#Movies"
        assert _strip_via_source_lines(text) == text

    def test_idempotent(self):
        text = "Hook\n\nVia r/movies\n\n#Tag"
        once = _strip_via_source_lines(text)
        twice = _strip_via_source_lines(once)
        assert once == twice

    def test_collapses_orphan_newlines(self):
        """Stripping a line between `\\n\\n` and `\\n\\n` creates a
        4-newline gap. Must collapse back to standard `\\n\\n`."""
        text = "Hook\n\nVia r/movies\n\n#Tag"
        result = _strip_via_source_lines(text)
        # No triple-newline runs left
        assert "\n\n\n" not in result

    def test_writer_prompt_bans_via_source(self):
        """Prompt-side pin: the writer module source must contain
        explicit ban on `Via {source}` lines. Belt-and-suspenders
        check that the prompt guidance isn't accidentally removed
        while keeping the post-process strip."""
        from pathlib import Path

        module_src = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "genlab_core"
            / "writing"
            / "video_content_writer.py"
        ).read_text()
        assert (
            "Via r/subreddit" in module_src
            or "'Via {source_name}'" in module_src
        ), (
            "Writer prompt must ban `Via {source}` explicitly. "
            "Without prompt guidance, the LLM emits these lines at "
            "~36% rate creating the F-QB-0708 template signature. "
            "Post-process strip is defense-in-depth, not the primary fix."
        )
