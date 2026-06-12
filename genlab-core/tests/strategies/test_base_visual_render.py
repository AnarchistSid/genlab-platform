"""R-70 part 2 — unit tests for ``BaseVisualRenderStrategy``.

PR 1 shipped the thin skeleton with one concrete method
(``_get_whisper_config``); PR 4 lifted ``_compose_frame`` into the
base after SR + CW + FD bodies were verified byte-identical except
for the ``[movies]`` / ``[sports]`` / ``[anime]`` log prefix. These
tests pin:

  1. A subclass that implements every remaining abstract method
     instantiates cleanly — the abstract contract is what the
     design plan says it is.
  2. ``_get_whisper_config`` reads the right path through
     ``_visuals_config`` (``animation.word_by_word.whisper_sync``)
     and returns the documented default when any key is missing.
  3. ``_get_whisper_config`` calls ``_ensure_config`` exactly once
     per invocation — the subclass's own config-loading caching is
     what makes the call idempotent; the base doesn't second-guess.
  4. ``_compose_frame`` honours the subclass-provided
     ``_log_prefix`` + ``_visuals_yaml_path``, guards an empty
     ``run_dir``, caps duration at 60s, falls back to 55s on probe
     failure, and returns an empty string when the compositor
     raises.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.strategies.base_visual_render import BaseVisualRenderStrategy


class _StubVisualRender(BaseVisualRenderStrategy):
    """Concrete subclass with every remaining abstract method stubbed
    for tests. PR 4 removed ``_compose_frame`` from the abstract list
    (it now lives concretely on the base); the stub no longer
    overrides it."""

    def __init__(
        self,
        visuals_config: dict | None = None,
        *,
        log_prefix: str = "[stub]",
        visuals_yaml_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._injected_config = visuals_config or {}
        self.ensure_call_count = 0
        self._log_prefix = log_prefix
        self._visuals_yaml_path = visuals_yaml_path or Path("/tmp/stub_visuals.yaml")

    def _ensure_config(self) -> None:
        self.ensure_call_count += 1
        self._visuals_config = self._injected_config

    def prepare_whisper_words(self, clip_path, story):
        return []

    def _build_pexels_queries(self, story):
        return []

    def _render_story(self, story):
        return {"status": "stub"}

    def execute(self, context):
        return context


def test_r70_pr1_subclass_with_full_abstract_impl_instantiates() -> None:
    """The abstract contract is what the design plan documents:
    one ``_ensure_config`` + five render methods + ``execute``.
    A subclass that provides all 7 instantiates cleanly."""
    stage = _StubVisualRender()
    assert stage._visuals_config is None  # set by _ensure_config on first use


def test_r70_pr1_cannot_instantiate_base_directly() -> None:
    """The base is abstract — direct instantiation must fail. Pin
    that so a future "let's make this a concrete fallback" edit
    can't silently weaken the contract."""
    with pytest.raises(TypeError, match="abstract"):
        BaseVisualRenderStrategy()  # type: ignore[abstract]


def test_r70_pr1_get_whisper_config_reads_documented_path() -> None:
    """Path: ``animation.word_by_word.whisper_sync`` — the documented
    shape of the three channels' ``visuals.yaml`` files."""
    cfg = {
        "animation": {
            "word_by_word": {
                "whisper_sync": {"enabled": True, "model": "small"},
            },
        },
    }
    stage = _StubVisualRender(visuals_config=cfg)
    assert stage._get_whisper_config() == {"enabled": True, "model": "small"}


def test_r70_pr1_get_whisper_config_returns_documented_default_when_missing() -> None:
    """When ANY key on the path is missing, return ``{"enabled":
    False}`` — the documented default that all three production
    channels relied on."""
    # Missing 'animation' key
    assert _StubVisualRender(visuals_config={})._get_whisper_config() == {"enabled": False}
    # Missing 'word_by_word' key
    assert _StubVisualRender(visuals_config={"animation": {}})._get_whisper_config() == {
        "enabled": False
    }
    # Missing 'whisper_sync' key
    assert _StubVisualRender(
        visuals_config={"animation": {"word_by_word": {}}}
    )._get_whisper_config() == {"enabled": False}


def test_r70_pr1_get_whisper_config_calls_ensure_config_once_per_invocation() -> None:
    """The base's ``_get_whisper_config`` calls ``_ensure_config()``
    at the top of every invocation — the subclass's own caching is
    what makes that cheap, not base-level memoization. Pin that
    contract so a 'helpful' future edit doesn't silently break
    subclasses that rely on _ensure_config being called per-use
    (e.g., for hot config reload)."""
    stage = _StubVisualRender(visuals_config={"animation": {"word_by_word": {}}})
    stage._get_whisper_config()
    stage._get_whisper_config()
    stage._get_whisper_config()
    assert stage.ensure_call_count == 3


def test_r70_pr1_get_whisper_config_tolerates_none_visuals_config() -> None:
    """If a buggy subclass forgets to set ``_visuals_config`` inside
    ``_ensure_config``, the base must still return the documented
    default rather than ``AttributeError`` on a None ``.get()``."""

    class _BrokenStub(_StubVisualRender):
        def _ensure_config(self) -> None:
            # Intentionally doesn't set _visuals_config.
            self.ensure_call_count += 1

    assert _BrokenStub()._get_whisper_config() == {"enabled": False}


def test_r70_pr1_base_subclasses_visual_render_strategy() -> None:
    """The base must inherit the existing
    ``VisualRenderStrategy(ABC)`` from interfaces.py, NOT introduce
    a parallel class hierarchy. Pin that so any subclass of the
    base also passes ``isinstance(x, VisualRenderStrategy)``
    checks that callers rely on."""
    from genlab_core.strategies.interfaces import VisualRenderStrategy

    stage: Any = _StubVisualRender()
    assert isinstance(stage, VisualRenderStrategy)


# ── R-70 part 2 PR 4: concrete ``_compose_frame`` on the base ──────


def _make_clip(tmp_path: Path) -> Path:
    """Realish source clip path — content irrelevant since the
    compositor is mocked; only the path needs to exist on disk."""
    clip = tmp_path / "src.mp4"
    clip.write_bytes(b"")
    return clip


def test_r70_pr4_compose_frame_returns_empty_string_when_no_run_dir(
    tmp_path: Path,
) -> None:
    """The pre-extraction body's first guard: if ``run_dir`` is
    missing from context, return ``""`` without touching the
    compositor. Pin so a future "let's default to cwd" edit
    can't silently change behavior."""
    stage = _StubVisualRender()
    clip = _make_clip(tmp_path)
    result = stage._compose_frame(clip, story={"story_id": "s1"}, context={})
    assert result == ""


def test_r70_pr4_compose_frame_wires_compositor_with_visuals_yaml_path(
    tmp_path: Path,
) -> None:
    """The base must instantiate FrameCompositor from the subclass-
    provided ``_visuals_yaml_path`` — not from a hardcoded constant
    or a re-derived path. Pin so per-channel overrides survive
    a future refactor."""
    visuals_yaml = tmp_path / "channel_visuals.yaml"
    visuals_yaml.write_text("animation: {}")
    stage = _StubVisualRender(visuals_yaml_path=visuals_yaml)
    clip = _make_clip(tmp_path)
    run_dir = tmp_path / "run"

    mock_compositor = MagicMock()
    mock_compositor.compose.return_value = "/tmp/out.mp4"

    with (
        patch("genlab_core.strategies.base_visual_render.FrameCompositor") as MockFC,
        patch("genlab_core.media.frame_compositor.probe_video") as mock_probe,
    ):
        MockFC.from_visuals_yaml.return_value = mock_compositor
        mock_probe.return_value = MagicMock(duration_seconds=22.0)

        result = stage._compose_frame(
            clip,
            story={"story_id": "s1", "content": {"hook": "H"}},
            context={"run_dir": str(run_dir)},
        )

    MockFC.from_visuals_yaml.assert_called_once_with(str(visuals_yaml))
    assert result == "/tmp/out.mp4"
    # Pinned hook + duration plumbing
    args, kwargs = mock_compositor.compose.call_args
    assert kwargs["hook_text"] == "H"
    assert kwargs["duration_seconds"] == 22


def test_r70_pr4_compose_frame_caps_duration_at_60s(tmp_path: Path) -> None:
    """A 90s clip must be capped to 60s when sent to the compositor —
    matches the 60s short-form publish ceiling. Pin so a future
    "let's keep long-form" edit can't silently lift the cap."""
    stage = _StubVisualRender()
    clip = _make_clip(tmp_path)
    run_dir = tmp_path / "run"

    mock_compositor = MagicMock()
    mock_compositor.compose.return_value = "/tmp/out.mp4"

    with (
        patch("genlab_core.strategies.base_visual_render.FrameCompositor") as MockFC,
        patch("genlab_core.media.frame_compositor.probe_video") as mock_probe,
    ):
        MockFC.from_visuals_yaml.return_value = mock_compositor
        mock_probe.return_value = MagicMock(duration_seconds=90.0)

        stage._compose_frame(
            clip,
            story={"story_id": "s1"},
            context={"run_dir": str(run_dir)},
        )

    _, kwargs = mock_compositor.compose.call_args
    assert kwargs["duration_seconds"] == 60


def test_r70_pr4_compose_frame_falls_back_to_55s_when_probe_fails(
    tmp_path: Path,
) -> None:
    """When ``probe_video`` raises, the body uses 55s as the safe
    fallback duration. Pin so a refactor doesn't silently make a
    probe failure a hard failure."""
    stage = _StubVisualRender()
    clip = _make_clip(tmp_path)
    run_dir = tmp_path / "run"

    mock_compositor = MagicMock()
    mock_compositor.compose.return_value = "/tmp/out.mp4"

    with (
        patch("genlab_core.strategies.base_visual_render.FrameCompositor") as MockFC,
        patch(
            "genlab_core.media.frame_compositor.probe_video",
            side_effect=RuntimeError("no ffprobe"),
        ),
    ):
        MockFC.from_visuals_yaml.return_value = mock_compositor

        stage._compose_frame(
            clip,
            story={"story_id": "s1"},
            context={"run_dir": str(run_dir)},
        )

    _, kwargs = mock_compositor.compose.call_args
    assert kwargs["duration_seconds"] == 55


def test_r70_pr4_compose_frame_falls_back_to_title_when_hook_missing(
    tmp_path: Path,
) -> None:
    """The original per-channel body read
    ``story.content.hook`` then fell back to ``story.title``. Pin
    that the lifted body keeps the same precedence — channels rely
    on it when the hook stage hasn't run yet."""
    stage = _StubVisualRender()
    clip = _make_clip(tmp_path)
    run_dir = tmp_path / "run"

    mock_compositor = MagicMock()
    mock_compositor.compose.return_value = "/tmp/out.mp4"

    with (
        patch("genlab_core.strategies.base_visual_render.FrameCompositor") as MockFC,
        patch("genlab_core.media.frame_compositor.probe_video") as mock_probe,
    ):
        MockFC.from_visuals_yaml.return_value = mock_compositor
        mock_probe.return_value = MagicMock(duration_seconds=20.0)

        stage._compose_frame(
            clip,
            story={"story_id": "s1", "title": "From title"},
            context={"run_dir": str(run_dir)},
        )

    _, kwargs = mock_compositor.compose.call_args
    assert kwargs["hook_text"] == "From title"


def test_r70_pr4_compose_frame_returns_empty_string_on_compositor_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If FrameCompositor.compose raises, the body must log the
    channel-tagged error and return ``""`` — never propagate the
    exception (the pipeline relies on this to keep the render stage
    moving across stories). Pin the log-prefix routing while we're
    here: the base must use the subclass's ``_log_prefix`` literally."""
    stage = _StubVisualRender(log_prefix="[movies]")
    clip = _make_clip(tmp_path)
    run_dir = tmp_path / "run"

    with (
        caplog.at_level(logging.ERROR, logger="genlab_core.strategies.base_visual_render"),
        patch("genlab_core.strategies.base_visual_render.FrameCompositor") as MockFC,
    ):
        MockFC.from_visuals_yaml.side_effect = ValueError("bad yaml")
        result = stage._compose_frame(
            clip,
            story={"story_id": "s1"},
            context={"run_dir": str(run_dir)},
        )

    assert result == ""
    # The log must carry the channel-specific prefix the subclass set.
    assert any("[movies]" in rec.message for rec in caplog.records)
    assert any("FrameCompositor failed" in rec.message for rec in caplog.records)


def test_compose_frame_persists_render_error_on_story_for_diagnostics(
    tmp_path: Path,
) -> None:
    """The silent-failure visibility fix: when ``_compose_frame``
    catches an exception, it must persist the exception string to
    ``story["media"]["render_error"]`` so ``push_to_backlog`` can
    write it to ``blueprint.error_message``. Without this, the
    stuck DRAFTED rows have NULL error_message and no diagnostic
    trail — the production failure mode the 2026-05-21 incident
    surfaced."""
    stage = _StubVisualRender(log_prefix="[movies]")
    clip = _make_clip(tmp_path)
    run_dir = tmp_path / "run"
    story = {"story_id": "s1"}

    with patch("genlab_core.strategies.base_visual_render.FrameCompositor") as MockFC:
        MockFC.from_visuals_yaml.side_effect = ValueError("bad visuals yaml")
        result = stage._compose_frame(clip, story=story, context={"run_dir": str(run_dir)})

    assert result == ""
    # Exception string preserved on the story for the downstream
    # error_message writer.
    assert "render_error" in story.get("media", {})
    assert "bad visuals yaml" in story["media"]["render_error"]


def test_compose_frame_render_error_truncated_to_400_chars(
    tmp_path: Path,
) -> None:
    """A pathological exception string must not blow past the
    column-budget for ``blueprint.error_message`` (~500 chars after
    the ``render:compositor_failed:`` prefix is added by
    push_to_backlog's helper). Cap at 400 here so the prefix fits."""
    stage = _StubVisualRender(log_prefix="[movies]")
    clip = _make_clip(tmp_path)
    run_dir = tmp_path / "run"
    story: dict = {"story_id": "s1"}
    huge = "x" * 5000

    with patch("genlab_core.strategies.base_visual_render.FrameCompositor") as MockFC:
        MockFC.from_visuals_yaml.side_effect = RuntimeError(huge)
        stage._compose_frame(clip, story=story, context={"run_dir": str(run_dir)})

    assert len(story["media"]["render_error"]) == 400


def test_compose_frame_no_render_error_on_success(tmp_path: Path) -> None:
    """The happy path must NOT set ``render_error`` — pin so a
    refactor doesn't accidentally start setting it on every call
    (which would then look like a render failure in the dashboard
    explainer)."""
    stage = _StubVisualRender(log_prefix="[movies]")
    clip = _make_clip(tmp_path)
    run_dir = tmp_path / "run"
    story: dict = {"story_id": "s1"}

    mock_compositor = MagicMock()
    mock_compositor.compose.return_value = "/tmp/out.mp4"
    with (
        patch("genlab_core.strategies.base_visual_render.FrameCompositor") as MockFC,
        patch("genlab_core.media.frame_compositor.probe_video") as mock_probe,
    ):
        MockFC.from_visuals_yaml.return_value = mock_compositor
        mock_probe.return_value = MagicMock(duration_seconds=20.0)
        stage._compose_frame(clip, story=story, context={"run_dir": str(run_dir)})

    assert "render_error" not in story.get("media", {})
