"""Pin tests for `genlab_core.settings.env_true` (tasks #587, #588).

Two round-3 flag-audit findings converged on this helper:

* **#587 silent no-op**: ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED`` was read
  at 3 sites with INCOMPATIBLE truthiness checks — one strict (``!= "1"``),
  two permissive (``.lower() in ("1", "true", ...)``). Setting the flag
  to ``"true"`` produced a partial fire.

* **#588 naming drift**: ``GENLAB_OPTIMAL_TIME_BANDIT_ENABLED`` (as
  documented in ``.env.example``) never matched the code's actual
  read of ``GENLAB_OPTIMAL_TIME_BANDIT`` (no ``_ENABLED`` suffix).
  Operators setting the documented name got silent OFF behavior.

`env_true` is the single source of truth for these checks. The tests
below LOCK IN its contract so future contributors can't drift.
"""

from __future__ import annotations

import pytest
from genlab_core.settings import env_true


class TestBasicTruthiness:
    """The accepted truthy values are frozen. Adding or removing any is a
    behavior change that should surface in code review — hence this pin."""

    @pytest.mark.parametrize(
        "raw",
        ["1", "true", "TRUE", "True", "yes", "YES", "on", "ON", "y", "Y", "t", "T"],
    )
    def test_truthy_values(self, monkeypatch, raw):
        monkeypatch.setenv("PIN_TEST_FLAG", raw)
        assert env_true("PIN_TEST_FLAG") is True, f"{raw!r} must be truthy"

    @pytest.mark.parametrize(
        "raw", ["0", "false", "FALSE", "no", "NO", "off", "OFF", "", "  ", "banana", "2"]
    )
    def test_falsy_values(self, monkeypatch, raw):
        monkeypatch.setenv("PIN_TEST_FLAG", raw)
        assert env_true("PIN_TEST_FLAG") is False, f"{raw!r} must be falsy"

    def test_whitespace_stripped(self, monkeypatch):
        """Operators occasionally hit trailing whitespace in .env; strip it."""
        monkeypatch.setenv("PIN_TEST_FLAG", " true \n")
        assert env_true("PIN_TEST_FLAG") is True


class TestUnsetVsDefault:
    def test_unset_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("PIN_TEST_FLAG", raising=False)
        assert env_true("PIN_TEST_FLAG") is False

    def test_unset_with_default_true(self, monkeypatch):
        """Kill-switch flags (ON unless explicitly disabled) use
        ``default=True``. This test locks in the semantic."""
        monkeypatch.delenv("PIN_TEST_FLAG", raising=False)
        assert env_true("PIN_TEST_FLAG", default=True) is True

    def test_explicit_false_overrides_default_true(self, monkeypatch):
        """A kill-switch flag SET to "false" must actually disable —
        default only applies when the var is UNSET."""
        monkeypatch.setenv("PIN_TEST_FLAG", "false")
        assert env_true("PIN_TEST_FLAG", default=True) is False


class TestLegacyNameFallback:
    """Task #588 — the ``GENLAB_OPTIMAL_TIME_BANDIT`` → ``..._ENABLED``
    rename cannot break prod. ``legacy_name=`` is how we thread the
    backward compat through cleanly. These tests lock in the semantics."""

    def test_new_name_takes_precedence(self, monkeypatch):
        """When both names are set, the canonical (new) name wins."""
        monkeypatch.setenv("PIN_TEST_NEW", "true")
        monkeypatch.setenv("PIN_TEST_LEGACY", "false")
        assert env_true("PIN_TEST_NEW", legacy_name="PIN_TEST_LEGACY") is True

    def test_legacy_name_used_when_new_unset(self, monkeypatch):
        """Prod's existing setting must survive the rename."""
        monkeypatch.delenv("PIN_TEST_NEW", raising=False)
        monkeypatch.setenv("PIN_TEST_LEGACY", "1")
        assert env_true("PIN_TEST_NEW", legacy_name="PIN_TEST_LEGACY") is True

    def test_legacy_name_falsy_still_falsy(self, monkeypatch):
        """A LEGACY name set to a falsy value must NOT be treated as unset —
        that would let a new-name default sneak in when operator meant
        explicit OFF."""
        monkeypatch.delenv("PIN_TEST_NEW", raising=False)
        monkeypatch.setenv("PIN_TEST_LEGACY", "false")
        # default=True, but legacy explicitly says false → False
        assert env_true("PIN_TEST_NEW", default=True, legacy_name="PIN_TEST_LEGACY") is False

    def test_neither_set_returns_default(self, monkeypatch):
        monkeypatch.delenv("PIN_TEST_NEW", raising=False)
        monkeypatch.delenv("PIN_TEST_LEGACY", raising=False)
        assert env_true("PIN_TEST_NEW", legacy_name="PIN_TEST_LEGACY") is False
        assert env_true("PIN_TEST_NEW", default=True, legacy_name="PIN_TEST_LEGACY") is True


class TestConsistentTruthinessAcrossSites:
    """Task #587 — the 3 read sites for ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED``
    (post_render_transform, transformation_orchestrator, transformation_selector)
    now ALL go through ``env_true``. Any future site added must too, or
    the silent no-op class of bug returns.

    This test doesn't lock the specific call sites in — file-line lock-ins
    are brittle. Instead it locks the CONTRACT: setting the flag to
    ``"true"`` must produce identical truthiness across all values that
    should be equivalent.
    """

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_all_documented_truthy_forms_agree(self, monkeypatch, raw):
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raw)
        # Verify a fresh env_true call agrees — the actual read sites
        # (post_render_transform, transformation_orchestrator,
        # transformation_selector) all import env_true, so they inherit
        # this behavior. If someone reintroduces a bespoke check at a
        # 4th site, that site would need its own pin test.
        assert env_true("GENLAB_INTELLIGENT_TRANSFORM_ENABLED") is True
