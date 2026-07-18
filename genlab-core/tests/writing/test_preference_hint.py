"""Pin tests for preference_hint — Intelligence stack #4a.

Reads preference_data table at writer runtime, formats top-engagement-
ratio pairs as few-shot examples. Behavior contract:

1. **Fail-open on any DB error** — no DATABASE_URL, connection fails,
   query fails all return empty list. Writer continues without hint.
2. **Empty examples → empty section** — writer prompt unaffected when
   preference_data is empty (default state).
3. **Contrastive format** — chosen + rejected as BETTER / WORSE pair
   with engagement ratio annotation.
4. **Filters empty rows** — rows where chosen or rejected is empty
   don't produce noise pairs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.writing.preference_hint import (
    fetch_recent_preference_examples,
    format_preference_prompt_section,
)


class TestFetchFailOpen:
    def test_no_database_url_returns_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert fetch_recent_preference_examples("gaming") == []

    def test_db_connect_failure_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://bogus:0/nope")
        with patch("psycopg.connect", side_effect=RuntimeError("simulated")):
            result = fetch_recent_preference_examples("gaming")
        assert result == []

    def test_query_failure_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://bogus:0/nope")
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.execute.side_effect = RuntimeError("query fail")
        with patch("psycopg.connect", return_value=fake_conn):
            result = fetch_recent_preference_examples("gaming")
        assert result == []


class TestFetchQueryShape:
    def test_returns_normalized_dicts(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://test/db")
        fake_conn = MagicMock()
        fake_cur = fake_conn.cursor.return_value
        fake_cur.fetchall.return_value = [
            {
                "chosen_hook": "What does this phone have?",
                "rejected_hook": "New phone announcement",
                "engagement_ratio": 34.28,
            },
            {
                "chosen_hook": "Why devs are ditching this framework",
                "rejected_hook": "Framework update released",
                "engagement_ratio": 4.65,
            },
        ]

        with patch("psycopg.connect", return_value=fake_conn):
            result = fetch_recent_preference_examples("gaming", "instagram", limit=3)

        assert len(result) == 2
        assert result[0]["chosen"] == "What does this phone have?"
        assert result[0]["engagement_ratio"] == 34.28
        assert result[1]["engagement_ratio"] == 4.65

    def test_passes_niche_platform_limit_to_query(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://test/db")
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.fetchall.return_value = []

        with patch("psycopg.connect", return_value=fake_conn):
            fetch_recent_preference_examples("sports", "youtube", limit=5)

        # Query args (2nd positional to execute) should include our params
        query_args = fake_conn.cursor.return_value.execute.call_args[0][1]
        assert query_args == ("sports", "youtube", 5)


class TestFormatPromptSection:
    def test_empty_examples_returns_empty_string(self) -> None:
        assert format_preference_prompt_section([]) == ""

    def test_valid_examples_produce_prompt_section(self) -> None:
        section = format_preference_prompt_section(
            [
                {
                    "chosen": "Why devs are ditching this framework",
                    "rejected": "Framework update released",
                    "engagement_ratio": 4.65,
                }
            ]
        )
        assert "PREFERENCE-LEARNED EXAMPLES" in section
        assert "BETTER" in section
        assert "WORSE" in section
        assert "Why devs are ditching this framework" in section
        assert "Framework update released" in section
        # engagement ratio annotation present (Python rounds 4.65 → 4.7 with :.1f)
        assert "4.6" in section or "4.7" in section
        assert "engagement" in section.lower()

    def test_filters_examples_with_empty_chosen_or_rejected(self) -> None:
        """Pairs where either side is empty carry no learning signal."""
        section = format_preference_prompt_section(
            [
                {"chosen": "Good hook", "rejected": "", "engagement_ratio": 5.0},
                {"chosen": "", "rejected": "Bad hook", "engagement_ratio": 5.0},
                {"chosen": "Real chosen", "rejected": "Real rejected", "engagement_ratio": 3.0},
            ]
        )
        # Only the third pair survives filtering
        assert "Real chosen" in section
        assert "Good hook" not in section

    def test_all_filtered_returns_empty(self) -> None:
        """If all examples have empty sides, section is empty."""
        section = format_preference_prompt_section(
            [
                {"chosen": "", "rejected": "", "engagement_ratio": 5.0},
            ]
        )
        assert section == ""

    def test_prompt_discourages_verbatim_copy(self) -> None:
        """Section must explicitly tell LLM to learn PATTERN, not copy phrasing —
        otherwise LLM sees "example" and reproduces exactly, degrading variety."""
        section = format_preference_prompt_section(
            [{"chosen": "c", "rejected": "r", "engagement_ratio": 3.0}]
        )
        assert "NOT" in section  # "Do NOT copy the phrasing verbatim"
