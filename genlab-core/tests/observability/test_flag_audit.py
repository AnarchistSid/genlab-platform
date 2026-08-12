"""Pin the flag-audit primitive.

Contract:

  * `log_active_flags(context=X)` emits ONE INFO log line summarising
    which GENLAB_*_ENABLED flags are currently on (per env)
  * Never raises. Never side-effects beyond the log line.
  * Truthy values follow env_true semantics: "1"/"true"/"yes"/"on"
    (case-insensitive) are ON; anything else is OFF.
  * The _KNOWN_FLAGS allowlist is curated (not auto-discovered) so
    adding a new flag requires an explicit PR — prevents accidental
    surface expansion.

Structural pin:

  * pipeline_runner.run() invokes log_active_flags at niche start
  * publish_all_platforms.main() invokes at publisher start
"""

from __future__ import annotations

import logging

from genlab_core.observability.flag_audit import (
    _KNOWN_FLAGS,
    _is_flag_on,
    log_active_flags,
)


class TestFlagOnDetection:
    def test_one_true_treated_as_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "1")
        assert _is_flag_on("GENLAB_YT_SHORTS_SEO_ENABLED") is True

    def test_true_string_treated_as_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "true")
        assert _is_flag_on("GENLAB_YT_SHORTS_SEO_ENABLED") is True

    def test_yes_string_treated_as_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "YES")
        assert _is_flag_on("GENLAB_YT_SHORTS_SEO_ENABLED") is True

    def test_zero_treated_as_off(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "0")
        assert _is_flag_on("GENLAB_YT_SHORTS_SEO_ENABLED") is False

    def test_false_string_treated_as_off(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "false")
        assert _is_flag_on("GENLAB_YT_SHORTS_SEO_ENABLED") is False

    def test_unset_treated_as_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_YT_SHORTS_SEO_ENABLED", raising=False)
        assert _is_flag_on("GENLAB_YT_SHORTS_SEO_ENABLED") is False


class TestLogEmission:
    def test_all_flags_off_emits_active_zero(self, monkeypatch, caplog):
        for flag in _KNOWN_FLAGS:
            monkeypatch.delenv(flag, raising=False)
        with caplog.at_level(logging.INFO):
            log_active_flags(context="test_context")
        msg = next(r.message for r in caplog.records if "flag_audit" in r.message)
        assert "context=test_context" in msg
        assert f"active=0/{len(_KNOWN_FLAGS)}" in msg
        assert "flags=[]" in msg

    def test_some_flags_on_appears_in_output(self, monkeypatch, caplog):
        for flag in _KNOWN_FLAGS:
            monkeypatch.delenv(flag, raising=False)
        monkeypatch.setenv("GENLAB_YT_SHORTS_SEO_ENABLED", "1")
        monkeypatch.setenv("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED", "1")
        with caplog.at_level(logging.INFO):
            log_active_flags(context="pipeline_ai")
        msg = next(r.message for r in caplog.records if "flag_audit" in r.message)
        assert "active=2/" in msg
        assert "GENLAB_YT_SHORTS_SEO_ENABLED" in msg
        assert "GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED" in msg

    def test_context_string_appears_verbatim(self, monkeypatch, caplog):
        with caplog.at_level(logging.INFO):
            log_active_flags(context="publisher")
        assert any("context=publisher" in r.message for r in caplog.records)

    def test_never_raises_on_broken_env(self, caplog):
        """Even if os.environ access failed somehow, we must not
        propagate the exception into the caller."""
        import genlab_core.observability.flag_audit as mod

        # Force _is_flag_on to raise for every call
        def _boom(_name):
            raise RuntimeError("simulated env failure")

        original = mod._is_flag_on
        mod._is_flag_on = _boom
        try:
            # Must not raise
            log_active_flags(context="test")
        finally:
            mod._is_flag_on = original


class TestKnownFlagsAllowlist:
    def test_all_flags_have_genlab_prefix(self):
        for flag in _KNOWN_FLAGS:
            assert flag.startswith("GENLAB_"), (
                f"non-GENLAB flag in allowlist: {flag}"
            )

    def test_all_flags_end_in_enabled(self):
        for flag in _KNOWN_FLAGS:
            assert flag.endswith("_ENABLED"), (
                f"non-standard flag suffix in allowlist: {flag}"
            )

    def test_no_duplicates(self):
        assert len(_KNOWN_FLAGS) == len(set(_KNOWN_FLAGS))

    def test_tonight_new_flags_present(self):
        """Tonight's arc added 8 new flags. All must be in allowlist
        or operator loses observability on the just-flipped features."""
        _NEW_TONIGHT = {
            "GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED",
            "GENLAB_YT_SHORTS_SEO_ENABLED",
            "GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED",
            "GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED",
            "GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED",
            "GENLAB_HOOK_NEAR_DUPE_RETRY_ENABLED",
            "GENLAB_TRENDING_AUDIO_META_ENABLED",
            "GENLAB_FIRST_FRAME_VALIDATOR_ENABLED",
        }
        assert _NEW_TONIGHT.issubset(set(_KNOWN_FLAGS))


class TestStructuralWires:
    """Guards against the wire being deleted from the call sites."""

    def test_pipeline_runner_calls_flag_audit(self):
        import pathlib

        path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "pipeline"
            / "pipeline_runner.py"
        )
        src = path.read_text()
        assert "from genlab_core.observability.flag_audit import log_active_flags" in src
        assert 'log_active_flags(context=f"pipeline_' in src

    def test_publisher_calls_flag_audit(self):
        import pathlib

        path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "publishing"
            / "publish_all_platforms.py"
        )
        src = path.read_text()
        assert "from genlab_core.observability.flag_audit import log_active_flags" in src
        assert 'log_active_flags(context="publisher")' in src

    def test_engagement_poller_calls_flag_audit(self):
        """The engagement_poller is a long-running daemon; its
        startup should emit flag_audit so operator can verify env
        state independent of pipeline / publisher fires."""
        import pathlib

        path = (
            pathlib.Path(__file__).parents[3]
            / "genlab-core"
            / "scripts"
            / "run_engagement_poller.py"
        )
        src = path.read_text()
        assert "from genlab_core.observability.flag_audit import log_active_flags" in src
        assert "log_active_flags(context=f" in src
        assert "engagement_poller_" in src
