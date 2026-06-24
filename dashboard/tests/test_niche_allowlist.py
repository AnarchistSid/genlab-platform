"""PR #539 (2026-06-24): SR-F foundation — per-user niche allowlist
resolver pins.

The resolver maps a logged-in username to the set of niche IDs that
user is authorized for. PR #540 (follow-up) wires endpoints to
enforce; this PR is the foundation only.

If these pins regress, the SR-F sprint loses its mechanism: every
endpoint that wires it in PR #540 would see None ("unrestricted")
for every user and the per-tenant scoping silently disappears.
"""

from __future__ import annotations

import importlib
import logging

import pytest


# Module-import shim — the auth subpackage was created in this PR
@pytest.fixture
def module():
    import server.auth.niche_allowlist as mod

    importlib.reload(mod)
    return mod


# ── default / no-allowlist mode ───────────────────────────────────


def test_returns_none_when_no_auth_user_env(monkeypatch, module):
    """REVIEW_AUTH_USER unset → no logged-in user concept → returns
    None (unrestricted). Preserves the localhost-only mode behavior."""
    monkeypatch.delenv("REVIEW_AUTH_USER", raising=False)
    assert module.get_allowed_niches() is None


def test_returns_none_when_user_has_no_env_var(monkeypatch, module):
    """User exists but no REVIEW_NICHE_ALLOWLIST_<user> env var
    configured → returns None. This is the pre-PR-539 default and
    must be preserved for every existing operator."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)
    assert module.get_allowed_niches() is None


def test_returns_none_when_allowlist_is_empty(monkeypatch, module):
    """REVIEW_NICHE_ALLOWLIST_<user>= (empty value) → None. Operators
    setting the env to "" should explicitly disable scoping for that
    user, not block them from everything."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_ADMIN", "")
    assert module.get_allowed_niches() is None


def test_returns_none_when_allowlist_is_asterisk(monkeypatch, module):
    """The literal ``*`` is an explicit "unrestricted" marker so
    operators can document admin status without listing every niche.
    More readable than an empty string for the same intent."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_ADMIN", "*")
    assert module.get_allowed_niches() is None


# ── valid allowlist parsing ────────────────────────────────────────


def test_returns_set_for_single_niche(monkeypatch, module):
    """The simplest case — operator scoped to one niche."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    assert module.get_allowed_niches() == {"gaming"}


def test_returns_set_for_multiple_niches(monkeypatch, module):
    """Comma-separated list parses into a set, whitespace stripped."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "multi")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_MULTI", "gaming, sports , movies")
    assert module.get_allowed_niches() == {"gaming", "sports", "movies"}


def test_returns_set_case_insensitive_input(monkeypatch, module):
    """Niche IDs are lowercase in canon — uppercase input gets
    normalized before validation so operators don't trip on case."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_ADMIN", "GAMING,Sports")
    assert module.get_allowed_niches() == {"gaming", "sports"}


# ── typo defense ───────────────────────────────────────────────────


def test_unknown_niche_ids_are_dropped(monkeypatch, module, caplog):
    """A typo (``gamming``) shouldn't silently grant access to
    nothing — it logs a WARNING and the VALID names from the list
    still grant their normal access. Defensive parsing.
    """
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_ADMIN", "gaming,gamming,sports")

    with caplog.at_level(logging.WARNING):
        result = module.get_allowed_niches()

    # Valid IDs preserved
    assert result == {"gaming", "sports"}
    # Warning fires for the typo
    assert any("unknown niche IDs" in r.message for r in caplog.records)
    assert any("gamming" in r.message for r in caplog.records)


def test_all_invalid_collapses_to_none(monkeypatch, module):
    """If EVERY entry is invalid, the resolver returns None
    (unrestricted) rather than an empty set — empty allowlist would
    block the user from everything which is almost always wrong.
    Better to fail-open with a WARNING."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_ADMIN", "typo1,typo2,not_real")
    assert module.get_allowed_niches() is None


# ── username sanitization ─────────────────────────────────────────


def test_username_with_special_chars_maps_to_underscore(monkeypatch, module):
    """``foo-bar`` username → ``REVIEW_NICHE_ALLOWLIST_FOO_BAR``."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "foo-bar")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_FOO_BAR", "anime")
    assert module.get_allowed_niches() == {"anime"}


def test_username_uppercase_normalization(monkeypatch, module):
    """``MixedCase`` username → ``REVIEW_NICHE_ALLOWLIST_MIXEDCASE``."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "MixedCase")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_MIXEDCASE", "movies")
    assert module.get_allowed_niches() == {"movies"}


# ── is_niche_allowed convenience wrapper ──────────────────────────


def test_is_niche_allowed_returns_true_when_unrestricted(monkeypatch, module):
    """No allowlist → every niche is allowed. Single-resource
    endpoints use this when they only need a yes/no answer."""
    monkeypatch.delenv("REVIEW_AUTH_USER", raising=False)
    assert module.is_niche_allowed("gaming") is True
    assert module.is_niche_allowed("anime") is True


def test_is_niche_allowed_filters_by_user_scope(monkeypatch, module):
    """When the user has an allowlist, only listed niches return True."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "limited")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_LIMITED", "gaming,sports")
    assert module.is_niche_allowed("gaming") is True
    assert module.is_niche_allowed("sports") is True
    assert module.is_niche_allowed("anime") is False
    assert module.is_niche_allowed("movies") is False


# ── source pins ────────────────────────────────────────────────────


def test_source_pins_pr_539_anchor(module):
    """Future archaeologist anchor."""
    from pathlib import Path

    src = Path(module.__file__).read_text()
    assert "PR #539" in src
    assert "SR-F foundation" in src


def test_source_pins_env_protocol_documented(module):
    """The env-var protocol (REVIEW_NICHE_ALLOWLIST_<USER>) MUST be
    documented in the module docstring so operators configuring it
    via grep can find the contract."""
    from pathlib import Path

    src = Path(module.__file__).read_text()
    assert "REVIEW_NICHE_ALLOWLIST_<UPPERCASE_USERNAME>" in src
    # Example shapes shown
    assert "REVIEW_NICHE_ALLOWLIST_GAMING_OPERATOR=gaming" in src


def test_valid_niche_ids_match_canonical_5(module):
    """The validation set must match configs/niches_registry.yaml.
    A drift would silently reject valid niches or accept invalid
    ones."""
    # The 5 production niches as of 2026-06-24
    assert module._VALID_NICHE_IDS == frozenset(
        {"gaming", "sports", "movies", "anime", "ai_creators"}
    )
