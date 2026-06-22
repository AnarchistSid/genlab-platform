"""Pin: episodic rejection RAG for hook generation.

The 2026-06-22 autonomy audit identified Loop 8 (feedback_category)
as half-wired: every operator Reject/Revise captures one of 6
categorical reasons + PR #461 emits them to episodic_events. But
nothing READS that signal to ADAPT future generation.

This module closes the loop for hook generation:
``get_recent_rejection_context(niche_id)`` queries episodic_events
and returns a formatted system-prompt block warning the LLM about
recent operator-rejection patterns for the niche.

These pins guard:
- Fail-OPEN on every error path (no DB → "", no rows → "", exception → "")
- Categorical advice messages match canonical 6-reason enum
- 5-min cache TTL prevents per-hook DB load
- Wire into llm_hook_generator.py is structurally present
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestRejectionRagFailOpen:
    """The hook generator MUST work even when RAG fails — it's
    augmentation, not a hard dependency."""

    def test_no_database_url_returns_empty(self, monkeypatch):
        """Pin: DATABASE_URL unset → empty string (silent no-op)."""
        from genlab_core.learning import rejection_rag

        rejection_rag._reset_cache_for_tests()
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert rejection_rag.get_recent_rejection_context("gaming") == ""

    def test_database_error_returns_empty(self, monkeypatch):
        """Pin: psycopg.connect raising → empty string (fail-OPEN).
        RAG is augmentation; hook generation must continue without it."""
        from genlab_core.learning import rejection_rag

        rejection_rag._reset_cache_for_tests()
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        # psycopg.connect raising must NOT propagate
        with patch("psycopg.connect", side_effect=RuntimeError("DB down")):
            result = rejection_rag.get_recent_rejection_context("gaming")
        assert result == ""

    def test_no_rejections_in_window_returns_empty(self, monkeypatch):
        """Pin: empty rowset → empty string. Don't pollute prompts with
        '0 rejections in last 7 days' boilerplate."""
        from genlab_core.learning import rejection_rag

        rejection_rag._reset_cache_for_tests()
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        # Mock psycopg to return empty rowset
        fake_cur = MagicMock()
        fake_cur.fetchall.return_value = []
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur
        with patch("psycopg.connect") as fake_connect:
            fake_connect.return_value.__enter__.return_value = fake_conn
            result = rejection_rag.get_recent_rejection_context("gaming")
        assert result == ""


class TestRejectionRagFormatting:
    """When rejections exist, the context block must be unambiguous
    and actionable."""

    def test_single_category_formatted(self, monkeypatch):
        """Pin: one category → block has 'RECENT OPERATOR FEEDBACK'
        header + the category name + count + advice line."""
        from genlab_core.learning import rejection_rag

        rejection_rag._reset_cache_for_tests()
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        fake_cur = MagicMock()
        fake_cur.fetchall.return_value = [("weak_hook", 5)]
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur
        with patch("psycopg.connect") as fake_connect:
            fake_connect.return_value.__enter__.return_value = fake_conn
            result = rejection_rag.get_recent_rejection_context("gaming")
        assert "RECENT OPERATOR FEEDBACK" in result
        assert "AVOID" in result
        assert "weak_hook (5x)" in result
        # Canonical advice for weak_hook must be present
        assert "be more concrete" in result

    def test_multiple_categories_ordered_by_count(self, monkeypatch):
        """Pin: rows must appear in DESCENDING count order (highest
        signal first — the LLM pays more attention to early lines)."""
        from genlab_core.learning import rejection_rag

        rejection_rag._reset_cache_for_tests()
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        # SQL is ORDER BY n DESC LIMIT — query returns sorted, we
        # render in that order
        fake_cur = MagicMock()
        fake_cur.fetchall.return_value = [
            ("too_generic", 8),
            ("weak_hook", 3),
            ("low_value", 1),
        ]
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur
        with patch("psycopg.connect") as fake_connect:
            fake_connect.return_value.__enter__.return_value = fake_conn
            result = rejection_rag.get_recent_rejection_context("gaming")
        # Check ORDER: too_generic line index < weak_hook line index
        too_generic_idx = result.find("too_generic")
        weak_hook_idx = result.find("weak_hook")
        low_value_idx = result.find("low_value")
        assert -1 < too_generic_idx < weak_hook_idx < low_value_idx, (
            f"Categories must render in DESC count order. Got positions: "
            f"too_generic={too_generic_idx}, weak_hook={weak_hook_idx}, "
            f"low_value={low_value_idx}"
        )

    def test_unknown_category_handled_gracefully(self, monkeypatch):
        """Pin: a feedback_issue value outside the canonical 6 (e.g. a
        new category added in a future PR not yet mapped here) must
        not crash — uses generic advice instead."""
        from genlab_core.learning import rejection_rag

        rejection_rag._reset_cache_for_tests()
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        fake_cur = MagicMock()
        fake_cur.fetchall.return_value = [("brand_new_category", 4)]
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur
        with patch("psycopg.connect") as fake_connect:
            fake_connect.return_value.__enter__.return_value = fake_conn
            result = rejection_rag.get_recent_rejection_context("gaming")
        # Must not crash + must include the category name and count
        assert "brand_new_category (4x)" in result
        assert "avoid this pattern" in result


class TestRejectionRagCache:
    """5-min cache TTL: a single pipeline run produces 3-10 hooks per
    niche; we must NOT pay the DB query cost for each."""

    def test_cache_hit_skips_db(self, monkeypatch):
        """Pin: second call within TTL must NOT hit the DB."""
        from genlab_core.learning import rejection_rag

        rejection_rag._reset_cache_for_tests()
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        fake_cur = MagicMock()
        fake_cur.fetchall.return_value = [("weak_hook", 3)]
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur
        with patch("psycopg.connect") as fake_connect:
            fake_connect.return_value.__enter__.return_value = fake_conn
            first = rejection_rag.get_recent_rejection_context("gaming")
            second = rejection_rag.get_recent_rejection_context("gaming")
        # Same result returned twice
        assert first == second
        # psycopg.connect called ONCE (second was cache hit)
        assert fake_connect.call_count == 1


class TestWireIntoHookGenerator:
    """Source-level pin: the wire into llm_hook_generator.py is
    structurally present."""

    def test_hook_generator_imports_rag(self):
        """Pin: llm_hook_generator.py imports + calls
        get_recent_rejection_context. Source-level pin guards against
        accidental wire removal in future refactors."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "writing"
            / "llm_hook_generator.py"
        )
        content = src.read_text()
        assert "get_recent_rejection_context" in content, (
            "llm_hook_generator.py is missing the rejection RAG wire. "
            "Add: from genlab_core.learning.rejection_rag import "
            "get_recent_rejection_context"
        )
        # The wire must be inside the system-prompt-build path AND
        # wrapped in try/except (RAG is augmentation; never blocks)
        assert "rejection RAG failed" in content, (
            "llm_hook_generator.py is missing the try/except guard "
            "around the RAG call. RAG must fail-open."
        )
