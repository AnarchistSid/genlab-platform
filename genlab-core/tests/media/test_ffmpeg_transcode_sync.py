"""R-56: transcode_for_platforms_sync forwarded args positionally to an async
function with a DIFFERENT parameter order, so output_dir bound to `platforms`
and vice-versa. Forwarding is now by keyword — assert the binding is correct.
"""

from __future__ import annotations

from pathlib import Path

import genlab_core.media.ffmpeg as ff


def test_sync_wrapper_does_not_swap_platforms_and_output_dir(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    async def _fake(master, platforms, output_dir, use_gpu=True):  # noqa: ANN001
        captured.update(master=master, platforms=platforms, output_dir=output_dir)
        return {"ok": output_dir}

    monkeypatch.setattr(ff, "transcode_for_platforms", _fake)

    master = tmp_path / "master.mp4"
    output_dir = tmp_path / "out"
    platforms = [next(iter(ff.PLATFORM_SPECS))]

    ff.transcode_for_platforms_sync(master, output_dir, platforms)

    assert captured["master"] == master
    assert captured["output_dir"] == output_dir  # NOT the platforms list
    assert captured["platforms"] == platforms  # NOT the output_dir path


def test_sync_wrapper_defaults_platforms_to_all(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    async def _fake(master, platforms, output_dir, use_gpu=True):  # noqa: ANN001
        captured["platforms"] = platforms
        return {}

    monkeypatch.setattr(ff, "transcode_for_platforms", _fake)
    ff.transcode_for_platforms_sync(tmp_path / "m.mp4", tmp_path / "out", None)

    assert captured["platforms"] == list(ff.PLATFORM_SPECS.keys())
