"""Pin Phase 3.E FB self-comment amplifier runner:

  * Route disabled → 0 posts commented (never queries DB)
  * No YT sibling → post skipped, counted separately
  * Dry-run doesn't call post_facebook_self_comment
  * Main exits 1 without DATABASE_URL
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "run_fb_self_comment_amplifier",
    _ROOT / "scripts" / "run_fb_self_comment_amplifier.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["run_fb_self_comment_amplifier"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestRunNiche:
    def _fake_conn(self, eligible_rows=(), yt_url=None):
        conn = MagicMock()

        def _execute(sql, *args):
            result = MagicMock()
            if "COUNT" in sql:
                result.fetchone.return_value = {"n": 0}
            elif "publishing_analytics pa" in sql and "views >=" in sql:
                # eligible-post query
                result.fetchall.return_value = list(eligible_rows)
            elif "youtube" in sql and "LIMIT 1" in sql:
                # yt-sibling query
                result.fetchone.return_value = (
                    {"post_url": yt_url} if yt_url else None
                )
            else:
                result.fetchone.return_value = None
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute
        return conn

    def test_route_disabled_zero_calls(self):
        with patch("genlab_core.publishing.cross_post_amplify._route_enabled",
                   return_value=False):
            conn = self._fake_conn()
            counts = _MOD._run_niche(conn, "gaming", dry_run=True)
        assert counts == {"eligible": 0, "no_sibling": 0, "commented": 0, "failed": 0}
        conn.execute.assert_not_called()

    def test_eligible_but_no_yt_sibling(self):
        eligible = [{
            "fb_post_id": "fb_1", "fb_reach": 5000, "blueprint_id": "bp_1",
        }]
        with patch("genlab_core.publishing.cross_post_amplify._route_enabled",
                   return_value=True), \
             patch("genlab_core.publishing.cross_post_amplify._fb_min_reach_threshold",
                   return_value=1000):
            conn = self._fake_conn(eligible_rows=eligible, yt_url=None)
            counts = _MOD._run_niche(conn, "gaming", dry_run=True)
        assert counts["eligible"] == 1
        assert counts["no_sibling"] == 1
        assert counts["commented"] == 0

    def test_dry_run_doesnt_call_amplify_module(self):
        eligible = [{
            "fb_post_id": "fb_1", "fb_reach": 5000, "blueprint_id": "bp_1",
        }]
        with patch("genlab_core.publishing.cross_post_amplify._route_enabled",
                   return_value=True), \
             patch("genlab_core.publishing.cross_post_amplify._fb_min_reach_threshold",
                   return_value=1000), \
             patch(
                 "genlab_core.publishing.cross_post_amplify.post_facebook_self_comment"
             ) as mock_post:
            conn = self._fake_conn(
                eligible_rows=eligible,
                yt_url="https://youtube.com/x",
            )
            _MOD._run_niche(conn, "gaming", dry_run=True)
        mock_post.assert_not_called()


class TestFindEligible:
    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._find_eligible_fb_posts(conn, "gaming", 1000) == []

    def test_normalizes_dict_rows(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"fb_post_id": "fb_1", "fb_reach": 5000, "blueprint_id": "bp_1"},
        ]
        rows = _MOD._find_eligible_fb_posts(conn, "gaming", 1000)
        assert len(rows) == 1
        assert rows[0]["fb_post_id"] == "fb_1"
        assert rows[0]["fb_reach"] == 5000


class TestFindYtSibling:
    def test_missing_blueprint_returns_none(self):
        conn = MagicMock()
        assert _MOD._find_yt_sibling(conn, None, "gaming") is None
        conn.execute.assert_not_called()

    def test_db_error_returns_none(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._find_yt_sibling(conn, "bp_1", "gaming") is None


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1


class TestActiveNiches:
    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }
