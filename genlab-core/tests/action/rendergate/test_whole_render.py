"""THE gate the packet specified for Port 4: re-render and compare.

It was declared unrunnable and replaced with function-level equivalence. That
was wrong -- the material had simply not been searched for properly. It is
runnable, it has been run, and it found a defect that the per-effect tests at
one ULP could not:

    `grade_world` read `vignette_amt` from the kit only. Vignette is solved PER
    CLIP alongside world/subject luma -- UFC-05 solved 0.30, WWE v6 ran
    grade_bathed's 0.62 -- so the port could not reproduce any reel but the one
    whose value happened to be in the kit. 18.7/255 on EVERY frame.

That is the whole argument for a whole-pipeline gate. Per-effect equivalence
passes the same arguments to both sides by construction, so it can never catch
an argument that the renderer should have passed and didn't.

MEASURED RESULT after the fix (96 frames, WWE v6 hit3s):

    frames bit-exact          85 / 96
    frames within 1 level     93 / 96
    worst frame                2 / 255
    mean abs difference    0.00001
    PSNR                     97.96 dB     (packet gate: >= 35 dB)

The residual is `push()`'s float->uint8 round-trip: a value sitting at x.5
rounds either way on a 1e-7 difference. It is not in the effects.

THE 35 dB GATE IS TOO LOOSE, MEASURED
-------------------------------------
Mutation check, dropping `bloom` entirely from the compose chain:

    PSNR                     74.70 dB     -- still TWICE the stated gate
    worst frame                5 / 255
    frames bit-exact          59 / 96

So PSNR >= 35 dB would have passed a render with an effect missing. It is a
weak instrument here because the frames are graded dark (world luma ~37/255)
and `bloom` only touches highlights above 0.85, so a whole missing effect moves
very few pixels. The assertions below are therefore on WORST-FRAME LEVELS and
BIT-EXACT COUNT, which both catch the mutation; PSNR is kept only because the
packet named it.

OPT-IN: two full renders at ~220 s each. Set GENLAB_RENDER_GATE=1 to run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[4]
_V6 = _ROOT / ".deliverables" / "clutchwire_action_hit3s_v6"

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("GENLAB_RENDER_GATE") != "1",
        reason="opt-in: two full renders (~7 min). Set GENLAB_RENDER_GATE=1",
    ),
    pytest.mark.skipif(not (_V6 / "inputs").is_dir(), reason="v6 archive not present"),
]

PSNR_GATE = 35.0
MAX_LEVELS = 2


def _stage(tmp: Path) -> Path:
    """Lay the archived inputs out the way the approved build expects them."""
    work = tmp / "work"
    (work / "v7").mkdir(parents=True)
    (work / "v10").mkdir(parents=True)
    (work / "v9" / "models").mkdir(parents=True)
    src = _V6 / "inputs"
    (work / "v7" / "clean").symlink_to(src / "clean")
    (work / "v7" / "plan.json").symlink_to(src / "plan.json")
    (work / "v10" / "matte").symlink_to(src / "matte")
    (work / "v9" / "draw024_mask.png").symlink_to(src / "draw024_mask.png")
    (work / "v9" / "models" / "yunet.onnx").symlink_to(src / "models" / "yunet.onnx")
    return work


def _render(work: Path, shim=None) -> np.ndarray:
    """Run the APPROVED build. With `shim`, its effect modules are swapped for
    the port -- so the compose order and the arguments come from the build
    itself, which is exactly what a re-description gets wrong."""
    cwd = Path.cwd()
    os.chdir(work)
    saved_argv, saved_path = sys.argv[:], sys.path[:]
    try:
        sys.argv = ["v10_build", "/dev/null"]
        sys.path.insert(0, str(_V6 / "scripts"))
        for mod in ("v10_build", "v6_look", "v8_look", "v9_face"):
            sys.modules.pop(mod, None)
        import v10_build as build

        if shim is not None:
            build.L6, build.L8 = shim.L6, shim.L8
        frames, _, _ = build.render()
        return np.stack(frames, 0).astype(np.float32)
    finally:
        os.chdir(cwd)
        sys.argv, sys.path = saved_argv, saved_path


def test_whole_pipeline_render_matches_the_approved_segment(tmp_path):
    from . import port_shim

    work = _stage(tmp_path)
    oracle = _render(work)
    port = _render(work, shim=port_shim)

    assert oracle.shape == port.shape == (96, 1920, 1080, 3)
    diff = np.abs(oracle - port)
    worst = float(diff.max())
    mse = float(((oracle - port) ** 2).mean())
    psnr = 10 * np.log10(255.0**2 / max(mse, 1e-12))
    exact = int(sum(1 for i in range(len(port)) if diff[i].max() == 0.0))

    # PSNR first because the packet named it -- but it is the weakest of the
    # three: a render with `bloom` dropped entirely still scores 74.70 dB.
    assert psnr >= PSNR_GATE, f"PSNR {psnr:.2f} dB below the {PSNR_GATE} dB gate"
    # These two are what actually bite. Measured: correct port 2 levels / 85
    # exact; bloom dropped 5 levels / 59 exact.
    assert worst <= MAX_LEVELS, f"worst frame differs by {worst} levels (bloom-dropped: 5)"
    assert exact >= 80, f"only {exact}/96 frames bit-exact (bloom-dropped: 59)"
