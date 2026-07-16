"""Pin the transformation-bandit summary API (PR 14, 2026-07-05).

Covers:
  1. Empty result when BacklogClient construction fails
  2. Empty result when proxy.all() raises
  3. Filter to arm_type='transformation' OR arm_id-starts-with-transform
  4. Malformed arm_ids skipped without crashing
  5. Group by (niche, dimension) → top-N by rate
  6. rate = alpha / (alpha + beta), n_obs = alpha + beta - 2
  7. Server injects flag_enabled from env var

No live DB — proxy mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from server.api.transformation_bandit import _load_transformation_arms, _shape_response


class TestShapeResponse:
    def test_empty_arms_returns_empty_niches(self) -> None:
        result = _shape_response([])
        assert result["niches"] == {}

    def test_rate_computation(self) -> None:
        arms = [
            {
                "niche_id": "gaming",
                "dimension": "music_mood",
                "value": "cinematic",
                "alpha": 6.0,
                "beta": 4.0,
                "n_plays": 0,
                "arm_id": "transform__music_mood__cinematic",
            },
        ]
        result = _shape_response(arms)
        arm_out = result["niches"]["gaming"]["music_mood"][0]
        # rate = 6/10 = 0.6
        assert arm_out["rate"] == pytest.approx(0.6)
        # n_obs = 6 + 4 - 2 = 8
        assert arm_out["n_obs"] == 8

    def test_top_n_per_dimension_capped_at_3(self) -> None:
        arms = [
            {
                "niche_id": "gaming",
                "dimension": "music_mood",
                "value": f"mood_{i}",
                "alpha": 1.0 + i,
                "beta": 5.0,
                "n_plays": 0,
                "arm_id": f"transform__music_mood__mood_{i}",
            }
            for i in range(5)
        ]
        result = _shape_response(arms)
        # 5 arms → capped at top 3 by rate
        assert len(result["niches"]["gaming"]["music_mood"]) == 3
        # Highest rate first — alpha=5, beta=5 → 0.5 wins
        top = result["niches"]["gaming"]["music_mood"][0]
        assert top["value"] == "mood_4"

    def test_sorted_desc_by_rate(self) -> None:
        arms = [
            {
                "niche_id": "gaming",
                "dimension": "music_mood",
                "value": "low",
                "alpha": 1.0,
                "beta": 5.0,
                "n_plays": 0,
                "arm_id": "transform__music_mood__low",
            },
            {
                "niche_id": "gaming",
                "dimension": "music_mood",
                "value": "high",
                "alpha": 5.0,
                "beta": 1.0,
                "n_plays": 0,
                "arm_id": "transform__music_mood__high",
            },
        ]
        result = _shape_response(arms)
        arms_out = result["niches"]["gaming"]["music_mood"]
        # 'high' (rate=0.83) comes before 'low' (rate=0.17)
        assert arms_out[0]["value"] == "high"
        assert arms_out[1]["value"] == "low"

    def test_multi_niche_multi_dim(self) -> None:
        arms = [
            {
                "niche_id": "gaming",
                "dimension": "music_mood",
                "value": "hype",
                "alpha": 5.0,
                "beta": 3.0,
                "n_plays": 0,
                "arm_id": "transform__music_mood__hype",
            },
            {
                "niche_id": "movies",
                "dimension": "hook_framing",
                "value": "question",
                "alpha": 4.0,
                "beta": 2.0,
                "n_plays": 0,
                "arm_id": "transform__hook_framing__question",
            },
        ]
        result = _shape_response(arms)
        assert "gaming" in result["niches"]
        assert "movies" in result["niches"]
        assert "music_mood" in result["niches"]["gaming"]
        assert "hook_framing" in result["niches"]["movies"]

    def test_zero_total_skipped_gracefully(self) -> None:
        """alpha=0, beta=0 would divide by zero — shouldn't crash."""
        arms = [
            {
                "niche_id": "gaming",
                "dimension": "music_mood",
                "value": "cold",
                "alpha": 0.0,
                "beta": 0.0,
                "n_plays": 0,
                "arm_id": "transform__music_mood__cold",
            },
        ]
        # Should not crash on the div-by-zero attempt
        result = _shape_response(arms)
        arm = result["niches"]["gaming"]["music_mood"][0]
        assert arm["rate"] == 0.0
        assert arm["n_obs"] == 0


class TestLoadTransformationArms:
    def test_empty_on_backlog_client_failure(self) -> None:
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            side_effect=RuntimeError("no db"),
        ):
            result = _load_transformation_arms()
        assert result == []

    def test_empty_on_proxy_none(self) -> None:
        class _Client:
            bandit_arms = None

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=_Client(),
        ):
            result = _load_transformation_arms()
        assert result == []

    def test_empty_on_proxy_all_error(self) -> None:
        proxy = MagicMock()
        proxy.all.side_effect = RuntimeError("db down")

        class _Client:
            def __init__(self):
                self.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=_Client(),
        ):
            result = _load_transformation_arms()
        assert result == []

    def test_filters_to_transformation_arms(self) -> None:
        """Non-transformation arms (style, source, hour) are skipped."""
        items = [
            # Valid transformation arm
            {
                "id": "row_1",
                "fields": {
                    "arm_id": "transform__music_mood__cinematic",
                    "arm_type": "transformation",
                    "alpha": 5.0,
                    "beta": 3.0,
                    "niche_id": "movies",
                },
            },
            # Style arm — SKIPPED
            {
                "id": "row_2",
                "fields": {
                    "arm_id": "style:gaming:revelation",
                    "arm_type": "style",
                    "alpha": 5.0,
                    "beta": 3.0,
                    "niche_id": "gaming",
                },
            },
            # Malformed arm_id — SKIPPED
            {
                "id": "row_3",
                "fields": {
                    "arm_id": "transform__weird",
                    "alpha": 5.0,
                    "beta": 3.0,
                    "niche_id": "movies",
                },
            },
        ]
        proxy = MagicMock()
        proxy.all.return_value = items

        class _Client:
            def __init__(self):
                self.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=_Client(),
        ):
            result = _load_transformation_arms()
        assert len(result) == 1
        assert result[0]["arm_id"] == "transform__music_mood__cinematic"

    def test_accepts_transform_prefix_even_without_arm_type(self) -> None:
        """Belt AND suspenders: rows with correct arm_id but missing
        arm_type (backfill not yet run) still show up."""
        items = [
            {
                "id": "row_1",
                "fields": {
                    "arm_id": "transform__music_mood__cinematic",
                    # arm_type absent — legacy row from before migration
                    "alpha": 5.0,
                    "beta": 3.0,
                    "niche_id": "movies",
                },
            },
        ]
        proxy = MagicMock()
        proxy.all.return_value = items

        class _Client:
            def __init__(self):
                self.bandit_arms = proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=_Client(),
        ):
            result = _load_transformation_arms()
        assert len(result) == 1


class TestFlagEnabledInjection:
    def test_flag_enabled_true(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        result = _shape_response([])
        assert result["flag_enabled"] is True

    def test_flag_enabled_false_when_absent(self, monkeypatch) -> None:
        monkeypatch.delenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raising=False)
        result = _shape_response([])
        assert result["flag_enabled"] is False

    def test_flag_case_insensitive(self, monkeypatch) -> None:
        for value in ("true", "TRUE", "yes", "on"):
            monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", value)
            result = _shape_response([])
            assert result["flag_enabled"] is True, f"value={value!r}"

    def test_flag_false_for_falsy(self, monkeypatch) -> None:
        for value in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", value)
            result = _shape_response([])
            assert result["flag_enabled"] is False, f"value={value!r}"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
