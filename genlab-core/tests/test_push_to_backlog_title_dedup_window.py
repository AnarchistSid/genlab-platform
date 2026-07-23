"""Regression pin: per-niche title_dedup_days config wiring.

Closes the second-order dedup-saturation bug exposed 2026-06-13 (after
fix #2 unblocked the publish chain): anime + sports + movies have
trending content that cycles weekly (same show name across days, same
team name across games), so a 7-day title-similarity dedup window
rejected every fetched candidate.

Pre-fix: hardcoded `_TITLE_DEDUP_DAYS = 7`.
Post-fix: per-niche override via `pipeline.title_dedup_days` in niche.yaml;
default 7 preserves current behaviour for niches without the override.

If this test fails, the per-niche override is no longer reachable from
the niche_config — the regression would be invisible because both 2-day
and 7-day windows produce zero blueprints on dedup-saturated days.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def test_title_dedup_days_read_from_niche_config():
    """The push_to_backlog dedup block must respect the per-niche override."""
    import genlab_core.pipeline.stages.push_to_backlog as ptb_mod

    src = Path(ptb_mod.__file__).read_text()
    # The override is read inside the title-dedup block. Pin the literal
    # path through niche_config["pipeline"]["title_dedup_days"] so a
    # refactor that moves the key elsewhere fails this test deterministically.
    assert 'context.get("niche_config", {})' in src
    assert '.get("pipeline", {})' in src
    assert '.get("title_dedup_days", 7)' in src


def test_anime_config_has_2day_window():
    """FrameDrift (anime) must keep the 2-day window — anime trending
    cycles weekly. Raising this back to 7 would re-saturate the dedup."""
    cfg = yaml.safe_load(
        (Path(__file__).resolve().parent.parent.parent / "FrameDrift/config/niche.yaml").read_text()
    )
    assert cfg["pipeline"]["title_dedup_days"] == 2, (
        "Anime title_dedup_days must remain at 2 — raising it caused the "
        "2026-06-13 dedup-saturation outage. See test docstring + the "
        "session_2026_06_12_deep_dive_findings memory § B.9."
    )


def test_sports_and_movies_have_3day_window():
    """Sports/movies have less-aggressive cycling than anime but still
    want a tighter window than the default 7."""
    for niche_dir in ("ClutchWire", "SpliceReel"):
        cfg = yaml.safe_load(
            (
                Path(__file__).resolve().parent.parent.parent / f"{niche_dir}/config/niche.yaml"
            ).read_text()
        )
        assert cfg["pipeline"]["title_dedup_days"] == 3, (
            f"{niche_dir} title_dedup_days must remain at 3 — raising it back "
            "to 7 will re-saturate dedup for franchise sequels (movies) and "
            "moment-clips (sports)."
        )


def test_gaming_and_ai_creators_inherit_default():
    """Niches without the override should use 7 — gaming clips and AI
    creator content are unique enough that title cycling isn't a problem.
    Don't touch what works."""
    for path in (
        Path(__file__).resolve().parent.parent.parent / "BlackboxBrief/config/niche.yaml",
        Path(__file__).resolve().parent.parent.parent
        / "CriticalRush/niches/gaming/config/niche.yaml",
    ):
        cfg = yaml.safe_load(path.read_text())
        # Either explicitly omitted (inherits default 7) or explicitly set to 7
        v = cfg.get("pipeline", {}).get("title_dedup_days", 7)
        assert v == 7, f"{path}: expected 7 (default), got {v}"


def test_push_to_backlog_emits_decision_trace_source_pin():
    """2026-07-23: PushToBacklog must emit a decision trace (7th stage
    in the trace-emission rollout). Behavioral tests would need to
    fabricate the full backlog client + niche config stack; source-grep
    pin catches removal during refactor.
    """
    import inspect

    from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog

    src = inspect.getsource(PushToBacklog.execute)
    assert 'stage="PushToBacklog"' in src, (
        "PushToBacklog.execute must emit a decision trace with "
        "stage='PushToBacklog' (added 2026-07-23; symmetric with "
        "VideoGate/ViralityScoring/QCGates/PreDownloadDedup/"
        "ValidateVideos)"
    )
    assert "record_decision" in src, (
        "Must call record_decision (on-disk JSONL)"
    )
    assert "append_trace" in src, (
        "Must call append_trace (dashboard reasoning trace)"
    )
    # Warning-decision path must exist for the "0 blueprints from N stories"
    # precursor state.
    assert '"warning"' in src
