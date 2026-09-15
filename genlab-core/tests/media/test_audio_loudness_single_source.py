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


class TestRepairChainIsTruePeakAware:
    """A true-peak failure cannot be repaired by a sample-peak-only chain.

    2026-09-15. _fix_loudness ran loudnorm only when the issue list contained
    `loudness_off`. For a pure `true_peak_over` it applied alimiter alone --
    and alimiter caps SAMPLE peaks, while inter-sample peaks ride above them by
    more the harder it works.

    Measured on an anime asset that arrived clipping at +0.63 dBTP:

        input                      -14.08 LUFS  +0.63 dBTP   fail
        limiter only  (old)        -14.17 LUFS  -0.63 dBTP   STILL FAILS
        loudnorm + limiter (new)   -14.30 LUFS  -2.23 dBTP   passes

    Limiting to a -2.0 dBFS sample ceiling left 1.37 dB of inter-sample
    overshoot. All four anime renders on the 08:13Z fire failed this way --
    "0 passed (0 auto-fixed), 4 failed" with a repair attempted on every one.

    It survived earlier because the movies and ai_creators assets repaired the
    same morning arrived at -0.93 and -0.64 dBTP, near enough that the limiter
    alone cleared the gate. The 0.3-0.45 dB margin measured there was overshoot
    on LOUDNORM output and never generalised to heavily-limited output.
    """

    @staticmethod
    def _chain_for(issues: list[str]) -> str:
        import inspect

        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        return inspect.getsource(ValidateVideos._fix_loudness)

    def test_loudnorm_is_unconditional(self) -> None:
        """Not gated on loudness_off. The gate it must satisfy is true peak."""
        src = self._chain_for(["true_peak_over:+0.63dBTP"])
        assert "chain: list[str] = [" in src, (
            "the repair chain must be built unconditionally; a conditional "
            "loudnorm means a pure true-peak failure gets a sample-peak-only "
            "repair, which measurably cannot fix it"
        )
        assert "if loud_off:\n            chain.append(" not in src, (
            "loudnorm is conditional again — see the +0.63 dBTP measurement above"
        )

    def test_limiter_still_runs_after_loudnorm(self) -> None:
        from genlab_core.media import audio_loudness

        chain = ",".join([audio_loudness.loudnorm_filter(), audio_loudness.limiter_filter()])
        assert chain.index("loudnorm") < chain.index("alimiter")

    def test_repair_target_sits_under_the_gate(self) -> None:
        """The repair must aim below the threshold it has to clear."""
        from genlab_core.media import audio_loudness
        from genlab_core.pipeline.stages.validate_videos import SPEC

        assert audio_loudness.NORMALISE_TARGET_TRUE_PEAK_DBTP < SPEC["max_true_peak"]


class TestRepairIsTwoPass:
    """The repair must MEASURE the asset, not predict it.

    PUBLISH-04 made loudnorm unconditional and called it single-pass. That
    fixed true-peak failures by creating loudness failures:

        input        -14.57 LUFS  -0.72 dBTP   fail (true peak)
        single-pass  -15.83 LUFS  -2.61 dBTP   FAIL (loudness_off)
        two-pass     -14.40 LUFS  -1.80 dBTP   passes both

    All four sports renders on the 09:31Z fire failed the new way. The anime
    asset that motivated PUBLISH-04 passed single-pass (-14.08 -> -14.30) purely
    by luck of where it started.
    """

    def test_repair_runs_the_analysis_pass(self) -> None:
        import inspect

        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        src = inspect.getsource(ValidateVideos._fix_loudness)
        assert "_measure_loudness" in src, (
            "the repair must run an analysis pass; single-pass loudnorm predicts "
            "its targets and can miss integrated loudness by >1 LU"
        )
        assert "loudnorm_filter(measured=measured)" in src, (
            "measured values must be threaded into the filter, or the analysis "
            "pass is computed and thrown away"
        )

    def test_analysis_failure_warns_rather_than_silently_degrading(self) -> None:
        import inspect

        from genlab_core.pipeline.stages.validate_videos import ValidateVideos

        src = inspect.getsource(ValidateVideos._fix_loudness)
        # Normalise implicit string concatenation + line wrapping before
        # matching: the message is split across source lines, so a contiguous
        # phrase match fails against code that is perfectly correct.
        flat = " ".join(src.split())
        assert "single-pass" in flat and "logger.warning" in flat, (
            "a silent single-pass fallback is indistinguishable from a working "
            "two-pass repair until the gate fails (rule #19)"
        )

    def test_two_pass_filter_carries_every_measured_field(self) -> None:
        """A missing field makes linear mode silently revert to dynamic."""
        from genlab_core.media import audio_loudness

        measured = {
            "input_i": "-14.57", "input_tp": "-0.72", "input_lra": "6.90",
            "input_thresh": "-24.83", "target_offset": "1.47",
        }
        f = audio_loudness.loudnorm_filter(measured=measured)
        for key in ("measured_I", "measured_TP", "measured_LRA",
                    "measured_thresh", "offset", "linear=true"):
            assert key in f, f"{key} missing from the two-pass filter"
