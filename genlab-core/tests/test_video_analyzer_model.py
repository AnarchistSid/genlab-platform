"""Pin R-43: ``VideoAnalyzer`` requests the GA Gemini model, not a
deprecated preview.

The audit found ``video_analyzer.py:76`` had
``gemini-2.5-flash-preview-04-17`` (the preview model deprecated
2025-07-15). PR #12 fixed the router-layer (``router.py`` +
``model_routing.yaml``) but missed this one direct caller that
bypasses the router entirely.

This test reads the source file and asserts the deprecated model
name is gone — keeping it fast (no Gemini API call needed) and
brittle to the regression we care about. Re-introducing the preview
name fails CI immediately.
"""

from __future__ import annotations

from pathlib import Path

# The deprecated preview model the audit flagged.
DEPRECATED_PREVIEW_MODEL = "gemini-2.5-flash-preview-04-17"

VIDEO_ANALYZER_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "genlab_core" / "llm" / "video_analyzer.py"
)


def test_video_analyzer_does_not_pin_deprecated_preview_model() -> None:
    text = VIDEO_ANALYZER_SOURCE.read_text()
    # The deprecated model appears only inside the explanatory comment
    # — a substring match would false-positive on that. Match a Python
    # string literal that BINDS the deprecated value.
    needle = f'"{DEPRECATED_PREVIEW_MODEL}"'
    # Find lines containing the literal; allow a comment line as
    # documentation-only.
    bad_lines = [
        i
        for i, line in enumerate(text.splitlines(), start=1)
        if needle in line and not line.lstrip().startswith("#")
    ]
    assert not bad_lines, (
        f"R-43 regression: video_analyzer.py binds the deprecated "
        f"preview model {DEPRECATED_PREVIEW_MODEL!r} on line(s) {bad_lines}. "
        f"Use the GA `gemini-2.5-flash` name."
    )


def test_video_analyzer_uses_ga_model_string() -> None:
    """Positive assertion — the file should reference the GA name
    somewhere (in the live ``GenerativeModel(...)`` call)."""
    text = VIDEO_ANALYZER_SOURCE.read_text()
    assert '"gemini-2.5-flash"' in text, (
        "VideoAnalyzer must reference the GA model name "
        "`gemini-2.5-flash` (matching model_routing.yaml's "
        "video_analysis tier)."
    )
