"""Pin: one true-peak constant, and the producer always lands under the gate.

PUBLISH-03 §2 (2026-09-15). Four modules defined the same three loudness numbers
without importing each other:

    post_render_transform  -1.5 dBTP   (every render)
    validate_videos        -1.0 dBTP   (the gate)
    motion_compositor      -1.5 dBTP   (hardcoded inline)
    ffmpeg_utils           -1.5 dBTP   (defaults, live on the narration path)

The producer normalised to -1.5 and the gate rejected above -1.0. Those agree
only because -1.5 happens to sit below -1.0 -- nothing enforced the direction,
and the integrated-loudness pair agreed by coincidence. A movies render reached
the gate at -0.93 dBTP and was rejected.

Two invariants below. The first is the one that was missing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from genlab_core.media import audio_loudness
from genlab_core.pipeline.stages.validate_videos import SPEC

_SRC = Path(__file__).resolve().parents[2] / "src" / "genlab_core"


def test_producer_target_is_strictly_under_the_gate() -> None:
    """The invariant nothing enforced.

    If someone raises the normalisation target or lowers the gate, the producer
    starts emitting files the gate rejects and every niche stops rendering. That
    is a silent, total outage; it must fail here instead.
    """
    assert audio_loudness.NORMALISE_TARGET_TRUE_PEAK_DBTP < SPEC["max_true_peak"], (
        f"producer normalises to {audio_loudness.NORMALISE_TARGET_TRUE_PEAK_DBTP} "
        f"dBTP but the gate rejects above {SPEC['max_true_peak']} dBTP"
    )


def test_the_margin_covers_the_measured_overshoot() -> None:
    """loudnorm overshoots its own TP target; the gap must absorb that."""
    gap = SPEC["max_true_peak"] - audio_loudness.NORMALISE_TARGET_TRUE_PEAK_DBTP
    assert gap >= audio_loudness.MEASURED_LOUDNORM_OVERSHOOT_DB, (
        f"only {gap:.2f} dB between normalisation target and gate, but loudnorm "
        f"was measured overshooting by {audio_loudness.MEASURED_LOUDNORM_OVERSHOOT_DB} dB"
    )


def test_gate_and_shared_spec_are_the_same_object() -> None:
    assert SPEC["max_true_peak"] == audio_loudness.GATE_MAX_TRUE_PEAK_DBTP
    assert SPEC["target_lufs"] == audio_loudness.TARGET_LUFS


def test_limiter_ceiling_derives_from_the_gate() -> None:
    """Not an independent number. Recompute it and compare."""
    expected = 10 ** (audio_loudness.NORMALISE_TARGET_TRUE_PEAK_DBTP / 20)
    assert audio_loudness.LIMITER_CEILING_LINEAR == pytest.approx(expected)


def test_every_loudnorm_ends_on_the_limiter() -> None:
    """loudnorm can raise peaks and its single-pass TP target is predictive.

    Before this pin the limiter ran only in the ValidateVideos REPAIR path --
    after the gate had already failed a file -- so the normal render path ended
    on loudnorm with nothing after it.
    """
    chain = audio_loudness.loudnorm_filter() + "," + audio_loudness.limiter_filter()
    assert chain.index("loudnorm") < chain.index("alimiter")

    prt = (_SRC / "media" / "post_render_transform.py").read_text()
    assert "audio_loudness.limiter_filter()" in prt, (
        "the producer path must append the limiter after loudnorm"
    )


# A TP literal anywhere outside audio_loudness.py is a fifth constant being born.
# Scanned with `tokenize` rather than line matching: the measurement history in
# validate_videos' docstrings legitimately quotes "TP=-0.72", and a line-based
# scan cannot tell a docstring interior from code.
_TP_LITERAL = re.compile(r"TP=-?\d+(?:\.\d+)?")
_EXEMPT = {"audio_loudness.py"}


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """(lineno, text) for tokens that are neither comments nor string literals."""
    import io
    import tokenize

    out: list[tuple[int, str]] = []
    try:
        with path.open("rb") as fh:
            for tok in tokenize.tokenize(io.BytesIO(fh.read()).readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                if tok.string.strip():
                    out.append((tok.start[0], tok.string))
    except (SyntaxError, tokenize.TokenError, UnicodeDecodeError):
        return []
    return out


def test_no_module_hardcodes_a_true_peak_literal() -> None:
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        if path.name in _EXEMPT:
            continue
        # f-strings tokenize as pieces, so rejoin per line before matching.
        by_line: dict[int, str] = {}
        for lineno, text in _code_lines(path):
            by_line[lineno] = by_line.get(lineno, "") + text
        for lineno, joined in sorted(by_line.items()):
            if _TP_LITERAL.search(joined):
                offenders.append(f"{path.relative_to(_SRC)}:{lineno}: {joined}")
    assert not offenders, (
        "hardcoded true-peak literal(s) — import "
        "audio_loudness.NORMALISE_TARGET_TRUE_PEAK_DBTP instead:\n  "
        + "\n  ".join(offenders)
    )


def test_the_literal_scan_actually_sees_code() -> None:
    """Guard the guard: a tokenize failure would make the scan above vacuous."""
    lines = _code_lines(_SRC / "media" / "post_render_transform.py")
    assert len(lines) > 100, f"tokenize returned {len(lines)} tokens — scan is dead"
