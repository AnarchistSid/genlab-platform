"""Pin cross_channel_footer (Phase 2026-08-15 growth-flywheel):

  * Flag-off (unset/empty/"0") returns "" everywhere
  * Flag-on canary "ai_creators" only fires for ai_creators
  * Wildcard "all"/"*" fires for every niche
  * Non-FB source platforms return "" (funnel-source restriction)
  * Unknown niche returns ""
  * Same blueprint_hash → same target-platform pick (idempotency)
  * Different blueprint_hashes rotate through targets over aggregate
  * append_footer_if_enabled is a no-op when disabled
  * append_footer_if_enabled doesn't double-stack when marker present
  * Empty caption stays empty (fail-open)
"""
from __future__ import annotations

from collections import Counter

import pytest

from genlab_core.publishing.cross_channel_footer import (
    _HANDLES,
    _TARGETS_FROM_FACEBOOK,
    append_footer_if_enabled,
    build_footer,
    is_enabled_for,
)


class TestFlagSemantics:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", val)
        assert is_enabled_for("ai_creators") is False

    def test_unset_is_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", raising=False)
        assert is_enabled_for("ai_creators") is False

    @pytest.mark.parametrize("val", ["all", "*", "ALL"])
    def test_wildcard(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", val)
        for n in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert is_enabled_for(n) is True

    def test_canary_isolation(self, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_CROSS_CHANNEL_FOOTER_NICHES", "ai_creators",
        )
        assert is_enabled_for("ai_creators") is True
        assert is_enabled_for("gaming") is False
        assert is_enabled_for("movies") is False


class TestBuildFooter:
    def _on(self, monkeypatch, niches="all"):
        monkeypatch.setenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", niches)

    def test_off_returns_empty(self, monkeypatch):
        monkeypatch.delenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", raising=False)
        assert build_footer("ai_creators", "facebook", "abc") == ""

    def test_non_facebook_source_returns_empty(self, monkeypatch):
        """Only Facebook is a supported funnel source today."""
        self._on(monkeypatch)
        assert build_footer("ai_creators", "instagram", "abc") == ""
        assert build_footer("ai_creators", "youtube", "abc") == ""
        assert build_footer("ai_creators", "threads", "abc") == ""

    def test_unknown_niche_returns_empty(self, monkeypatch):
        self._on(monkeypatch)
        assert build_footer("nonexistent_niche", "facebook", "abc") == ""

    def test_empty_blueprint_hash_returns_empty(self, monkeypatch):
        self._on(monkeypatch)
        assert build_footer("ai_creators", "facebook", "") == ""

    def test_footer_contains_handle(self, monkeypatch):
        self._on(monkeypatch)
        footer = build_footer("ai_creators", "facebook", "hash_seed_1")
        assert footer, "expected non-empty footer"
        # The exact target depends on hash — but SOME ai_creators
        # handle must appear.
        handles = list(_HANDLES["ai_creators"].values())
        assert any(h in footer for h in handles), (
            f"footer missing any known handle: {footer!r} vs {handles}"
        )

    def test_footer_starts_with_double_newline(self, monkeypatch):
        """Prevents the footer smashing into the CTA line."""
        self._on(monkeypatch)
        footer = build_footer("ai_creators", "facebook", "hash_seed_2")
        if footer:
            assert footer.startswith("\n\n")

    def test_idempotent_same_hash_same_target(self, monkeypatch):
        self._on(monkeypatch)
        a = build_footer("movies", "facebook", "consistent_seed")
        b = build_footer("movies", "facebook", "consistent_seed")
        assert a == b, "same hash must yield same footer"

    def test_rotation_covers_multiple_targets(self, monkeypatch):
        """Over 100 distinct hashes, all 3 target platforms should
        appear at least once. (Small chance of statistical miss but
        with 100 draws and 3 buckets, P(any bucket empty) ≈ 0)."""
        self._on(monkeypatch)
        footers = [
            build_footer("gaming", "facebook", f"seed_{i}")
            for i in range(100)
        ]
        # Detect target from marker string
        markers = Counter()
        for f in footers:
            if "Instagram" in f: markers["instagram"] += 1
            elif "YouTube" in f: markers["youtube"] += 1
            elif "Threads" in f: markers["threads"] += 1
        # All 3 buckets non-empty
        assert markers["instagram"] > 0
        assert markers["youtube"] > 0
        assert markers["threads"] > 0
        # YouTube emphasized 2× per _TARGETS_FROM_FACEBOOK design
        assert markers["youtube"] >= markers["instagram"]


class TestAppendFooterIfEnabled:
    def test_disabled_returns_caption_unchanged(self, monkeypatch):
        monkeypatch.delenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", raising=False)
        cap = "Original caption body"
        out = append_footer_if_enabled(
            cap, niche_id="ai_creators", source_platform="facebook",
            blueprint_hash="abc",
        )
        assert out == cap

    def test_empty_caption_stays_empty(self, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", "all")
        assert append_footer_if_enabled(
            "", niche_id="ai_creators", source_platform="facebook",
            blueprint_hash="abc",
        ) == ""

    def test_appends_when_enabled(self, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", "all")
        cap = "Original caption body"
        out = append_footer_if_enabled(
            cap, niche_id="ai_creators", source_platform="facebook",
            blueprint_hash="seed_x",
        )
        assert out.startswith(cap)
        assert len(out) > len(cap)

    def test_append_logs_info_on_success(self, monkeypatch, caplog):
        """2026-08-18 (task #212 audit): silent-success made DB
        archaeology necessary to verify the footer was firing. Now
        emits INFO log so `journalctl -u genlab-pipeline-* | grep
        cross_channel_footer` proves the code path fires per blueprint.
        Rule #19 pattern — never silent-DEBUG a canary-observability
        surface."""
        import logging as _logging
        monkeypatch.setenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", "all")
        with caplog.at_level(_logging.INFO):
            append_footer_if_enabled(
                "Original caption body",
                niche_id="ai_creators",
                source_platform="facebook",
                blueprint_hash="seed_x",
            )
        assert any(
            "cross_channel_footer" in r.message and "appended" in r.message
            for r in caplog.records
        ), (
            "expected INFO log line on successful footer append; "
            f"got: {[r.message for r in caplog.records]}"
        )

    def test_no_double_stack_when_marker_present(self, monkeypatch):
        """Idempotency: re-running append doesn't add a second footer."""
        monkeypatch.setenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", "all")
        cap = "Original caption body"
        once = append_footer_if_enabled(
            cap, niche_id="ai_creators", source_platform="facebook",
            blueprint_hash="seed_x",
        )
        twice = append_footer_if_enabled(
            once, niche_id="ai_creators", source_platform="facebook",
            blueprint_hash="seed_x",
        )
        assert once == twice, (
            "second call must be no-op when footer already present"
        )

    def test_non_facebook_source_no_change(self, monkeypatch):
        """IG/Threads/YT are NOT funnel sources today."""
        monkeypatch.setenv("GENLAB_CROSS_CHANNEL_FOOTER_NICHES", "all")
        cap = "IG caption body"
        out = append_footer_if_enabled(
            cap, niche_id="ai_creators", source_platform="instagram",
            blueprint_hash="seed_x",
        )
        assert out == cap


class TestHandleTable:
    """The handle table is the source of truth for cross-refs. If a
    handle changes (rebrand, account switch), this pin surfaces the
    breakage instead of shipping wrong handles to prod."""

    def test_every_niche_has_all_three_platform_handles(self):
        for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert niche in _HANDLES
            handles = _HANDLES[niche]
            for platform in ("instagram", "youtube", "threads"):
                assert platform in handles
                assert handles[platform].startswith("@"), (
                    f"{niche}/{platform} handle must start with @: "
                    f"{handles[platform]!r}"
                )

    def test_rotation_only_targets_platforms_with_handles(self):
        for target in _TARGETS_FROM_FACEBOOK:
            assert target in ("instagram", "youtube", "threads"), (
                f"rotation lists {target!r} but no handles exist for it"
            )
