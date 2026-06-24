"""PR #523 (2026-06-24): v23 synthetic fallback for ``engaged_users``.

Meta deprecated ``post_engaged_users`` for new posts in v22.0 — the
field still returns numeric data for legacy posts but already returns
0 on newer ones, and will silently return 0 across the board once
v23 cuts over. Per the registry hint
(:data:`DEPRECATED_METRICS["post_engaged_users"]`):

    compute engagement from `post_reactions_*` + `post_comments`
    + `post_shares` instead.

This PR adds the synthetic fallback: when the direct field is 0 or
absent AND the component metrics sum to a positive value, recompose
``engaged_users`` from the components and mark the source so an
operator audit can distinguish v22 direct from v23 synthetic.

If these pins regress:

* the synthetic substitute disappears → ``engaged_users`` silently
  drops to 0 once v23 ships, deleting a stored signal that downstream
  analyses (Publishing_Analytics history, future RewardShaper
  iterations) depend on.
* the source-tag disappears → an operator looking at the dashboard
  can't tell whether a high engaged_users number is direct or
  derived → loses the ability to monitor the migration.

Same shape as PR #518's ``fb_reels_total_plays`` fallback (also a
v23-driven migration; we add the new metric alongside the old one,
prefer the new one only when it's signalled present, and don't break
v22 callers).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.learning.metrics.facebook import _fetch_facebook


@pytest.fixture(autouse=True)
def fb_token(monkeypatch):
    """All tests in this module need the FB token to even attempt a fetch."""
    monkeypatch.setenv("FB_PAGE_ACCESS_TOKEN", "tok_fb")


def _mock_insights_response(data):
    """Build a 200 OK mock with ``data`` payload."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": data}
    return resp


# ── synthesis kicks in when direct value is 0 ───────────────────────


def test_synthetic_engaged_users_when_direct_returns_zero(monkeypatch):
    """The v23 cutover case: direct returns 0 + reel insights have
    real reactions+comments+shares → synthesize.

    This is what we expect to see for every Reels post once Meta cuts
    over. Without the synthetic, engaged_users stays at 0 — losing
    the signal entirely.
    """
    insights_resp = _mock_insights_response(
        [
            {"name": "post_impressions", "values": [{"value": 5000}]},
            {"name": "post_engaged_users", "values": [{"value": 0}]},
        ]
    )

    # The reel-insights helper is the third endpoint; stub it to return
    # the components Meta still ships in v23.
    with (
        patch("requests.get", return_value=insights_resp),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_reel_insights",
            return_value={"shares": 20, "reactions": 100},
        ),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_video_object",
            return_value={"likes": 0, "comments": 25, "views": 5000},
        ),
    ):
        result = _fetch_facebook("fb_post_v23_case")

    # Synthesized: reactions(100) + comments(25) + shares(20) = 145
    assert result["engaged_users"] == 145
    assert result["engaged_users_source"] == "synthetic_v23", (
        "Synthetic path must tag its source so operators can audit "
        "the v22→v23 migration on existing rows."
    )


def test_synthetic_skipped_when_direct_returns_positive(monkeypatch):
    """The v22 happy path: direct returns a positive value → trust it.
    Source tag must NOT appear (existing dict shape preserved)."""
    insights_resp = _mock_insights_response(
        [
            {"name": "post_impressions", "values": [{"value": 5000}]},
            {"name": "post_engaged_users", "values": [{"value": 500}]},
        ]
    )
    with (
        patch("requests.get", return_value=insights_resp),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_reel_insights",
            return_value={"shares": 20, "reactions": 100},
        ),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_video_object",
            return_value={"likes": 0, "comments": 25, "views": 5000},
        ),
    ):
        result = _fetch_facebook("fb_post_v22_case")

    # Direct value preserved, no synthesis
    assert result["engaged_users"] == 500
    assert "engaged_users_source" not in result, (
        "Direct path must NOT add the source tag — preserves the "
        "long-standing dict shape so existing dashboards / tests don't "
        "see a new key appear under v22."
    )


def test_no_synthesis_when_components_sum_to_zero(monkeypatch):
    """The genuinely-empty case: direct=0 AND reactions+comments+shares=0.
    We should NOT add the synthetic-tag for a 0 value — distinguishing
    'silently zero' from 'genuinely zero' is the whole point."""
    insights_resp = _mock_insights_response(
        [
            {"name": "post_impressions", "values": [{"value": 5000}]},
            {"name": "post_engaged_users", "values": [{"value": 0}]},
        ]
    )
    with (
        patch("requests.get", return_value=insights_resp),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_reel_insights",
            return_value={"shares": 0},
        ),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_video_object",
            return_value={"likes": 0, "comments": 0, "views": 5000},
        ),
    ):
        result = _fetch_facebook("fb_post_genuinely_empty")

    # No synthesis attempted → engaged_users stays at the direct 0,
    # no source tag, no misleading "synthetic" label on a 0 value.
    assert result.get("engaged_users", 0) == 0
    assert "engaged_users_source" not in result, (
        "Adding the synthetic tag to a 0 value would falsely imply we "
        "computed from non-zero components — defeats the audit value."
    )


def test_synthesis_handles_post_engaged_users_missing(monkeypatch):
    """When Meta drops ``post_engaged_users`` from the response entirely
    (v23 future state), the parser never sets ``engaged_users`` to any
    value. The synthesis MUST kick in based on the components, not
    require the key to exist with value 0."""
    insights_resp = _mock_insights_response(
        [
            {"name": "post_impressions", "values": [{"value": 5000}]},
            # Note: post_engaged_users entirely absent
        ]
    )
    with (
        patch("requests.get", return_value=insights_resp),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_reel_insights",
            return_value={"shares": 5, "reactions": 50},
        ),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_video_object",
            return_value={"likes": 0, "comments": 10, "views": 5000},
        ),
    ):
        result = _fetch_facebook("fb_post_v23_no_field")

    # Synthesized: 50 + 10 + 5 = 65
    assert result["engaged_users"] == 65
    assert result["engaged_users_source"] == "synthetic_v23"


def test_synthesis_robust_to_string_typed_components(monkeypatch):
    """Meta occasionally returns numeric metrics as JSON strings. The
    synthesis must coerce to int (the existing direct-field parser
    does the same on line 222/224) — without this, sum() would crash."""
    insights_resp = _mock_insights_response(
        [
            {"name": "post_impressions", "values": [{"value": 5000}]},
            {"name": "post_engaged_users", "values": [{"value": 0}]},
        ]
    )
    with (
        patch("requests.get", return_value=insights_resp),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_reel_insights",
            # Strings simulate Meta's occasional habit of returning
            # numeric metrics wrapped in quotes — the coercion test.
            return_value={"shares": "30", "reactions": "200"},
        ),
        patch(
            "genlab_core.learning.metrics.facebook._fetch_facebook_video_object",
            return_value={"likes": 0, "comments": "15", "views": 5000},
        ),
    ):
        result = _fetch_facebook("fb_post_string_metrics")

    # Coerced + summed: 200 + 15 + 30 = 245
    assert result["engaged_users"] == 245
    assert result["engaged_users_source"] == "synthetic_v23"


# ── source pins ───────────────────────────────────────────────────


def test_source_pins_synthetic_formula_per_registry():
    """The synthesis formula MUST match the registry's replacement hint
    exactly: ``reactions + comments + shares``. If this drifts, the
    migration's correctness vs Meta's recommendation breaks."""
    from pathlib import Path

    import genlab_core.learning.metrics.facebook as mod

    src = Path(mod.__file__).read_text()
    # Pin the three component names appearing together in the synthesis
    # block. Order isn't critical (sum is commutative); presence is.
    assert 'reactions = int(metrics.get("reactions", 0)' in src
    assert 'comments = int(metrics.get("comments", 0)' in src
    assert 'shares = int(metrics.get("shares", 0)' in src
    assert "reactions + comments + shares" in src, (
        "Synthesis formula must match Meta's registry hint exactly."
    )


def test_source_pins_source_tag_only_on_synthetic_path():
    """The tag must be set ONLY in the synthetic branch — adding it on
    the direct path would change the dict shape of every v22 caller
    and break unrelated assertions."""
    from pathlib import Path

    import genlab_core.learning.metrics.facebook as mod

    src = Path(mod.__file__).read_text()
    # Count occurrences — should be exactly 1 (inside synthesis branch).
    occurrences = src.count('metrics["engaged_users_source"]')
    assert occurrences == 1, (
        f"engaged_users_source must be set exactly once (synthesis branch); "
        f"found {occurrences}. Two sites means direct path is also tagging — "
        f"will break existing v22 dict-shape tests."
    )
    # And it must be the synthetic tag value
    assert '"synthetic_v23"' in src


def test_source_pins_components_sum_guard():
    """Synthesis only fires when ``components_sum > 0`` — without this,
    we'd tag a genuinely-zero post with 'synthetic_v23' (misleading
    audit signal)."""
    from pathlib import Path

    import genlab_core.learning.metrics.facebook as mod

    src = Path(mod.__file__).read_text()
    assert "components_sum > 0" in src or "if components_sum:" in src, (
        "Synthesis must guard on components_sum>0 to avoid mis-labeling "
        "genuinely-zero posts as synthetic."
    )
