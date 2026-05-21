"""Tests for genlab_core.http — async_bridge, retry, graph_proxy, backlog_client."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

# ── async_bridge tests ──────────────────────────────────────────────


class TestRunAsync:
    """Test the persistent event loop async-to-sync bridge."""

    def test_run_async_returns_coroutine_result(self):
        from genlab_core.http.async_bridge import run_async

        async def greet():
            return "hello"

        assert run_async(greet()) == "hello"

    def test_run_async_timeout_raises(self):
        from genlab_core.http.async_bridge import run_async

        async def slow():
            await asyncio.sleep(10)
            return "done"

        with pytest.raises(TimeoutError, match="timed out"):
            run_async(slow(), timeout=0.1)

    def test_run_async_propagates_exception(self):
        from genlab_core.http.async_bridge import run_async

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_async(fail())

    def test_persistent_loop_reused(self):
        from genlab_core.http.async_bridge import _get_loop

        loop1 = _get_loop()
        loop2 = _get_loop()
        assert loop1 is loop2
        assert loop1.is_running()


# ── retry tests ─────────────────────────────────────────────────────


class TestRetry:
    """Test the retry decorator."""

    def test_succeeds_first_try(self):
        from genlab_core.http.retry import retry

        call_count = 0

        @retry(max_attempts=3, initial_delay=0.01, exceptions=(ValueError,))
        def ok():
            nonlocal call_count
            call_count += 1
            return 42

        assert ok() == 42
        assert call_count == 1

    def test_retries_then_succeeds(self):
        from genlab_core.http.retry import retry

        call_count = 0

        @retry(max_attempts=3, initial_delay=0.01, exceptions=(ValueError,))
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "ok"

        assert flaky() == "ok"
        assert call_count == 3

    def test_exhausts_retries_raises(self):
        from genlab_core.http.retry import retry

        @retry(max_attempts=2, initial_delay=0.01, exceptions=(ValueError,))
        def always_fail():
            raise ValueError("permanent")

        with pytest.raises(ValueError, match="permanent"):
            always_fail()

    def test_ignores_non_matching_exceptions(self):
        from genlab_core.http.retry import retry

        @retry(max_attempts=3, initial_delay=0.01, exceptions=(ValueError,))
        def wrong_error():
            raise TypeError("nope")

        with pytest.raises(TypeError):
            wrong_error()

    def test_backoff_multiplier(self):
        from genlab_core.http.retry import retry

        delays = []

        with patch("genlab_core.http.retry.time") as mock_time:
            mock_time.sleep = lambda d: delays.append(d)

            @retry(max_attempts=3, initial_delay=1.0, backoff=3.0, exceptions=(RuntimeError,))
            def fail():
                raise RuntimeError("fail")

            with pytest.raises(RuntimeError):
                fail()

        assert len(delays) == 2
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(3.0)


# ── formula_to_odata tests ──────────────────────────────────────────


class TestFormulaToOdata:
    """Test the legacy formula → OData translator."""

    def test_none_returns_none(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        assert formula_to_odata(None) is None
        assert formula_to_odata("") is None
        assert formula_to_odata("   ") is None

    def test_simple_field_eq(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("{status}='INTAKE'")
        assert result == "fields/status eq 'INTAKE'"

    def test_and_formula(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("AND({status}='INTAKE', {niche_id}='gaming')")
        assert result == "fields/status eq 'INTAKE' and fields/niche_id eq 'gaming'"

    def test_or_formula(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("OR({status}='INTAKE', {status}='DRAFTED')")
        assert result == "(fields/status eq 'INTAKE' or fields/status eq 'DRAFTED')"

    def test_true_formula(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("{enabled}=TRUE()")
        assert result == "fields/enabled eq 1"

    def test_false_formula(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("{archived}=FALSE()")
        assert result == "fields/archived eq 0"

    def test_blank_formula(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("{scheduled_for}=BLANK()")
        assert result == "fields/scheduled_for eq null"

    def test_comparison_operators(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        assert formula_to_odata("{date}>='2026-01-01'") == "fields/date ge '2026-01-01'"
        assert formula_to_odata("{score}!='low'") == "fields/score ne 'low'"

    def test_numeric_eq(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("{priority}=5")
        assert result == "fields/priority eq 5"

    def test_find_arrayjoin(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("FIND('abc', ARRAYJOIN({link}))")
        assert result == "contains(fields/link_text, 'abc')"

    def test_find_bare(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("FIND('test', {title})")
        assert result == "contains(fields/title, 'test')"

    def test_datestr_formula(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata("DATESTR({created})='2026-03-07'")
        assert "fields/created ge '2026-03-07T00:00:00Z'" in result
        assert "fields/created lt '2026-03-08T00:00:00Z'" in result

    def test_nested_and_or(self):
        from genlab_core.http.graph_proxy import formula_to_odata

        result = formula_to_odata(
            "AND(OR({status}='INTAKE', {status}='DRAFTED'), {niche_id}='gaming')"
        )
        assert "or" in result
        assert "and" in result

    def test_escape_single_quotes(self):
        from genlab_core.http.graph_proxy import _esc

        assert _esc("it's") == "it\\'s"
        assert _esc("") == ""


class TestSplitTopLevel:
    def test_simple(self):
        from genlab_core.http.graph_proxy import _split_top_level

        result = _split_top_level("{a}='1', {b}='2'")
        assert len(result) == 2

    def test_nested_parens(self):
        from genlab_core.http.graph_proxy import _split_top_level

        result = _split_top_level("OR({a}='1', {b}='2'), {c}='3'")
        assert len(result) == 2
        assert result[0].startswith("OR(")


# ── ScheduleGuardedProxy tests ──────────────────────────────────────


class TestScheduleGuardedProxy:
    """Test the scheduled post protection wrapper."""

    def test_blocks_status_demotion_on_scheduled(self):
        from genlab_core.http.backlog_client import (
            ScheduledPostProtectionError,
            ScheduleGuardedProxy,
        )

        inner = MagicMock()
        inner.get.return_value = {
            "fields": {
                "status": "SCHEDULED",
                "scheduled_for": "2026-03-10T10:00:00Z",
            }
        }
        proxy = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="Cannot demote"):
            proxy.update("123", {"status": "DRAFTED"})

    def test_allows_promotion_on_scheduled(self):
        from genlab_core.http.backlog_client import ScheduleGuardedProxy

        inner = MagicMock()
        inner.get.return_value = {
            "fields": {
                "status": "SCHEDULED",
                "scheduled_for": "2026-03-10T10:00:00Z",
            }
        }
        inner.update.return_value = {"id": "123", "fields": {"status": "PUBLISHED"}}
        proxy = ScheduleGuardedProxy(inner)

        result = proxy.update("123", {"status": "PUBLISHED"})
        inner.update.assert_called_once()
        assert result["fields"]["status"] == "PUBLISHED"

    def test_blocks_delete_on_scheduled(self):
        from genlab_core.http.backlog_client import (
            ScheduledPostProtectionError,
            ScheduleGuardedProxy,
        )

        inner = MagicMock()
        inner.get.return_value = {"fields": {"scheduled_for": "2026-03-10T10:00:00Z"}}
        proxy = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="Cannot delete"):
            proxy.delete("123")

    def test_force_bypasses_guard(self):
        from genlab_core.http.backlog_client import ScheduleGuardedProxy

        inner = MagicMock()
        inner.update.return_value = {"id": "123", "fields": {"status": "INTAKE"}}
        proxy = ScheduleGuardedProxy(inner)

        proxy.update("123", {"status": "INTAKE"}, force=True)
        inner.update.assert_called_once()
        # get() should NOT have been called — force skips the guard
        inner.get.assert_not_called()

    def test_allow_scheduled_updates_context_manager(self):
        from genlab_core.http.backlog_client import (
            ScheduleGuardedProxy,
            allow_scheduled_updates,
        )

        inner = MagicMock()
        inner.get.return_value = {
            "fields": {
                "status": "SCHEDULED",
                "scheduled_for": "2026-03-10",
            }
        }
        inner.update.return_value = {"id": "1", "fields": {"status": "INTAKE"}}
        proxy = ScheduleGuardedProxy(inner)

        with allow_scheduled_updates():
            proxy.update("1", {"status": "INTAKE"})

        inner.update.assert_called_once()

    def test_blocks_clearing_visual_paths(self):
        from genlab_core.http.backlog_client import (
            ScheduledPostProtectionError,
            ScheduleGuardedProxy,
        )

        inner = MagicMock()
        inner.get.return_value = {
            "fields": {
                "visual_paths": "/some/path.png",
                "scheduled_for": "2026-03-10",
            }
        }
        proxy = ScheduleGuardedProxy(inner)

        with pytest.raises(ScheduledPostProtectionError, match="visual_paths"):
            proxy.update("1", {"visual_paths": ""})

    def test_unguarded_fields_pass_through(self):
        from genlab_core.http.backlog_client import ScheduleGuardedProxy

        inner = MagicMock()
        inner.update.return_value = {"id": "1", "fields": {"hook": "new hook"}}
        proxy = ScheduleGuardedProxy(inner)

        proxy.update("1", {"hook": "new hook"})
        inner.update.assert_called_once()
        # get() should NOT be called — no guarded fields touched
        inner.get.assert_not_called()

    def test_is_demotion_logic(self):
        from genlab_core.http.backlog_client import ScheduleGuardedProxy

        assert ScheduleGuardedProxy._is_demotion("SCHEDULED", "DRAFTED") is True
        assert ScheduleGuardedProxy._is_demotion("DRAFTED", "SCHEDULED") is False
        assert ScheduleGuardedProxy._is_demotion("PUBLISHED", "PUBLISHED") is False
        # Unknown status → not a demotion
        assert ScheduleGuardedProxy._is_demotion("UNKNOWN", "DRAFTED") is False


# ── GraphTableProxy._prepare_fields tests ───────────────────────────


class TestPrepareFields:
    """Test field preparation logic (no Graph API calls)."""

    def _make_proxy(self):
        """Build a proxy with mocked Graph client and pre-set column map."""
        from genlab_core.http.graph_proxy import GraphTableProxy

        with patch.object(GraphTableProxy, "_load_column_map"):
            proxy = GraphTableProxy(MagicMock(), "site", "list", "Test")
        proxy._display_to_internal = {"hook_text": "hook"}
        proxy._internal_to_display = {"hook": "hook_text"}
        return proxy

    def test_linked_record_field(self):
        proxy = self._make_proxy()
        result = proxy._prepare_fields({"story": ["rec-123"]})
        assert result["story_link"] == "rec-123"
        assert "story" not in result

    def test_file_field_extraction(self):
        proxy = self._make_proxy()
        result = proxy._prepare_fields({"file": [{"url": "https://example.com/img.png"}]})
        assert result["file"] == "https://example.com/img.png"

    def test_list_to_csv(self):
        proxy = self._make_proxy()
        result = proxy._prepare_fields({"themes": ["AI", "ML", "NLP"]})
        assert result["themes"] == "AI, ML, NLP"

    def test_none_skipped(self):
        proxy = self._make_proxy()
        result = proxy._prepare_fields({"title": "foo", "empty": None})
        assert "empty" not in result
        assert result["title"] == "foo"

    def test_clear_sentinel(self):
        from genlab_core.http.graph_proxy import GraphTableProxy

        proxy = self._make_proxy()
        result = proxy._prepare_fields({"scheduled_for": GraphTableProxy.CLEAR})
        assert result["scheduled_for"] is None

    def test_field_alias_reversal(self):
        proxy = self._make_proxy()
        result = proxy._prepare_fields({"hook_text": "catchy hook"})
        # hook_text → hook (reverse alias) → hook (internal name via map)
        assert result.get("hook") == "catchy hook"

    def test_display_to_internal_name_mapping(self):
        proxy = self._make_proxy()
        proxy._display_to_internal["My Display Name"] = "myInternalCol"
        result = proxy._prepare_fields({"My Display Name": "value"})
        assert "myInternalCol" in result


# ── BacklogClient helper tests ──────────────────────────────────────


class TestBacklogClientHelpers:
    """Test domain methods that don't need a live Graph connection."""

    def test_resolve_source_known_domain(self):
        from genlab_core.http.backlog_client import BacklogClient

        # Test without instantiation — call the method directly
        bc = object.__new__(BacklogClient)
        assert bc._resolve_source({"source": "Other", "domain": "openai.com/blog"}) == "OpenAI"

    def test_resolve_source_explicit(self):
        from genlab_core.http.backlog_client import BacklogClient

        bc = object.__new__(BacklogClient)
        assert bc._resolve_source({"source": "My Source"}) == "My Source"

    def test_resolve_source_fallback(self):
        from genlab_core.http.backlog_client import BacklogClient

        bc = object.__new__(BacklogClient)
        assert bc._resolve_source({"source": "Other", "domain": "random.org"}) == "Other"

    def test_normalize_asset_source_type(self):
        from genlab_core.http.backlog_client import BacklogClient

        bc = object.__new__(BacklogClient)
        assert bc._normalize_asset_source_type("og_image", "image") == "og_image"
        assert bc._normalize_asset_source_type("url", "video") == "video_embed"
        assert bc._normalize_asset_source_type("url", "image") == "hero_image"
        assert bc._normalize_asset_source_type("unsplash", "image") == "stock_search"
        assert bc._normalize_asset_source_type("generator", "") == "generated"

    def test_is_demotion(self):
        from genlab_core.http.backlog_client import BacklogClient

        bc = object.__new__(BacklogClient)
        assert bc._is_demotion("SCHEDULED", "DRAFTED") is True
        assert bc._is_demotion("DRAFTED", "SCHEDULED") is False
        assert bc._is_demotion("UNKNOWN", "DRAFTED") is False

    def test_assert_not_scheduled_raises_on_demotion(self):
        from genlab_core.http.backlog_client import BacklogClient

        bc = object.__new__(BacklogClient)
        blueprint = {
            "fields": {
                "status": "SCHEDULED",
                "scheduled_for": "2026-03-10",
                "candidate_id": "cand-1",
            }
        }
        with pytest.raises(ValueError, match="Refusing to demote"):
            bc.assert_not_scheduled(blueprint, "DRAFTED")

    def test_assert_not_scheduled_allows_promotion(self):
        from genlab_core.http.backlog_client import BacklogClient

        bc = object.__new__(BacklogClient)
        blueprint = {
            "fields": {
                "status": "SCHEDULED",
                "scheduled_for": "2026-03-10",
            }
        }
        # Should not raise
        bc.assert_not_scheduled(blueprint, "PUBLISHED")


# ── BACKLOG_OP_ERRORS tests ─────────────────────────────────────────


class TestBacklogOpErrors:
    def test_backlog_op_errors_is_tuple(self):
        from genlab_core.http.backlog_client import BACKLOG_OP_ERRORS

        assert isinstance(BACKLOG_OP_ERRORS, tuple)
        assert TimeoutError in BACKLOG_OP_ERRORS
        assert ValueError in BACKLOG_OP_ERRORS
