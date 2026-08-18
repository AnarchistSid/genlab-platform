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


class TestCanaryFlagNamesMatchReaders:
    """2026-08-18 (task #217 audit): flag_audit's canary allowlist
    silently drifted from the actual code-side flag readers for 2
    entries. The audit line reported the wrong flag name as "off" even
    when the real flag was actively firing in prod.

    Detection: the reader-side file's ``_ROLLOUT_ENV`` constant is the
    canonical name. If flag_audit's ``_CANARY_FLAGS`` disagrees, the
    audit line is dead-lettered. Pin here — matches the class-of-bug
    for shared contracts with N implementers (rule for observability
    layer).
    """

    def test_ig_discovery_hashtags_flag_name_matches_reader(self):
        from genlab_core.observability import flag_audit as fa
        from genlab_core.publishing import ig_discovery_hashtags as reader
        assert reader._ROLLOUT_ENV in fa._CANARY_FLAGS, (
            f"flag_audit._CANARY_FLAGS must contain {reader._ROLLOUT_ENV} "
            f"— reader is source of truth. Got: {fa._CANARY_FLAGS}"
        )

    def test_threads_hashtags_flag_name_matches_reader(self):
        from genlab_core.observability import flag_audit as fa
        from genlab_core.publishing import threads_hashtags as reader
        assert reader._ROLLOUT_ENV in fa._CANARY_FLAGS, (
            f"flag_audit._CANARY_FLAGS must contain {reader._ROLLOUT_ENV} "
            f"— reader is source of truth. Got: {fa._CANARY_FLAGS}"
        )

    def test_cross_channel_footer_flag_name_matches_reader(self):
        from genlab_core.observability import flag_audit as fa
        from genlab_core.publishing import cross_channel_footer as reader
        assert reader._ROLLOUT_ENV in fa._CANARY_FLAGS


class TestNarrationFlagName:
    """NARR-01 (2026-08-18): the narration gate reader is source of
    truth for the flag name. Pin here to catch the same class of drift
    as ig_discovery_hashtags / threads_hashtags (fixed d6d136e3)."""

    def test_narration_flag_name_matches_reader(self):
        """publishing/narration_gate.py:_ROLLOUT_ENV must equal
        GENLAB_NARRATION_ENABLED and be present in flag_audit._KNOWN_FLAGS."""
        from genlab_core.observability import flag_audit as fa
        from genlab_core.publishing import narration_gate as reader
        assert reader._ROLLOUT_ENV == "GENLAB_NARRATION_ENABLED"
        assert reader._ROLLOUT_ENV in fa._KNOWN_FLAGS, (
            f"flag_audit._KNOWN_FLAGS must contain {reader._ROLLOUT_ENV} "
            f"— reader is source of truth. Missing this = the audit "
            f"line silently reports narration off even when the real "
            f"flag is on. Same class as d6d136e3 IG/Threads drift."
        )


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


class TestCanaryFlags:
    """2026-08-18 (task #200 hygiene): canary-list flags carry a value
    (e.g. 'ai_creators' or 'all') rather than a boolean. flag_audit
    reports them separately so operators can see per-canary state
    alongside the boolean allowlist."""

    def test_canary_flags_registered(self):
        from genlab_core.observability.flag_audit import _CANARY_FLAGS
        # Tonight's 3 canary flips
        assert "GENLAB_HOOK_THUMBNAIL_NICHES" in _CANARY_FLAGS
        assert "GENLAB_CHART_BROLL_NICHES" in _CANARY_FLAGS
        assert "GENLAB_ANIME_BACKFILL_NICHES" in _CANARY_FLAGS

    def test_canary_flag_value_reports_niche_list(self, monkeypatch):
        from genlab_core.observability.flag_audit import _canary_flag_value
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "ai_creators")
        assert _canary_flag_value("GENLAB_HOOK_THUMBNAIL_NICHES") == "ai_creators"

    def test_canary_flag_value_off_tokens_return_none(self, monkeypatch):
        from genlab_core.observability.flag_audit import _canary_flag_value
        for val in ("", "0", "false", "no", "off"):
            monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", val)
            assert _canary_flag_value("GENLAB_HOOK_THUMBNAIL_NICHES") is None

    def test_canary_flag_value_unset_returns_none(self, monkeypatch):
        from genlab_core.observability.flag_audit import _canary_flag_value
        monkeypatch.delenv("GENLAB_HOOK_THUMBNAIL_NICHES", raising=False)
        assert _canary_flag_value("GENLAB_HOOK_THUMBNAIL_NICHES") is None

    def test_canary_flag_value_wildcard(self, monkeypatch):
        from genlab_core.observability.flag_audit import _canary_flag_value
        monkeypatch.setenv("GENLAB_CHART_BROLL_NICHES", "all")
        assert _canary_flag_value("GENLAB_CHART_BROLL_NICHES") == "all"

    def test_log_emits_canary_line_when_any_active(self, monkeypatch, caplog):
        """When at least one canary flag has a value, a second log
        line reports the {name: value} map. Silent when all off."""
        import logging
        from genlab_core.observability.flag_audit import log_active_flags

        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "ai_creators")
        monkeypatch.setenv("GENLAB_CHART_BROLL_NICHES", "ai_creators")
        with caplog.at_level(logging.INFO, logger="genlab_core.observability.flag_audit"):
            log_active_flags(context="test")
        canary_lines = [r for r in caplog.records if "canaries=" in r.getMessage()]
        assert len(canary_lines) == 1
        msg = canary_lines[0].getMessage()
        assert "GENLAB_HOOK_THUMBNAIL_NICHES" in msg
        assert "ai_creators" in msg

    def test_log_no_canary_line_when_all_off(self, monkeypatch, caplog):
        """When all canary flags are off, no second log line — avoids
        spamming an empty line every pipeline fire."""
        import logging
        from genlab_core.observability.flag_audit import (
            _CANARY_FLAGS, log_active_flags,
        )
        for name in _CANARY_FLAGS:
            monkeypatch.delenv(name, raising=False)
        with caplog.at_level(logging.INFO, logger="genlab_core.observability.flag_audit"):
            log_active_flags(context="test")
        canary_lines = [r for r in caplog.records if "canaries=" in r.getMessage()]
        assert len(canary_lines) == 0


class TestInfshTTSFlagListed:
    """2026-08-18 (task #200): boolean flag registered."""

    def test_infsh_tts_in_known_flags(self):
        from genlab_core.observability.flag_audit import _KNOWN_FLAGS
        assert "GENLAB_INFSH_TTS_ENABLED" in _KNOWN_FLAGS
