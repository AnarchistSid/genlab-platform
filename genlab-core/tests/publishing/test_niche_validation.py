"""Tests for :mod:`genlab_core.publishing.niche_validation`.

Lives next to its module home so the regression surface is right next to
the code under test, not embedded in the omnibus
``test_publish_all_platforms``. Covers the validate/normalise pair
extracted in PR 1/N of the publish_all_platforms decomposition.
"""

from __future__ import annotations

import pytest
from genlab_core.publishing.niche_validation import (
    VALID_NICHE_IDS,
    normalize_niche,
    validate_niche,
)


class TestNormalizeNiche:
    @pytest.mark.parametrize("alias", ["ai_tech", "ai_news"])
    def test_ai_aliases_collapse_to_ai_creators(self, alias: str) -> None:
        assert normalize_niche(alias) == "ai_creators"

    @pytest.mark.parametrize("niche", sorted(VALID_NICHE_IDS))
    def test_canonical_ids_pass_through(self, niche: str) -> None:
        assert normalize_niche(niche) == niche

    def test_whitespace_stripped(self) -> None:
        assert normalize_niche("  gaming  ") == "gaming"

    def test_unknown_id_passes_through_untouched(self) -> None:
        """Normalisation is alias-mapping only — unknown IDs are returned
        unchanged so :func:`validate_niche` can reject them with the right
        diagnostic message."""
        assert normalize_niche("typo") == "typo"


class TestValidateNiche:
    @pytest.mark.parametrize("niche", sorted(VALID_NICHE_IDS))
    def test_canonical_ids_return_canonical(self, niche: str) -> None:
        assert validate_niche(niche) == niche

    @pytest.mark.parametrize(
        "alias,canonical", [("ai_tech", "ai_creators"), ("ai_news", "ai_creators")]
    )
    def test_alias_returns_canonical(self, alias: str, canonical: str) -> None:
        """validate_niche must always RETURN the canonical form, never the
        raw alias — downstream code branches on the canonical string."""
        assert validate_niche(alias) == canonical

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty niche_id"):
            validate_niche("")

    def test_whitespace_only_raises(self) -> None:
        """Whitespace-only strings normalise to empty and must still raise —
        otherwise a typo'd `niche="   "` would silently publish nowhere."""
        with pytest.raises(ValueError, match="Empty niche_id"):
            validate_niche("   ")

    def test_unknown_niche_raises_with_diagnostic(self) -> None:
        """Unknown niche IDs must produce a hard ValueError, NEVER a silent
        default to ai_creators (which would cross-channel-publish — the
        thing Sprint 62's cross-channel guard was built to prevent)."""
        with pytest.raises(ValueError, match="unknown niche_id 'fashion'"):
            validate_niche("fashion")

    def test_diagnostic_lists_valid_options(self) -> None:
        """When rejecting an unknown niche, the error message lists the
        valid set so an operator can self-diagnose without grep'ing source."""
        try:
            validate_niche("fashion")
        except ValueError as e:
            msg = str(e)
            # Every canonical niche id should appear in the diagnostic.
            for niche in VALID_NICHE_IDS:
                assert niche in msg, f"valid niche {niche!r} missing from diagnostic"


class TestValidNicheIds:
    def test_size_is_5(self) -> None:
        """Catches an accidental drop or add — every change to the canonical
        set should be intentional and accompanied by a test bump."""
        assert len(VALID_NICHE_IDS) == 5

    def test_no_legacy_aliases_in_canonical_set(self) -> None:
        """``ai_tech`` and ``ai_news`` are aliases, NOT canonical — they
        must not leak into the canonical frozenset."""
        assert "ai_tech" not in VALID_NICHE_IDS
        assert "ai_news" not in VALID_NICHE_IDS

    def test_is_frozenset(self) -> None:
        """Immutable so a mistaken downstream ``.add()`` is rejected at
        write time — VALID_NICHE_IDS is the source of truth and must not
        be mutable from any caller."""
        assert isinstance(VALID_NICHE_IDS, frozenset)
