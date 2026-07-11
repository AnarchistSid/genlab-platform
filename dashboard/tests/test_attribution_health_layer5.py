"""PR #Layer5 (2026-07-11) — attribution health backend endpoint.

Source pins on the Layer 5 endpoint + core module. Full behavioural
tests would need a live Postgres fixture + seeded blueprint data — the
source pin catches structural regressions at import time with zero
runtime cost. Consistent with the sibling ``publishing_health_per_niche``
pattern.
"""

from __future__ import annotations

import re
from pathlib import Path


def _normalise(src: str) -> str:
    return re.sub(r"\s+", " ", src)


def test_endpoint_registered_in_review_server():
    """review_server.py must import + register the blueprint. Failure
    here means the endpoint won't be reachable in prod even if the
    handler module itself is fine."""
    import server.review_server as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "from server.api.attribution_health import" in src
    assert "app.register_blueprint(attribution_health_bp)" in src


def test_endpoint_module_exposes_stats_route():
    """The Layer 5 stats endpoint under /api/v1/attribution-health.
    A rename or route drift surfaces here."""
    import server.api.attribution_health as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "/api/v1/attribution-health" in src
    assert '@bp.route("/stats"' in src


def test_endpoint_clamps_window_hours_to_safe_range():
    """The endpoint must clamp window_hours to [1, 168] — an unbounded
    window would let an operator query the entire blueprint history
    and pin the DB. The clamp constants aren't magic numbers; they're
    pinned so a future refactor can't silently widen the range."""
    import server.api.attribution_health as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "_MIN_WINDOW = 1" in src
    assert "_MAX_WINDOW = 168" in src
    assert "max(_MIN_WINDOW, min(_MAX_WINDOW, window_hours))" in src


def test_endpoint_is_fail_open_on_db_error():
    """The card must render its zero-state on DB errors, not vanish
    with a 500. Pin the exception-swallow + empty-shape fallback."""
    import server.api.attribution_health as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "except Exception" in src
    # The fail-open path returns zero-shaped data
    assert '"status": "unknown"' in src


def test_core_module_computes_per_niche_and_overall():
    """The compute_stats function returns (per_niche, overall) so the
    endpoint can wrap both into the response. Pin the return shape."""
    import server.core.attribution_health as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "def compute_stats(" in src
    assert "return per_niche, overall" in src


def test_core_module_recognizes_three_attribution_signals():
    """The SQL must union THREE attribution signals per blueprint:
    (a) source_channel_id populated, (b) 🎬 Original: marker anywhere,
    (c) Footage: marker anywhere. Missing any signal would under-count
    attribution health and trigger spurious operator alerts."""
    import server.core.attribution_health as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "source_channel_id IS NOT NULL" in src
    # Emoji marker — file uses the escape form ``\U0001f3ac`` (ruff
    # format normalises to lowercase hex), so match the literal escape
    # string that appears in the source text. Both forms of the raw
    # unicode escape are accepted for future-proofing.
    assert (
        "\\U0001f3ac Original:" in src
        or "\\U0001F3AC Original:" in src
        or "\U0001f3ac Original:" in src
    )
    assert "Footage:" in src


def test_core_module_includes_all_five_niches_in_output():
    """Every known niche must appear in the output rows, even when
    zero-published. Otherwise a niche that fires zero publishes
    silently disappears from the dashboard — the operator would think
    everything is fine when actually the pipeline is broken."""
    import server.core.attribution_health as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert '_NICHE_IDS = ("ai_creators", "anime", "gaming", "movies", "sports")' in src


def test_core_module_status_thresholds_kept_in_sync_with_endpoint():
    """Endpoint and core module both declare _HEALTHY_PCT + _CAUTION_PCT.
    If someone changes one without the other, the response will show
    the wrong threshold constants to the frontend."""
    import server.api.attribution_health as api_mod
    import server.core.attribution_health as core_mod

    api_src = _normalise(Path(api_mod.__file__).read_text())
    core_src = _normalise(Path(core_mod.__file__).read_text())
    # Post-2026-07-11 audit: 100/99 instead of 95/90. Any single miss
    # is a real audience-facing failure — the operator needs to see it.
    assert "_HEALTHY_PCT = 100.0" in api_src
    assert "_HEALTHY_PCT = 100.0" in core_src
    assert "_CAUTION_PCT = 99.0" in api_src
    assert "_CAUTION_PCT = 99.0" in core_src
