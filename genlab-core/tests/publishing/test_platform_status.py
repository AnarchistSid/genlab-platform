"""Tests for :mod:`genlab_core.publishing.platform_status`.

Lives next to its module home so the regression surface is right next to
the code under test, not embedded in the omnibus
``test_publish_all_platforms``. Covers the three pure-function helpers
extracted in PR 1/N of the publish_all_platforms decomposition.
"""

from __future__ import annotations

import json

import pytest
from genlab_core.publishing.platform_status import (
    PLATFORM_ID_MAP,
    TERMINAL_ATTEMPT_THRESHOLD,
    TERMINAL_ERROR_CLASSES,
    already_published_platforms,
    terminal_failed_platforms,
    to_registry_id,
)


class TestToRegistryId:
    @pytest.mark.parametrize(
        "alias,canonical",
        [
            ("twitter", "x_twitter"),
            ("x", "x_twitter"),
            ("ig", "instagram"),
            ("yt", "youtube"),
            ("fb", "facebook"),
            ("tt", "tiktok"),
        ],
    )
    def test_known_aliases_route_to_canonical(self, alias: str, canonical: str) -> None:
        assert to_registry_id(alias) == canonical

    def test_already_canonical_passes_through(self) -> None:
        assert to_registry_id("youtube") == "youtube"
        assert to_registry_id("instagram") == "instagram"

    def test_unknown_name_passes_through(self) -> None:
        """No silent re-routing — an unknown name returns unchanged so the
        downstream registry lookup can fail loud with the typo."""
        assert to_registry_id("publishng") == "publishng"


class TestAlreadyPublishedPlatforms:
    def test_empty_fields_returns_empty_set(self) -> None:
        assert already_published_platforms({}) == set()

    def test_bare_string_published_value(self) -> None:
        """Legacy shape: ``{"youtube": "PUBLISHED"}`` (no dict wrapper).
        Important because partial-publish recoveries can leave both shapes
        in the same record over a blueprint's lifetime."""
        fields = {"platform_publish_status": json.dumps({"youtube": "PUBLISHED"})}
        assert already_published_platforms(fields) == {"youtube"}

    def test_dict_shape_published_value(self) -> None:
        """Current shape: ``{"youtube": {"status": "PUBLISHED", ...}}``."""
        fields = {
            "platform_publish_status": json.dumps(
                {"youtube": {"status": "PUBLISHED", "post_id": "abc"}}
            )
        }
        assert already_published_platforms(fields) == {"youtube"}

    def test_mixed_shapes_in_one_record(self) -> None:
        fields = {
            "platform_publish_status": json.dumps(
                {
                    "youtube": "PUBLISHED",
                    "instagram": {"status": "PUBLISHED"},
                    "facebook": {"status": "FAILED"},
                    "twitter": "FAILED",
                }
            )
        }
        assert already_published_platforms(fields) == {"youtube", "instagram"}

    def test_already_parsed_dict_no_json_re_parse_needed(self) -> None:
        fields = {"platform_publish_status": {"youtube": "PUBLISHED"}}
        assert already_published_platforms(fields) == {"youtube"}

    def test_malformed_json_returns_empty_set(self) -> None:
        """A junk value never crashes the publisher — we'd rather miss the
        already-published guard (and risk an idempotent double-post that
        the platform itself dedups) than abort an entire run."""
        fields = {"platform_publish_status": "{not: valid json"}
        assert already_published_platforms(fields) == set()

    def test_non_dict_top_level_returns_empty_set(self) -> None:
        """JSON parses to a list/string — also returns empty set."""
        fields = {"platform_publish_status": json.dumps(["youtube"])}
        assert already_published_platforms(fields) == set()


class TestTerminalFailedPlatforms:
    @pytest.mark.parametrize("error_class", sorted(TERMINAL_ERROR_CLASSES))
    def test_non_retryable_error_class_is_terminal(self, error_class: str) -> None:
        status = {"youtube": {"status": "FAILED", "error_class": error_class, "attempts": 1}}
        assert "youtube" in terminal_failed_platforms(status)

    def test_attempt_threshold_makes_transient_terminal(self) -> None:
        """A TRANSIENT failure stays terminal once it hits the attempt cap —
        the retry loop refuses to spin on a permanently-broken platform."""
        status = {
            "youtube": {
                "status": "FAILED",
                "error_class": "TRANSIENT",
                "attempts": TERMINAL_ATTEMPT_THRESHOLD,
            }
        }
        assert "youtube" in terminal_failed_platforms(status)

    def test_low_attempt_transient_not_terminal(self) -> None:
        status = {"youtube": {"status": "FAILED", "error_class": "TRANSIENT", "attempts": 1}}
        assert "youtube" not in terminal_failed_platforms(status)

    def test_default_error_class_treated_as_transient(self) -> None:
        """Missing ``error_class`` defaults to TRANSIENT — only the attempt
        count can promote it to terminal."""
        status = {"youtube": {"status": "FAILED", "attempts": 1}}
        assert "youtube" not in terminal_failed_platforms(status)

    def test_published_platform_not_in_failed_set(self) -> None:
        status = {"youtube": {"status": "PUBLISHED"}}
        assert terminal_failed_platforms(status) == {}

    def test_non_dict_value_ignored(self) -> None:
        """Legacy bare-string entries don't carry attempts/error_class —
        they can't be terminal-by-our-rules; ignore them rather than crash."""
        status = {"youtube": "FAILED"}
        assert terminal_failed_platforms(status) == {}


class TestPlatformIdMap:
    def test_all_aliases_lower_case(self) -> None:
        """The map keys are case-sensitive — blueprints store platform names
        lowercase. A capital-letter alias would silently route nowhere."""
        for k in PLATFORM_ID_MAP:
            assert k == k.lower(), f"alias {k!r} must be lowercase"

    def test_canonical_targets_are_all_distinct_or_intentional(self) -> None:
        """``twitter`` and ``x`` both map to ``x_twitter``; that's the only
        many-to-one. Catches an accidental future alias that collides with
        an existing canonical name."""
        targets = set(PLATFORM_ID_MAP.values())
        # Six aliases, five distinct canonical targets (twitter + x share).
        assert len(targets) == 5
