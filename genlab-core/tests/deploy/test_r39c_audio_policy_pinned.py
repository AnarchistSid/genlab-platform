"""R-39 (c): pin the video-first source-audio policy.

The audit (R-39 LOW) offered an explicit OR for sub-item (c):

  recommendation: trim at render; **wire TTS OR document
  video-first source-audio policy**.

PR #138 shipped (a)+(b) (the trim + the silent-mux autofix). This
PR's policy doc — ``docs/audio-policy.md`` — is the documentation
half of that OR for (c). The render path uses source-video audio
with a silent-mux fallback; the TTS cascade is correctly scoped
to non-render contexts (narration audio stage + Whisper caption
alignment).

This file pins:

  1. The policy doc exists.
  2. The TTS module docstring points at the policy.
  3. The validate_videos docstring points at the policy (the
     ``no_audio_stream`` autofix is the render-time half).

A future refactor that quietly deletes the policy reference from
either module would fail CI here — the policy decision becomes
re-debatable instead of silently lost.
"""

from __future__ import annotations

from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_POLICY_DOC = _GENLAB_ROOT / "docs" / "audio-policy.md"


def test_r39c_audio_policy_doc_exists() -> None:
    """The canonical policy doc must exist at the path the two
    modules' docstrings point at."""
    assert _POLICY_DOC.is_file(), (
        f"R-39 (c) regression: the canonical audio policy doc is missing "
        f"at {_POLICY_DOC.relative_to(_GENLAB_ROOT)}. Either restore the "
        "doc (and keep the module docstrings pointing at it) or take an "
        "explicit decision to wire TTS into render — but the silent loss "
        "of the policy is what the regression pin guards against."
    )


def test_r39c_audio_policy_doc_carries_the_decision() -> None:
    """The doc must explicitly declare the policy: video-first source
    audio with silent-mux fallback, NOT TTS-in-render. If a future
    edit waters that down (e.g., 'we may add TTS to render someday'),
    the policy becomes ambiguous again."""
    import re

    body = _POLICY_DOC.read_text()
    # The TL;DR section must say render NEVER synthesizes audio. Use
    # a whitespace-tolerant regex so the assertion catches the
    # statement even when it line-breaks across "never" and
    # "synthesizes" (Markdown reflow).
    assert re.search(r"never\s+synthesize", body), (
        "R-39 (c) regression: ``docs/audio-policy.md`` no longer "
        "explicitly states the render path NEVER synthesizes audio. "
        "Without that explicit statement, the policy becomes a soft "
        "preference and the audit's 'doc-only guarantee' complaint "
        "comes back."
    )
    # The doc must reference both R-39 a+b's silent-mux mechanism
    # AND the explicit closure of (c).
    assert "anullsrc" in body, "R-39 silent-mux mechanism missing from policy"
    assert "R-39 (c)" in body, "R-39 (c) closure framing missing from policy"


def test_r39c_tts_module_docstring_references_policy() -> None:
    """The TTS module is the natural place someone looking for
    'how does GenLab handle audio' lands. Its docstring must point
    them at the policy doc."""
    body = (
        _GENLAB_ROOT / "genlab-core" / "src" / "genlab_core" / "tts" / "__init__.py"
    ).read_text()
    assert "audio-policy.md" in body, (
        "R-39 (c) regression: ``genlab_core.tts.__init__`` no longer "
        "references ``docs/audio-policy.md``. Without that pointer, "
        "a contributor scanning the TTS module sees the cascade and "
        "naturally reaches for it in render — the exact mistake the "
        "policy doc prevents."
    )
    # And must explicitly state TTS is NOT for render.
    assert "not* by the render path" in body or "not by the render path" in body, (
        "R-39 (c) regression: TTS module docstring no longer states "
        "TTS is NOT used by the render path."
    )


def test_r39c_validate_videos_docstring_references_policy() -> None:
    """The validate_videos stage hosts the ``no_audio_stream`` autofix
    that IS the render-time half of the policy. Its docstring must
    point at the policy so the choice (silent-mux not TTS) is
    discoverable from the code that implements it."""
    body = (
        _GENLAB_ROOT
        / "genlab-core"
        / "src"
        / "genlab_core"
        / "pipeline"
        / "stages"
        / "validate_videos.py"
    ).read_text()
    assert "audio-policy.md" in body, (
        "R-39 (c) regression: ``validate_videos`` no longer references "
        "``docs/audio-policy.md``. The silent-mux autofix has no "
        "discoverable rationale without that pointer."
    )
