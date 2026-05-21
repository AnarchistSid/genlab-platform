"""Verify gaming_flow.py reads stages from niche.yaml — no hardcoded list."""

from __future__ import annotations

import inspect
from pathlib import Path

import yaml

NICHE_YAML = Path(__file__).resolve().parent.parent / "niches" / "gaming" / "config" / "niche.yaml"


def test_gaming_flow_stages_match_niche_yaml():
    """gaming_flow stages must come from niche.yaml, not a hardcoded list."""
    from niches.gaming.flows.gaming_flow import _load_stages_from_config

    stages = _load_stages_from_config()

    with open(NICHE_YAML) as f:
        config = yaml.safe_load(f)
    expected = config["pipeline"]["stages"]

    # Compare class paths
    loaded_classes = [s["class"] for s in stages]
    expected_classes = [s["class"] for s in expected]

    assert loaded_classes == expected_classes, (
        f"gaming_flow stages diverge from niche.yaml.\n"
        f"  gaming_flow: {loaded_classes}\n"
        f"  niche.yaml:  {expected_classes}"
    )


def test_gaming_flow_has_no_hardcoded_stage_list():
    """gaming_flow.py source must not contain a hardcoded PIPELINE_STAGES constant."""
    import niches.gaming.flows.gaming_flow as gf

    source = inspect.getsource(gf)

    assert "PIPELINE_STAGES" not in source, (
        "gaming_flow.py still has hardcoded PIPELINE_STAGES constant"
    )

    # Module-level _make_task() calls with hardcoded module paths should be gone
    # (they should now be generated dynamically from niche.yaml)
    assert "fetch_stories = _make_task(" not in source, (
        "gaming_flow.py still has module-level hardcoded task definitions"
    )
    assert "publish_content = _make_task(" not in source, (
        "gaming_flow.py still has module-level hardcoded task definitions"
    )


def test_gaming_flow_load_stages_returns_list():
    """_load_stages_from_config returns a non-empty list of dicts with 'class' keys."""
    from niches.gaming.flows.gaming_flow import _load_stages_from_config

    stages = _load_stages_from_config()

    assert isinstance(stages, list)
    assert len(stages) > 0
    for s in stages:
        assert "class" in s, f"Stage entry missing 'class' key: {s}"


def test_gaming_flow_prefect_metadata_from_yaml():
    """Stage entries with retries/retry_delay_seconds are preserved from niche.yaml."""
    from niches.gaming.flows.gaming_flow import _load_stages_from_config

    stages = _load_stages_from_config()

    # Find the fetch stage — it should have retries=1, retry_delay_seconds=30
    fetch_stage = [s for s in stages if "FetchGamingStories" in s["class"]]
    assert len(fetch_stage) == 1
    assert fetch_stage[0].get("retries") == 1
    assert fetch_stage[0].get("retry_delay_seconds") == 30

    # push_to_backlog has retries=2
    backlog_stage = [s for s in stages if "PushToBacklog" in s["class"]]
    assert len(backlog_stage) == 1
    assert backlog_stage[0].get("retries") == 2
