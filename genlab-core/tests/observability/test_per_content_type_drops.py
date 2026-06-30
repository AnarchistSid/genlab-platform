"""C3 (2026-06-30) — per-content_type drop accounting pins.

Pins ``PipelineMetrics.record_filter_drops(stage_name, before, after)``
and its surface in ``filter_drops_by_content_type``. The filter stages
(RelevanceGate / VideoGate / PushToBacklog) call this with their
input + output story lists; run_report.py surfaces the aggregated
view in ``metrics.filter_drops_by_content_type``.

Without these pins, a future refactor that drops the content_type
default ("unknown") or changes the dict shape would silently break
the per-content_type bottleneck attribution operators rely on.
"""

from __future__ import annotations

import inspect

from genlab_core.observability.metrics_writer import PipelineMetrics

# ── API surface pins ───────────────────────────────────────────────


def test_record_filter_drops_method_exists_with_correct_signature():
    """The wire must expose ``record_filter_drops(stage_name, before,
    after)`` on PipelineMetrics — callers in pipeline/stages/ depend
    on the exact arg names."""
    pm = PipelineMetrics()
    assert hasattr(pm, "record_filter_drops")
    sig = inspect.signature(pm.record_filter_drops)
    params = list(sig.parameters.keys())
    assert params == ["stage_name", "before", "after"], (
        f"record_filter_drops signature changed: got {params}, "
        "expected ['stage_name', 'before', 'after']"
    )


def test_filter_drops_by_content_type_property_exists():
    """The aggregated view must be exposed as a property on the
    PipelineMetrics instance. run_report.py reads it via attribute
    access (not a method call)."""
    pm = PipelineMetrics()
    assert hasattr(pm, "filter_drops_by_content_type")
    # Must be a property (no parens needed at read-site)
    drops = pm.filter_drops_by_content_type
    assert isinstance(drops, dict)
    assert drops == {}  # empty on fresh instance


# ── happy-path math pins ───────────────────────────────────────────


def test_record_filter_drops_basic_diff():
    """before=[showcase, news], after=[news] →
    showcase: {before:1, after:0, dropped:1}
    news:     {before:1, after:1, dropped:0}"""
    pm = PipelineMetrics()
    before = [
        {"title": "AI demo", "content_type": "showcase"},
        {"title": "GPT-5 leaked", "content_type": "news"},
    ]
    after = [
        {"title": "GPT-5 leaked", "content_type": "news"},
    ]
    pm.record_filter_drops("RelevanceGate", before, after)

    drops = pm.filter_drops_by_content_type
    assert "RelevanceGate" in drops
    rg = drops["RelevanceGate"]
    assert rg["showcase"] == {"before": 1, "after": 0, "dropped": 1}
    assert rg["news"] == {"before": 1, "after": 1, "dropped": 0}


def test_record_filter_drops_multi_bucket_diff():
    """Realistic spec-example shape: showcase 12→4, news 8→7,
    unknown 3→3."""
    pm = PipelineMetrics()
    before = (
        [{"content_type": "showcase"}] * 12
        + [{"content_type": "news"}] * 8
        + [{"content_type": "unknown"}] * 3
    )
    after = (
        [{"content_type": "showcase"}] * 4
        + [{"content_type": "news"}] * 7
        + [{"content_type": "unknown"}] * 3
    )
    pm.record_filter_drops("RelevanceGate", before, after)

    rg = pm.filter_drops_by_content_type["RelevanceGate"]
    assert rg["showcase"] == {"before": 12, "after": 4, "dropped": 8}
    assert rg["news"] == {"before": 8, "after": 7, "dropped": 1}
    assert rg["unknown"] == {"before": 3, "after": 3, "dropped": 0}


# ── defensive pins ─────────────────────────────────────────────────


def test_missing_content_type_buckets_as_unknown():
    """Stories without content_type → bucketed as 'unknown'. This is
    the dominant case for legacy stories that predate the field."""
    pm = PipelineMetrics()
    before = [{"title": "no type 1"}, {"title": "no type 2"}]
    after = [{"title": "no type 2"}]
    pm.record_filter_drops("VideoGate", before, after)

    vg = pm.filter_drops_by_content_type["VideoGate"]
    assert vg["unknown"] == {"before": 2, "after": 1, "dropped": 1}


def test_none_content_type_buckets_as_unknown():
    """content_type=None (explicit) → still 'unknown', not crash.
    Some upstream stages set the key to None as a sentinel."""
    pm = PipelineMetrics()
    before = [{"content_type": None}, {"content_type": "news"}]
    after = [{"content_type": "news"}]
    pm.record_filter_drops("PushToBacklog", before, after)

    p = pm.filter_drops_by_content_type["PushToBacklog"]
    assert p["unknown"] == {"before": 1, "after": 0, "dropped": 1}
    assert p["news"] == {"before": 1, "after": 1, "dropped": 0}


def test_empty_before_and_after_records_nothing():
    """Both lists empty → stage key still recorded with empty bucket
    dict. Consumers can distinguish 'stage ran on 0 items' from
    'stage never ran' (missing key)."""
    pm = PipelineMetrics()
    pm.record_filter_drops("RelevanceGate", [], [])
    drops = pm.filter_drops_by_content_type
    assert "RelevanceGate" in drops
    assert drops["RelevanceGate"] == {}


def test_all_dropped_records_full_drop():
    """All items dropped → 'after' bucket missing for the content_type
    but the bucket still shows up with after=0, dropped=N."""
    pm = PipelineMetrics()
    before = [{"content_type": "news"}] * 5
    after: list[dict] = []
    pm.record_filter_drops("RelevanceGate", before, after)

    rg = pm.filter_drops_by_content_type["RelevanceGate"]
    assert rg["news"] == {"before": 5, "after": 0, "dropped": 5}


def test_record_filter_drops_overwrites_per_stage():
    """Second call with same stage_name overwrites the first record —
    matches the semantic 'this stage just ran; here are its drops'.
    Important so re-runs in long-lived metrics instances (tests,
    REPL) don't accumulate stale data."""
    pm = PipelineMetrics()
    pm.record_filter_drops("VideoGate", [{"content_type": "showcase"}] * 3, [])
    pm.record_filter_drops("VideoGate", [{"content_type": "news"}] * 5, [{"content_type": "news"}])

    vg = pm.filter_drops_by_content_type["VideoGate"]
    # showcase bucket from the first call is gone
    assert "showcase" not in vg
    assert vg["news"] == {"before": 5, "after": 1, "dropped": 4}


def test_filter_drops_property_returns_deep_copy():
    """Mutating the returned dict must NOT corrupt internal state.
    Consumers (dashboard, run_report) iterate freely without
    needing to copy defensively."""
    pm = PipelineMetrics()
    pm.record_filter_drops("RelevanceGate", [{"content_type": "news"}], [])
    drops = pm.filter_drops_by_content_type
    # Mutate the returned snapshot
    drops["RelevanceGate"]["news"]["before"] = 999
    drops["INJECTED"] = {"x": {"before": 1, "after": 0, "dropped": 1}}

    # Internal state untouched
    fresh = pm.filter_drops_by_content_type
    assert fresh["RelevanceGate"]["news"]["before"] == 1
    assert "INJECTED" not in fresh


def test_multiple_stages_isolated():
    """Each stage gets its own top-level key. Drops accounted for
    one stage MUST NOT bleed into another stage's record."""
    pm = PipelineMetrics()
    pm.record_filter_drops(
        "RelevanceGate",
        [{"content_type": "showcase"}, {"content_type": "news"}],
        [{"content_type": "news"}],
    )
    pm.record_filter_drops(
        "VideoGate",
        [{"content_type": "news"}, {"content_type": "news"}],
        [{"content_type": "news"}],
    )

    drops = pm.filter_drops_by_content_type
    assert set(drops.keys()) == {"RelevanceGate", "VideoGate"}
    assert drops["RelevanceGate"]["showcase"]["dropped"] == 1
    assert drops["VideoGate"]["news"]["dropped"] == 1
    # No bleed
    assert "showcase" not in drops["VideoGate"]


# ── shape pin: stage call-site search ─────────────────────────────


def test_three_target_stages_call_record_filter_drops():
    """The audit specified three stages that must surface drops:
    RelevanceGate, VideoGate, PushToBacklog. Pin via source-text
    search so a future refactor that removes the wire surfaces."""
    from pathlib import Path

    from genlab_core.pipeline.stages import (
        push_to_backlog,
        relevance_gate,
        video_gate,
    )

    for mod, stage_name in [
        (relevance_gate, "RelevanceGate"),
        (video_gate, "VideoGate"),
        (push_to_backlog, "PushToBacklog"),
    ]:
        src = Path(mod.__file__).read_text()
        assert "record_filter_drops(" in src, (
            f"{stage_name} ({mod.__file__}) no longer calls record_filter_drops — C3 wire removed?"
        )


# ── shape pin: run_report emits the dict ──────────────────────────


def test_run_report_emits_filter_drops_in_metrics_section():
    """run_report.py must include `filter_drops_by_content_type`
    under the `metrics` section of the JSON output. Source-text pin
    so a future refactor that drops the key surfaces."""
    from pathlib import Path

    from genlab_core.pipeline.stages import run_report

    src = Path(run_report.__file__).read_text()
    assert "filter_drops_by_content_type" in src, (
        "run_report.py no longer emits filter_drops_by_content_type — C3 surface removed?"
    )
