"""Structural pin — every niche's virality_scoring config must have
compilable patterns.

Guards against the class-of-bug that shipped 2026-07-21 evening:
using PCRE syntax like `\\p{IsHiragana}` in a Python re pattern
silently compiles as a literal string (thanks to `re.error` fallback
in `_compile_patterns`), producing a virality_score=0 no-op for that
pattern. Better to fail loudly at test time.

Also pins that the file layout matches the loader contract:
`scoring_weights.yaml → virality_scoring → patterns → {name: regex}`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from genlab_core.pipeline.stages.virality_scoring import DEFAULT_PATTERNS


_REPO_ROOT = Path(__file__).resolve().parents[3]

# All 5 niches' scoring_weights.yaml paths. Any new niche added must
# also have a virality_scoring section — extending this list catches
# the missing-config regression at PR-review time.
NICHE_CONFIGS: dict[str, Path] = {
    "ai_creators": _REPO_ROOT / "BlackboxBrief" / "config" / "scoring_weights.yaml",
    "gaming": _REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "scoring_weights.yaml",
    "sports": _REPO_ROOT / "ClutchWire" / "config" / "scoring_weights.yaml",
    "movies": _REPO_ROOT / "SpliceReel" / "config" / "scoring_weights.yaml",
    "anime": _REPO_ROOT / "FrameDrift" / "config" / "scoring_weights.yaml",
}


@pytest.mark.parametrize("niche_id", list(NICHE_CONFIGS.keys()))
def test_scoring_weights_yaml_loads(niche_id: str) -> None:
    """Baseline — every niche has a scoring_weights.yaml that parses."""
    path = NICHE_CONFIGS[niche_id]
    assert path.exists(), f"{niche_id}: missing {path}"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{niche_id}: config not a mapping"


@pytest.mark.parametrize("niche_id", [n for n in NICHE_CONFIGS if n != "ai_creators"])
def test_non_ai_niches_have_virality_scoring_section(niche_id: str) -> None:
    """The 4 non-AI niches MUST have their own virality_scoring section
    because the module's DEFAULT_PATTERNS are AI-industry vocabulary.
    Without an override, non-AI hooks score 0 → auto_approval_gate
    hard-rejects at the >=0.05 min. See virality_score fix 2026-07-21.
    """
    with open(NICHE_CONFIGS[niche_id], encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    assert "virality_scoring" in data, (
        f"{niche_id}: config missing 'virality_scoring' top-level key — "
        "falls back to AI-industry DEFAULT_PATTERNS which won't match "
        "this niche's vocabulary; all blueprints will score 0 and be "
        "gate-rejected on virality"
    )
    patterns = data["virality_scoring"].get("patterns", {})
    assert isinstance(patterns, dict) and patterns, (
        f"{niche_id}: virality_scoring.patterns must be a non-empty mapping"
    )


@pytest.mark.parametrize("niche_id", list(NICHE_CONFIGS.keys()))
def test_all_niche_patterns_compile_as_python_regex(niche_id: str) -> None:
    """Every regex in every niche's overrides must compile with Python's
    re module. Guards against PCRE-only syntax (`\\p{...}`, `\\g{...}`,
    named-lookbehinds) that Python's re silently rejects and the
    _compile_patterns fallback would swallow, defeating the override."""
    with open(NICHE_CONFIGS[niche_id], encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    virality = data.get("virality_scoring", {}) or {}
    patterns = virality.get("patterns", {}) or {}

    for pattern_name, pattern_regex in patterns.items():
        # Must be a valid override key (matches a DEFAULT_PATTERNS entry).
        # Unknown keys get logged as WARN + skipped by _compile_patterns —
        # that's silent regression territory, so pin at test time.
        assert pattern_name in DEFAULT_PATTERNS, (
            f"{niche_id}: unknown pattern override key {pattern_name!r}; "
            f"valid keys: {sorted(DEFAULT_PATTERNS.keys())}"
        )
        # Must compile as Python re syntax.
        try:
            re.compile(pattern_regex, re.I)
        except re.error as exc:
            pytest.fail(
                f"{niche_id}.virality_scoring.patterns.{pattern_name}: "
                f"regex fails to compile as Python re: {exc}\n"
                f"Pattern: {pattern_regex!r}"
            )
