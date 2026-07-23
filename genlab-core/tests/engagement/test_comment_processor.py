"""Tests for comment_processor — idempotency, spam, routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_persona_engine_cache():
    """2026-06-21: ``_get_persona_engine`` caches PersonaEngine per niche
    at module level (perf fix). Tests that ``@patch`` PersonaEngine and
    expect the patch to take effect must start with an empty cache —
    otherwise a stale entry from a prior test (or earlier import) wins.
    Mirrors the autouse fixture in ``test_comment_processor_persona_engine_cache.py``."""
    from genlab_core.engagement import comment_processor as cp

    cp._persona_engine_cache.clear()
    yield
    cp._persona_engine_cache.clear()


@pytest.fixture
def agent_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ROOT", str(tmp_path))
    # Create a minimal persona.yaml
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "persona.yaml").write_text(
        'name: "TestBrand"\n'
        "voice:\n"
        "  formality: 0.5\n"
        "  enthusiasm: 0.5\n"
        "  emoji_density: low\n"
        "  vocabulary: casual\n"
        "style_examples: []\n"
        "topics_to_avoid: []\n"
        "reply_constraints:\n"
        "  max_length_chars: 280\n"
        "  language: en\n"
    )
    return tmp_path


def _make_event(**overrides):
    defaults = {
        "comment_id": "c123",
        "comment_text": "This is a great clip!",
        "platform": "youtube",
        "niche_id": "gaming",
        "post_id": "p456",
        "post_context": "",
    }
    defaults.update(overrides)
    return defaults


class TestIdempotency:
    def test_has_replied_returns_false_when_no_file(self, agent_root):
        from genlab_core.engagement.comment_processor import _has_replied

        assert _has_replied("c123", "youtube") is False

    def test_mark_and_check_replied(self, agent_root):
        from genlab_core.engagement.comment_processor import (
            _has_replied,
            _mark_replied,
        )

        _mark_replied("c123", "youtube")
        assert _has_replied("c123", "youtube") is True
        assert _has_replied("c123", "instagram") is False
        assert _has_replied("c999", "youtube") is False

    def test_mark_replied_uses_file_locking(self, agent_root):
        from genlab_core.engagement.comment_processor import _mark_replied, _replied_set_path

        _mark_replied("c1", "yt")
        _mark_replied("c2", "yt")
        path = _replied_set_path()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2


class TestSpamSkipping:
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    def test_spam_comments_are_skipped(self, mock_gate_cls, agent_root):
        from genlab_core.engagement.comment_processor import process_reply_event

        event = _make_event(comment_text="Check my bio for free money! http://scam.com")
        process_reply_event(event)
        # Should have been filtered by spam — ToxicityGate never instantiated
        mock_gate_cls.assert_not_called()


class TestAgentRootValidation:
    def test_raises_when_agent_root_not_set(self, monkeypatch):
        monkeypatch.delenv("AGENT_ROOT", raising=False)
        from genlab_core.engagement.comment_processor import _get_agent_root

        with pytest.raises(RuntimeError, match="AGENT_ROOT"):
            _get_agent_root()


class TestLikeIdempotency:
    def test_like_key_distinct_from_reply_key(self, agent_root):
        from genlab_core.engagement.comment_processor import _has_replied, _mark_replied

        _mark_replied("c456", "instagram")
        assert not _has_replied("like:c456", "instagram")

    def test_mark_like_and_check(self, agent_root):
        from genlab_core.engagement.comment_processor import _has_replied, _mark_replied

        assert not _has_replied("like:c123", "youtube")
        _mark_replied("like:c123", "youtube")
        assert _has_replied("like:c123", "youtube")


class TestRateLimitRetry:
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    def test_rate_limit_raises_for_dramatiq_retry(self, mock_replied, mock_spam, agent_root):
        import dramatiq
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_gate = MagicMock()
        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_gate.return_value.check_inbound.return_value = mock_result

        with (
            patch("genlab_core.engagement.comment_processor.ToxicityGate", mock_gate),
            patch("genlab_core.engagement.comment_processor._rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = False
            # The processor raises dramatiq.Retry (not RuntimeError) so the
            # broker re-queues with a calibrated delay instead of letting
            # Dramatiq's default exponential backoff chase a token bucket
            # that refills on a slower cadence. See wave 2 fix in
            # comment_processor.py line ~466.
            with pytest.raises(dramatiq.Retry, match="rate_limited:youtube"):
                process_reply_event(_make_event())


class TestIdempotencyOnFailure:
    """Verify that failed API calls do NOT create false-positive idempotency records."""

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    @patch("genlab_core.engagement.comment_processor._rate_limiter")
    def test_failed_reply_does_not_mark_replied(
        self,
        mock_rl,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_delay,
        agent_root,
    ):
        """When platform client returns False (failure), _mark_replied must NOT run."""
        from genlab_core.engagement.comment_processor import _has_replied, process_reply_event

        mock_rl.acquire.return_value = True

        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_gate_cls.return_value.check_inbound.return_value = mock_result

        # 2026-06-21 (PR #4xx): comment_processor now reads
        # ``engine.persona.reply_constraints.max_length_chars`` to trim the
        # bot-disclosure suffix (lines 652, 672). Without configuring it
        # the chained MagicMock returns a non-int and ``_append_bot_disclosure``
        # raises TypeError on the ``len(text) + ... > max_len`` comparison.
        mock_engine_cls.return_value.persona.reply_constraints.max_length_chars = 280
        mock_engine_cls.return_value.generate_reply.return_value = "Test reply"

        with patch("genlab_core.engagement.comment_processor._post_reply", return_value=False):
            process_reply_event(_make_event(comment_id="fail_c1"))

        assert _has_replied("fail_c1", "youtube") is False

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    @patch("genlab_core.engagement.comment_processor._rate_limiter")
    def test_successful_reply_marks_replied(
        self,
        mock_rl,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_delay,
        agent_root,
    ):
        """When platform client returns True (success), _mark_replied MUST run."""
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_rl.acquire.return_value = True

        # 2026-06-17: also patch the MODULE-LEVEL ``_toxicity_gate`` singleton
        # (line ~135 in comment_processor.py). ``mock_gate_cls`` patches the
        # ``ToxicityGate`` *class*, but the module-level instance was already
        # built at import time using the real class, so the class patch
        # doesn't replace it. Previously this test happened to pass because
        # of a side-effect of test ordering (other tests not having loaded
        # the module yet); U-04's Detoxify model change shifted the import
        # order enough to expose the latent bug. Patching the GLOBAL is
        # the deterministic fix.
        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_result.max_score = 0.02  # low toxicity score
        mock_gate_cls.return_value.check_inbound.return_value = mock_result

        # Outbound check fires AFTER the LLM generates the reply
        # (comment_processor.py:572: ``check_outbound(reply).max_score``).
        # Without this stub, the default MagicMock returns a non-numeric
        # value that ``classify_reply_action`` interprets as max-toxicity
        # → routes to 'discard' → ``_mark_replied`` never runs.
        mock_outbound = MagicMock()
        mock_outbound.max_score = 0.05  # low outbound toxicity = auto
        mock_outbound.is_toxic = False
        mock_gate_cls.return_value.check_outbound.return_value = mock_outbound

        mock_engine_cls.return_value.persona.reply_constraints.max_length_chars = 280
        mock_engine_cls.return_value.generate_reply.return_value = "Thanks! Glad you liked it"

        # Replace the module-level singleton with the mocked instance, so
        # comment_processor uses the mock instead of the real ToxicityGate
        # that was instantiated at import time.
        from genlab_core.engagement import comment_processor as cp_mod

        with (
            patch.object(cp_mod, "_toxicity_gate", mock_gate_cls.return_value),
            patch("genlab_core.engagement.comment_processor._post_reply", return_value=True),
            patch("genlab_core.engagement.comment_processor._mark_replied") as mock_mark,
        ):
            process_reply_event(_make_event(comment_id="ok_c1"))

        mock_mark.assert_called_once_with("ok_c1", "youtube")


class TestBacklogClientWiring:
    """Verify SharePoint integration in the reply pipeline."""

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    @patch("genlab_core.engagement.comment_processor._rate_limiter")
    def test_successful_reply_updates_sharepoint(
        self,
        mock_rl,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_delay,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_rl.acquire.return_value = True

        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_result.max_score = 0.02
        mock_gate_cls.return_value.check_inbound.return_value = mock_result
        mock_engine_cls.return_value.persona.reply_constraints.max_length_chars = 280
        mock_engine_cls.return_value.generate_reply.return_value = "Thanks! Glad you enjoyed it"

        mock_bl = MagicMock()
        mock_bl.write_pending_engagement.return_value = "sp-42"

        with (
            patch("genlab_core.engagement.comment_processor._post_reply", return_value=True),
            patch("genlab_core.engagement.comment_processor._mark_replied"),
            patch(
                "genlab_core.engagement.comment_processor.classify_reply_action",
                return_value="auto",
            ),
            patch(
                "genlab_core.engagement.comment_processor._get_backlog_client", return_value=mock_bl
            ),
        ):
            process_reply_event(_make_event(comment_id="bl_c1"))

        mock_bl.write_pending_engagement.assert_called_once()
        # 2026-07-14: niche_id kwarg added for RLS-bypass fix (audit F3).
        mock_bl.update_engagement_status.assert_called_once_with(
            "sp-42",
            "replied",
            reply_text="Thanks! Glad you enjoyed it [automated reply]",
            niche_id="gaming",
        )

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    @patch("genlab_core.engagement.comment_processor._rate_limiter")
    def test_failed_reply_updates_sharepoint_as_failed(
        self,
        mock_rl,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_delay,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_rl.acquire.return_value = True

        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_result.max_score = 0.02
        mock_gate_cls.return_value.check_inbound.return_value = mock_result
        mock_engine_cls.return_value.persona.reply_constraints.max_length_chars = 280
        mock_engine_cls.return_value.generate_reply.return_value = "Thanks for the feedback!"

        mock_bl = MagicMock()
        mock_bl.write_pending_engagement.return_value = "sp-99"

        with (
            patch("genlab_core.engagement.comment_processor._post_reply", return_value=False),
            patch(
                "genlab_core.engagement.comment_processor.classify_reply_action",
                return_value="auto",
            ),
            patch(
                "genlab_core.engagement.comment_processor._get_backlog_client", return_value=mock_bl
            ),
        ):
            process_reply_event(_make_event(comment_id="fail_bl"))

        mock_bl.update_engagement_status.assert_called_once_with(
            "sp-99",
            "failed",
            error_msg="Platform API call failed",
            niche_id="gaming",
        )

    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=True)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    def test_spam_updates_sharepoint_as_skipped(
        self,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_bl = MagicMock()
        mock_bl.write_pending_engagement.return_value = "sp-spam"

        with patch(
            "genlab_core.engagement.comment_processor._get_backlog_client", return_value=mock_bl
        ):
            process_reply_event(_make_event(comment_id="spam_c1", comment_text="FREE MONEY"))

        mock_bl.update_engagement_status.assert_called_once_with(
            "sp-spam", "skipped", niche_id="gaming"
        )

    def test_pipeline_works_without_backlog_client(self, agent_root):
        """Pipeline runs normally when BacklogClient is not configured."""
        from genlab_core.engagement.comment_processor import process_reply_event

        with patch(
            "genlab_core.engagement.comment_processor._get_backlog_client", return_value=None
        ):
            # Spam event — should complete without errors even without BacklogClient
            process_reply_event(
                _make_event(comment_id="no_bl", comment_text="FREE MONEY http://scam.com"),
            )


class TestLoadPersonaPackagedFallback:
    """Pin the prod-broken-on-2026-06-20 contract: the packaged personas/
    directory MUST contain ``ai_creators.yaml`` (canonical) so that
    engagement replies don't 100% fail when the gitignored operator
    persona at ``BlackboxBrief/config/persona.yaml`` is missing from
    prod deploy. The legacy ``ai_news`` alias must also resolve.
    """

    def test_packaged_personas_dir_contains_ai_creators_yaml(self):
        from pathlib import Path

        from genlab_core.engagement import comment_processor

        personas_dir = Path(comment_processor.__file__).parent / "personas"
        canonical = personas_dir / "ai_creators.yaml"
        assert canonical.exists(), (
            f"{canonical} MUST exist or every BB engagement reply fails — "
            "the gitignored operator persona is missing from prod deploys."
        )

    def test_load_persona_finds_packaged_ai_creators(self, monkeypatch, tmp_path):
        """When the operator persona is missing, the packaged fallback wins."""
        # Point AGENT_ROOT at an empty dir so the first two candidate paths miss
        monkeypatch.setenv("AGENT_ROOT", str(tmp_path))
        from genlab_core.engagement.comment_processor import _load_persona

        persona = _load_persona("ai_creators")
        assert persona.name == "Blackbox Brief"

    def test_load_persona_resolves_ai_news_alias_to_canonical_file(self, monkeypatch, tmp_path):
        """Legacy ``ai_news`` ID must hit the canonical ai_creators.yaml file
        even when no ai_news.yaml exists — the normalize_niche() call in
        the resolver is what prevents the Sprint-47-style rename from
        silently breaking every reply on the legacy alias path.
        """
        monkeypatch.setenv("AGENT_ROOT", str(tmp_path))
        from genlab_core.engagement.comment_processor import _load_persona

        persona = _load_persona("ai_news")
        assert persona.name == "Blackbox Brief"


class TestBotDisclosure:
    """R-78: the '[automated reply]' suffix must not push a reply over the
    platform char limit — the reply is trimmed, the disclosure preserved."""

    def test_short_reply_unchanged_and_suffixed(self):
        from genlab_core.engagement.comment_processor import (
            _BOT_DISCLOSURE_SUFFIX,
            _append_bot_disclosure,
        )

        out = _append_bot_disclosure("Nice clutch!", max_len=280)
        assert out == "Nice clutch!" + _BOT_DISCLOSURE_SUFFIX
        assert len(out) <= 280

    def test_long_reply_trimmed_to_fit_with_suffix(self):
        from genlab_core.engagement.comment_processor import (
            _BOT_DISCLOSURE_SUFFIX,
            _append_bot_disclosure,
        )

        reply = "x" * 280  # already at the limit; the suffix would overflow it
        out = _append_bot_disclosure(reply, max_len=280)
        assert len(out) <= 280
        assert out.endswith(_BOT_DISCLOSURE_SUFFIX)  # disclosure kept, reply trimmed

    def test_idempotent_when_already_disclosed(self):
        from genlab_core.engagement.comment_processor import _append_bot_disclosure

        already = "Thanks! [automated reply]"
        assert _append_bot_disclosure(already, max_len=280) == already

    def test_no_max_len_appends_without_trim(self):
        from genlab_core.engagement.comment_processor import (
            _BOT_DISCLOSURE_SUFFIX,
            _append_bot_disclosure,
        )

        reply = "y" * 500
        assert _append_bot_disclosure(reply) == reply + _BOT_DISCLOSURE_SUFFIX


@pytest.fixture
def _clear_replied_cache():
    """Isolate the module-level ``_replied_set_cache`` — otherwise
    marks from a prior test leak into subsequent tests via the shared
    set object (agent_root gives a fresh tmp_path, but the cache
    only reloads from disk when explicitly invalidated)."""
    from genlab_core.engagement import comment_processor as cp

    cp._invalidate_replied_cache()
    yield
    cp._invalidate_replied_cache()


class TestQueuedDedup:
    """2026-07-23 fix: prevent pending_engagement DB row duplication.

    Before the ``queued:`` marker, rate-limited comments were re-queued
    every 10-min poller cycle → 11,151 rate_limited rows in 24h for 6
    unique sports Threads comments (~1858 duplicates per comment).

    The ``_mark_replied(f"queued:{comment_id}", platform)`` sentinel is
    set IMMEDIATELY after the DB write succeeds, so subsequent poller
    cycles skip re-writing regardless of whether the downstream reply
    step eventually succeeds, fails, or hits rate-limit.
    """

    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    def test_queued_marker_skips_second_poll_cycle(
        self, mock_spam, agent_root, _clear_replied_cache
    ):
        """After the first poll writes to pending_engagement, a second poll
        for the same (comment_id, platform) must return early without
        writing another row."""
        from genlab_core.engagement.comment_processor import (
            _mark_replied,
            process_reply_event,
        )

        # Simulate: first poll already ran and marked queued.
        _mark_replied("queued:c123", "threads")

        mock_bl = MagicMock()
        event = _make_event(platform="threads")

        with (
            patch(
                "genlab_core.engagement.comment_processor._get_backlog_client",
                return_value=mock_bl,
            ),
            patch("genlab_core.engagement.comment_processor.ToxicityGate"),
        ):
            process_reply_event(event)

        # write_pending_engagement should NOT have been called — the
        # queued: check returned early.
        mock_bl.write_pending_engagement.assert_not_called()

    def test_queued_marker_set_after_first_write(self, agent_root, _clear_replied_cache):
        """First poll cycle: write pending_engagement, then IMMEDIATELY
        mark queued:{comment_id}. The marker firing is decoupled from
        whatever the downstream reply pipeline decides."""
        from genlab_core.engagement import comment_processor as cp

        mock_bl = MagicMock()
        mock_bl.write_pending_engagement.return_value = "sp_row_1"

        called_marks: list[tuple[str, str]] = []

        def _capture_mark(key: str, platform: str) -> None:
            called_marks.append((key, platform))

        # Patch _has_replied to always False so the queued: guard
        # doesn't short-circuit, and _mark_replied to capture calls.
        with (
            patch.object(cp, "_get_backlog_client", return_value=mock_bl),
            patch.object(cp, "_has_replied", return_value=False),
            patch.object(cp, "_mark_replied", side_effect=_capture_mark),
            patch.object(cp, "is_spam", return_value=False),
            # Short-circuit the toxicity gate to True so downstream
            # persona/reply/post logic doesn't run. Any short-circuit
            # after write_pending_engagement works — we only care that
            # queued: fires immediately after DB write.
            patch.object(cp._toxicity_gate, "check_inbound") as mock_tox,
        ):
            mock_tox.return_value = MagicMock(is_toxic=True)
            cp.process_reply_event(_make_event(platform="threads"))

        # DB write happened once.
        mock_bl.write_pending_engagement.assert_called_once()
        # queued: marker fired with the right key + platform.
        assert ("queued:c123", "threads") in called_marks, (
            f"queued: marker missing from {called_marks!r} — poller will re-queue"
        )

    def test_queued_marker_scopes_by_platform(self, agent_root, _clear_replied_cache):
        """Threads comment queued must NOT block the same comment_id on
        Instagram. The (comment_id, platform) pair is the dedup key."""
        from genlab_core.engagement.comment_processor import (
            _has_replied,
            _mark_replied,
        )

        _mark_replied("queued:c123", "threads")

        assert _has_replied("queued:c123", "threads") is True
        assert _has_replied("queued:c123", "instagram") is False


class TestBrokenRateLimitReQueueRegression:
    """Regression: reproduce the exact 2026-07-23 sports Threads pattern
    (5,500 rate_limited rows for 6 unique comments in 24h) and prove the
    ``queued:`` marker breaks the loop."""

    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    def test_rate_limited_comment_not_re_queued_after_first_pass(
        self, mock_spam, agent_root, _clear_replied_cache
    ):
        """Simulate the exact bug shape: comment C hits rate_limit on
        poll cycle 1. Poll cycle 2 (10 min later) sees the same C from
        the Threads API. Fix: queued: marker prevents cycle 2 from
        writing a second row."""
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_bl = MagicMock()
        mock_bl.write_pending_engagement.return_value = "sp_row_1"

        mock_gate = MagicMock()
        mock_gate.return_value.check_inbound.return_value = MagicMock(is_toxic=False)

        event = _make_event(platform="threads", niche_id="sports")

        # Cycle 1: rate_limit will fire → raises dramatiq.Retry
        import dramatiq

        with (
            patch(
                "genlab_core.engagement.comment_processor._get_backlog_client",
                return_value=mock_bl,
            ),
            patch("genlab_core.engagement.comment_processor.ToxicityGate", mock_gate),
            patch("genlab_core.engagement.comment_processor._rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = False
            with pytest.raises(dramatiq.Retry):
                process_reply_event(event)

        # After cycle 1: DB write happened once, queued: marker set.
        assert mock_bl.write_pending_engagement.call_count == 1

        # Cycle 2: same event fires 10 min later from a fresh poll.
        with (
            patch(
                "genlab_core.engagement.comment_processor._get_backlog_client",
                return_value=mock_bl,
            ),
            patch("genlab_core.engagement.comment_processor.ToxicityGate", mock_gate),
            patch("genlab_core.engagement.comment_processor._rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = False
            # Should NOT raise — queued: check returns early before
            # rate_limit is even consulted.
            process_reply_event(event)

        # KEY ASSERTION: no second DB row written.
        # This is what stopped the 5,500/day duplication.
        assert mock_bl.write_pending_engagement.call_count == 1, (
            f"Cycle 2 wrote another pending_engagement row — "
            f"the 11,151 sports Threads duplication bug is back. "
            f"call_count={mock_bl.write_pending_engagement.call_count}"
        )
