"""Pin: per-platform bandit arm helpers (PR Z).

Per the AGENT-LEARNING-ENGINES synthesis: per-platform bandit arms
are the highest-leverage "make the agent smarter" move because:

1. The ``content_type__platform`` format is already canonical at
   ``meta_prior.py:100``. We're propagating an existing convention
   to the OTHER class of arms.
2. ``feedback_registration.py:234`` already wires per-platform
   hour-arms (``hour:{publish_hour}:{platform}:{niche_id}``). We're
   reusing the established pattern.
3. ``metric_collector.py:962`` already knows the platform at reward
   attribution time. The wire is one extra ``bandit.update()`` call.

These pins lock in the helper module's pure contract:

  Format (3):
  1. platform_arm_id composes base + platform with doubled underscore
  2. parse_platform_arm_id round-trips platform_arm_id correctly
  3. parse_platform_arm_id preserves complex base names with ``__``

  Parser rejection (2):
  4. Unknown trailing segment → no platform (treated as legacy base)
  5. No double underscore at all → no platform

  Opt-in (3):
  6. is_enabled default OFF — env unset
  7. is_enabled strict "1" only — "true" stays OFF
  8. is_enabled "1" → ON

  Expand (3):
  9. list_split_platforms pinned to (instagram, youtube) — silent
     platform additions break tests
  10. expand_arm_for_platforms OFF → just [base]
  11. expand_arm_for_platforms ON → [base, base__instagram, base__youtube]

  Doubled-underscore edge (1):
  12. platform_arm_id handles base names that already contain ``__``
"""

from __future__ import annotations

from genlab_core.learning.bandit_platform_split import (
    expand_arm_for_platforms,
    is_enabled,
    list_split_platforms,
    parse_platform_arm_id,
    platform_arm_id,
)


class TestPlatformArmIdFormat:
    def test_platform_arm_id_format(self):
        """Pin: doubled-underscore convention matches meta_prior.py:100."""
        assert platform_arm_id("gameplay_clip", "instagram") == "gameplay_clip__instagram"
        assert platform_arm_id("gameplay_clip", "youtube") == "gameplay_clip__youtube"

    def test_parse_platform_arm_id_roundtrip(self):
        """Pin: parse(platform_arm_id(x, p)) == (x, p) for both platforms."""
        for base in ("gameplay_clip", "style:gaming:revelation", "default"):
            for platform in list_split_platforms():
                composed = platform_arm_id(base, platform)
                assert parse_platform_arm_id(composed) == (base, platform), (
                    f"round-trip failed for ({base}, {platform})"
                )

    def test_round_trip_preserves_complex_base_arm_id(self):
        """Pin: base names with colons (style:gaming:revelation) survive."""
        base = "style:gaming:revelation"
        composed = platform_arm_id(base, "instagram")
        assert composed == "style:gaming:revelation__instagram"
        assert parse_platform_arm_id(composed) == (base, "instagram")


class TestParserRejection:
    def test_parse_unknown_platform_returns_none(self):
        """Pin: trailing segment not in split set → treated as opaque base.

        Prevents misclassifying a legacy arm name that happens to
        contain ``__foo`` as a per-platform arm. ``foo`` is not in
        :func:`list_split_platforms`, so the whole string is returned
        as the base with platform=None.
        """
        assert parse_platform_arm_id("foo__bar") == ("foo__bar", None)
        assert parse_platform_arm_id("clip__facebook") == ("clip__facebook", None)
        assert parse_platform_arm_id("clip__twitter") == ("clip__twitter", None)

    def test_parse_no_double_underscore_returns_none(self):
        """Pin: legacy arm names with no separator → platform=None."""
        assert parse_platform_arm_id("style:gaming:revelation") == (
            "style:gaming:revelation",
            None,
        )
        assert parse_platform_arm_id("gameplay_clip") == ("gameplay_clip", None)
        assert parse_platform_arm_id("default") == ("default", None)


class TestIsEnabled:
    def test_is_enabled_default_off(self, monkeypatch):
        """Pin: env unset → False. Default OFF preserves all behavior."""
        monkeypatch.delenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", raising=False)
        assert is_enabled() is False

    def test_is_enabled_accepts_env_true_semantics(self, monkeypatch):
        """2026-07-14: migrated to canonical env_true (accepts
        1/true/yes/on, case-insensitive). Prior 'strict-1-only'
        was inconsistent with the rest of the codebase — operator
        setting =true would silently no-op. Class-of-bug from the
        session's flag-divergence audit."""
        for truthy_value in ("1", "true", "yes", "on", "TRUE", "True", "y", "t"):
            monkeypatch.setenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", truthy_value)
            assert is_enabled() is True, (
                f"is_enabled must be True for {truthy_value!r} under env_true"
            )
        for falsy_value in ("0", "false", "no", "off", "", "anything"):
            monkeypatch.setenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", falsy_value)
            assert is_enabled() is False, f"is_enabled must be False for {falsy_value!r}"


class TestListSplitPlatforms:
    def test_list_split_platforms_is_ig_yt_only(self):
        """Pin: only instagram + youtube split for now.

        Silent platform additions break this pin — adding a platform
        changes the arm-row count across all 5 niches and should be
        a deliberate decision documented alongside this test update.
        See the module docstring for the rationale.
        """
        assert list_split_platforms() == ("instagram", "youtube")


class TestExpandArmForPlatforms:
    def test_expand_arm_for_platforms_off(self, monkeypatch):
        """Pin: flag off → just [base_arm_id]. Caller's loop is unchanged."""
        monkeypatch.delenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", raising=False)
        assert expand_arm_for_platforms("gameplay_clip") == ["gameplay_clip"]

    def test_expand_arm_for_platforms_on(self, monkeypatch):
        """Pin: flag on → [base, base__instagram, base__youtube] in order.

        Order matters: tests downstream depend on iteration order
        being stable across runs (no dict.values()-style flake).
        """
        monkeypatch.setenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", "1")
        assert expand_arm_for_platforms("gameplay_clip") == [
            "gameplay_clip",
            "gameplay_clip__instagram",
            "gameplay_clip__youtube",
        ]


class TestDoubledUnderscoreEdge:
    def test_platform_arm_id_handles_existing_doubled_underscore(self):
        """Pin: base names that already contain ``__`` are left as-is.

        ``platform_arm_id("a__b", "youtube") → "a__b__youtube"``. Only
        the LAST ``__`` segment is treated as the platform marker by
        :func:`parse_platform_arm_id`. Even though this base name
        shape is unusual (we don't generate it ourselves), the helper
        contract has to be deterministic so a hypothetical regression
        is caught at CI time.
        """
        composed = platform_arm_id("a__b", "youtube")
        assert composed == "a__b__youtube"
        # And the parser strips only the last segment, preserving the
        # "a__b" base. The middle ``__`` was never a platform marker.
        assert parse_platform_arm_id(composed) == ("a__b", "youtube")
