"""Pin the caption_segments wire from writer → blueprint_context →
orchestrator → caption_animator (Task #521 follow-up).

Verifies:
  1. Orchestrator reads ``blueprint_context.get('caption_segments')``
     (list[dict] serialized shape from writer stage).
  2. Deserializes into ``list[CaptionSegment]`` before passing to
     ``apply_captions``.
  3. Malformed entries (missing text, non-dict items) are dropped
     without crashing.
  4. Missing / empty caption_segments → orchestrator skips caption
     stage cleanly (stages_skipped includes 'caption_style').
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.intelligent_transform import IntelligentTransformConfig
from genlab_core.media.transformation_orchestrator import apply_transformations
from genlab_core.media.transformation_selector import (
    TransformationChoice,
    TransformationChoices,
)


def _mk_choices() -> TransformationChoices:
    c = TransformationChoices(niche_id="gaming")
    c.choices["caption_style"] = TransformationChoice(
        dimension="caption_style",
        dimension_value="word_by_word",
        arm_id="transform__caption_style__word_by_word",
        propensity=0.5,
    )
    c.choices["caption_emphasis_color"] = TransformationChoice(
        dimension="caption_emphasis_color",
        dimension_value="yellow",
        arm_id="transform__caption_emphasis_color__yellow",
        propensity=0.5,
    )
    c.choices["caption_pacing"] = TransformationChoice(
        dimension="caption_pacing",
        dimension_value="750",
        arm_id="transform__caption_pacing__750",
        propensity=0.5,
    )
    return c


def _mk_cfg() -> IntelligentTransformConfig:
    return IntelligentTransformConfig.from_visuals_dict(
        {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": {
                    "caption_style": {
                        "enabled": True,
                        "styles": ["word_by_word"],
                    },
                    "caption_emphasis_color": {
                        "enabled": True,
                        "colors": ["yellow"],
                    },
                    "caption_pacing": {
                        "enabled": True,
                        "pacing_ms_options": [750],
                    },
                },
            }
        }
    )


class TestCaptionSegmentsWire:
    def test_serialized_segments_flow_through(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Writer's list[dict] shape must reach apply_captions as
        list[CaptionSegment]."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"x" * 2000)
        out = tmp_path / "out.mp4"

        captured_specs = []

        def _fake_apply_captions(spec):
            captured_specs.append(spec)
            spec.output_path.write_bytes(b"y" * 2048)
            return True

        with patch(
            "genlab_core.media.transformation_selector.select_transformation_dimensions",
            return_value=_mk_choices(),
        ), patch(
            "genlab_core.media.caption_animator.apply_captions",
            side_effect=_fake_apply_captions,
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=out,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_mk_cfg(),
                blueprint_context={
                    "caption_segments": [
                        {
                            "text": "OpenAI just launched",
                            "emphasis_words": ["OpenAI"],
                        },
                        {
                            "text": "the fastest AI video",
                            "emphasis_words": ["fastest"],
                        },
                    ]
                },
            )

        assert "caption_style" in result.stages_applied
        assert len(captured_specs) == 1
        spec = captured_specs[0]
        assert len(spec.segments) == 2
        assert spec.segments[0].text == "OpenAI just launched"
        assert spec.segments[0].emphasis_words == ["OpenAI"]

    def test_missing_segments_skips_caption_stage(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"x" * 2000)
        out = tmp_path / "out.mp4"

        with patch(
            "genlab_core.media.transformation_selector.select_transformation_dimensions",
            return_value=_mk_choices(),
        ):
            result = apply_transformations(
                source_video_path=source,
                output_path=out,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_mk_cfg(),
                blueprint_context={},  # no caption_segments
            )

        assert "caption_style" in result.stages_skipped
        assert "caption_style" not in result.stages_applied

    def test_malformed_entries_dropped(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Malformed items (missing text, non-dicts) don't crash the
        deserialize step — valid entries still flow through."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = tmp_path / "source.mp4"
        source.write_bytes(b"x" * 2000)
        out = tmp_path / "out.mp4"

        captured_specs = []

        def _fake_apply_captions(spec):
            captured_specs.append(spec)
            spec.output_path.write_bytes(b"y" * 2048)
            return True

        with patch(
            "genlab_core.media.transformation_selector.select_transformation_dimensions",
            return_value=_mk_choices(),
        ), patch(
            "genlab_core.media.caption_animator.apply_captions",
            side_effect=_fake_apply_captions,
        ):
            apply_transformations(
                source_video_path=source,
                output_path=out,
                niche_root=tmp_path,
                niche_id="gaming",
                config=_mk_cfg(),
                blueprint_context={
                    "caption_segments": [
                        {"text": "valid segment", "emphasis_words": []},
                        "not a dict",  # dropped
                        {"text": "", "emphasis_words": []},  # empty text
                        {"emphasis_words": ["nope"]},  # no text field
                        {"text": "another valid", "emphasis_words": ["another"]},
                    ]
                },
            )

        assert len(captured_specs) == 1
        spec = captured_specs[0]
        assert len(spec.segments) == 2
        assert spec.segments[0].text == "valid segment"
        assert spec.segments[1].text == "another valid"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
