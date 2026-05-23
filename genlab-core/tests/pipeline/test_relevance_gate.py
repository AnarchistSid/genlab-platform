"""R-12: relevance gate must not SILENTLY fail-open on a broken filter config.

A content_filter that's present but has no positive_keywords is a
misconfiguration: previously the gate returned silently and off-niche content
shipped. It now logs an ERROR + records the error (fail-LOUD) while still
passing stories through — failing fully closed would dark the niche on a
config typo (and a dark/0-blueprint run is already caught by R-65/R-01).
"""

from __future__ import annotations

import logging

from genlab_core.pipeline.stages.relevance_gate import RelevanceGate


def _run_gate(tmp_path, sources_yaml: str | None, stories):
    niche_root = tmp_path / "niche"
    (niche_root / "config").mkdir(parents=True)
    if sources_yaml is not None:
        (niche_root / "config" / "sources.yaml").write_text(sources_yaml)
    ctx = {"stories": stories, "niche_id": "gaming", "niche_root": str(niche_root)}
    return RelevanceGate().execute(ctx)


def test_misconfigured_filter_fails_loud_not_closed(tmp_path, caplog):
    stories = [{"title": "a"}, {"title": "b"}]
    # content_filter present, but no positive_keywords — a misconfiguration.
    yaml_txt = "content_filter:\n  relevance_threshold: 0.35\n"
    with caplog.at_level(logging.ERROR):
        ctx = _run_gate(tmp_path, yaml_txt, stories)
    # Fail-LOUD: all stories pass (niche not darked) but the error is surfaced.
    assert len(ctx["stories"]) == 2
    assert ctx["run_stats"]["relevance_gate"]["error"]
    assert "positive_keywords" in caplog.text


def test_no_content_filter_passes_quietly(tmp_path):
    stories = [{"title": "a"}]
    # No content_filter key at all — relevance filtering is intentionally off.
    ctx = _run_gate(tmp_path, "sources:\n  yt: []\n", stories)
    assert len(ctx["stories"]) == 1
    assert "error" not in ctx.get("run_stats", {}).get("relevance_gate", {})


def test_missing_sources_yaml_passes(tmp_path):
    ctx = _run_gate(tmp_path, None, [{"title": "a"}])
    assert len(ctx["stories"]) == 1
