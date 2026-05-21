"""Regression test for niche_loader.load_niche_config merging scoring_weights.yaml.

Four niches had dedup thresholds in scoring_weights.yaml that the pipeline
was never reading. The loader now merges them into niche_config under
both 'scoring_weights' and 'dedup' keys.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from genlab_core.niche_loader import load_niche_config


def _write_niche(tmp_path: Path, niche_yaml: dict, scoring_yaml: dict | None = None) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "niche.yaml").write_text(yaml.safe_dump(niche_yaml))
    if scoring_yaml is not None:
        (config_dir / "scoring_weights.yaml").write_text(yaml.safe_dump(scoring_yaml))
    return tmp_path


def test_merges_scoring_weights_under_key(tmp_path: Path) -> None:
    root = _write_niche(
        tmp_path,
        niche_yaml={"niche_id": "test"},
        scoring_yaml={"virality_scoring": {"weight": 0.3}},
    )
    cfg = load_niche_config("test", root)
    assert cfg["scoring_weights"]["virality_scoring"]["weight"] == 0.3


def test_promotes_dedup_block_to_top_level(tmp_path: Path) -> None:
    root = _write_niche(
        tmp_path,
        niche_yaml={"niche_id": "test"},
        scoring_yaml={
            "dedup": {"jaccard_threshold": 0.70, "tfidf_threshold": 0.65}
        },
    )
    cfg = load_niche_config("test", root)
    assert cfg["dedup"]["jaccard_threshold"] == 0.70
    assert cfg["dedup"]["tfidf_threshold"] == 0.65


def test_normalizes_similarity_threshold_to_jaccard(tmp_path: Path) -> None:
    """Legacy key `similarity_threshold` → `jaccard_threshold`."""
    root = _write_niche(
        tmp_path,
        niche_yaml={"niche_id": "test"},
        scoring_yaml={"dedup": {"similarity_threshold": 0.68}},
    )
    cfg = load_niche_config("test", root)
    assert cfg["dedup"]["jaccard_threshold"] == 0.68
    # Original key also preserved for backward compat
    assert cfg["dedup"]["similarity_threshold"] == 0.68


def test_niche_config_dedup_takes_precedence(tmp_path: Path) -> None:
    """If niche.yaml already has a dedup block, don't overwrite it."""
    root = _write_niche(
        tmp_path,
        niche_yaml={
            "niche_id": "test",
            "dedup": {"jaccard_threshold": 0.85},
        },
        scoring_yaml={"dedup": {"jaccard_threshold": 0.50}},
    )
    cfg = load_niche_config("test", root)
    assert cfg["dedup"]["jaccard_threshold"] == 0.85


def test_missing_scoring_weights_is_not_an_error(tmp_path: Path) -> None:
    root = _write_niche(
        tmp_path,
        niche_yaml={"niche_id": "test"},
    )
    cfg = load_niche_config("test", root)
    assert cfg["niche_id"] == "test"
    assert "scoring_weights" not in cfg


def test_invalid_yaml_in_scoring_weights_is_non_fatal(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "niche.yaml").write_text("niche_id: test")
    (config_dir / "scoring_weights.yaml").write_text("not: valid: yaml: {{[")
    # Should NOT raise
    cfg = load_niche_config("test", tmp_path)
    assert cfg["niche_id"] == "test"
