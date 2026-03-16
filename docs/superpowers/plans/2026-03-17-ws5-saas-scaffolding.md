# WS5: SaaS Scaffolding — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `create_niche.py` scaffold, `validate_configs.py` pre-flight checker, clean up residual `ai_news` hardcodes, and wire rate_limiter into CW/SR/FD.

**Architecture:** Two new CLI tools in genlab-core/tools/. Config validation via Pydantic. Niche ID cleanup via find-and-replace with alias backward compat.

**Tech Stack:** Python, Pydantic v2, argparse, YAML

**Spec:** `docs/superpowers/specs/2026-03-17-ws5-saas-scaffolding-design.md`

---

## Chunk 1: create_niche.py + validate_configs.py

### Task 1: Create niche scaffold tool

**Files:**
- Create: `genlab-core/src/genlab_core/tools/create_niche.py`
- Test: `genlab-core/tests/tools/test_create_niche.py`

- [ ] **Step 1: Write failing test**

```python
# genlab-core/tests/tools/test_create_niche.py
"""Tests for the niche scaffold tool."""
import tempfile
from pathlib import Path
from genlab_core.tools.create_niche import scaffold_niche


class TestCreateNiche:
    def test_creates_directory_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "TestNiche"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            assert (out / "config" / "niche.yaml").exists()
            assert (out / "config" / "sources.yaml").exists()
            assert (out / "config" / "visuals.yaml").exists()
            assert (out / "config" / "publishing.yaml").exists()
            assert (out / "config" / "schedule.yaml").exists()
            assert (out / "config" / "scoring_weights.yaml").exists()
            assert (out / "strategies").is_dir()

    def test_replaces_placeholders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "FitPulse"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            niche_yaml = (out / "config" / "niche.yaml").read_text()
            assert "fitness" in niche_yaml
            assert "FitPulse" in niche_yaml
            assert "{NICHE_ID}" not in niche_yaml

    def test_strategy_classes_renamed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "FitPulse"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            hooks = (out / "strategies" / "hooks.py").read_text()
            assert "FitnessHookStrategy" in hooks or "fitness" in hooks.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/tools/test_create_niche.py -v --tb=short
```

- [ ] **Step 3: Implement scaffold_niche**

```python
# genlab-core/src/genlab_core/tools/create_niche.py
"""CLI tool to scaffold a new niche from the _template directory."""
from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parents[4] / "CriticalRush" / "niches" / "_template"


def scaffold_niche(
    niche_id: str,
    brand_name: str,
    accent_color: str,
    output_dir: Path,
) -> None:
    """Copy template and replace placeholders."""
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")

    if not _TEMPLATE_DIR.exists():
        raise FileNotFoundError(f"Template not found: {_TEMPLATE_DIR}")

    shutil.copytree(_TEMPLATE_DIR, output_dir)

    # String replacements
    replacements = {
        "{NICHE_ID}": niche_id,
        "{BRAND_NAME}": brand_name,
        "{ACCENT_COLOR}": accent_color,
        "TemplateContentResearch": f"{brand_name.replace(' ', '')}ContentResearch",
        "TemplateScoring": f"{brand_name.replace(' ', '')}Scoring",
        "TemplateWriting": f"{brand_name.replace(' ', '')}Writing",
        "TemplateHook": f"{brand_name.replace(' ', '')}Hook",
        "TemplateVisualRender": f"{brand_name.replace(' ', '')}VisualRender",
        "TemplatePlatformAdaptation": f"{brand_name.replace(' ', '')}PlatformAdaptation",
    }

    for f in output_dir.rglob("*"):
        if not f.is_file():
            continue
        try:
            content = f.read_text()
        except UnicodeDecodeError:
            continue

        original = content
        for old, new in replacements.items():
            content = content.replace(old, new)
        # Also replace lowercase variants
        content = content.replace("template_niche", niche_id)

        if content != original:
            f.write_text(content)

    logger.info("Scaffolded niche '%s' at %s", niche_id, output_dir)
    print(f"\nNiche '{niche_id}' scaffolded at {output_dir}")
    print("\nNext steps:")
    print(f"  1. Add to pipeline_runner.NICHE_ROOTS: '{niche_id}': '{output_dir}'")
    print(f"  2. Add to dashboard/configs/niches_registry.yaml")
    print(f"  3. Add platform credentials to .env ({brand_name.upper()}_* vars)")
    print(f"  4. Run: uv run python -m genlab_core.tools.validate_configs --niche-dir {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Scaffold a new GenLab niche")
    parser.add_argument("--niche-id", required=True, help="e.g. fitness")
    parser.add_argument("--brand-name", required=True, help="e.g. FitPulse")
    parser.add_argument("--accent-color", required=True, help="e.g. #00FF88")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    scaffold_niche(args.niche_id, args.brand_name, args.accent_color, args.output_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/tools/test_create_niche.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/tools/create_niche.py genlab-core/tests/tools/test_create_niche.py
git commit -m "feat(saas): add create_niche.py scaffold tool

Copies _template, replaces placeholders, renames strategy classes.
Usage: uv run python -m genlab_core.tools.create_niche --niche-id X --brand-name Y --accent-color Z --output-dir /path"
```

### Task 2: Create config validator

**Files:**
- Create: `genlab-core/src/genlab_core/tools/validate_configs.py`
- Test: `genlab-core/tests/tools/test_validate_configs.py`

- [ ] **Step 1: Write failing test**

```python
# genlab-core/tests/tools/test_validate_configs.py
"""Tests for niche config validation."""
import tempfile
from pathlib import Path
from genlab_core.tools.validate_configs import validate_niche_dir, ValidationResult


class TestValidateConfigs:
    def test_valid_config_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "config").mkdir()
            (d / "config" / "niche.yaml").write_text(
                "niche_id: test\nbrand_name: Test\naccent_color: '#FF0000'\n"
                "video_gate: require\nfallback_to_text_render: false\n"
            )
            result = validate_niche_dir(d)
            assert result.niche_yaml_valid

    def test_missing_niche_yaml_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "config").mkdir()
            result = validate_niche_dir(d)
            assert not result.niche_yaml_valid

    def test_bad_accent_color_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "config").mkdir()
            (d / "config" / "niche.yaml").write_text(
                "niche_id: test\nbrand_name: Test\naccent_color: red\n"
                "video_gate: require\nfallback_to_text_render: false\n"
            )
            result = validate_niche_dir(d)
            assert len(result.errors) > 0
```

- [ ] **Step 2: Implement validate_configs.py**

```python
# genlab-core/src/genlab_core/tools/validate_configs.py
"""Pre-flight config validator for niche directories."""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class NicheYamlSchema(BaseModel):
    niche_id: str
    brand_name: str
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    video_gate: str = "require"
    fallback_to_text_render: bool = False

    @field_validator("fallback_to_text_render")
    @classmethod
    def must_be_false(cls, v):
        if v:
            raise ValueError("fallback_to_text_render must be false (video-only platform)")
        return v


@dataclass
class ValidationResult:
    niche_yaml_valid: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def validate_niche_dir(niche_dir: Path) -> ValidationResult:
    result = ValidationResult()
    config_dir = niche_dir / "config"

    # Check niche.yaml
    niche_path = config_dir / "niche.yaml"
    if not niche_path.exists():
        result.errors.append(f"Missing: {niche_path}")
        return result

    try:
        data = yaml.safe_load(niche_path.read_text()) or {}
        NicheYamlSchema(**data)
        result.niche_yaml_valid = True
    except Exception as e:
        result.errors.append(f"niche.yaml: {e}")

    # Check other expected files
    for name in ["sources.yaml", "publishing.yaml", "schedule.yaml",
                  "scoring_weights.yaml", "visuals.yaml"]:
        p = config_dir / name
        if not p.exists():
            result.warnings.append(f"Missing optional config: {name}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Validate niche config")
    parser.add_argument("--niche-dir", type=Path, required=True)
    args = parser.parse_args()
    result = validate_niche_dir(args.niche_dir)
    if result.ok:
        print(f"VALID: {args.niche_dir}")
    else:
        print(f"INVALID: {args.niche_dir}")
        for e in result.errors:
            print(f"  ERROR: {e}")
    for w in result.warnings:
        print(f"  WARN: {w}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run tests and commit**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/tools/test_validate_configs.py -v --tb=short
git add genlab-core/src/genlab_core/tools/validate_configs.py genlab-core/tests/tools/test_validate_configs.py
git commit -m "feat(saas): add validate_configs.py pre-flight config checker

Pydantic validation for niche.yaml (niche_id, brand_name, accent_color,
video_gate=require, fallback_to_text_render=false). Reports errors/warnings."
```

---

## Chunk 2: ai_news Cleanup + Rate Limiter Wiring

### Task 3: Clean up residual ai_news hardcodes

**Files:**
- Modify: `genlab-core/src/genlab_core/media/video_sourcer.py:26`
- Modify: `genlab-core/src/genlab_core/media/trending_video_fetcher.py:79,114,130`
- Modify: `genlab-core/src/genlab_core/media/download_top_videos.py:33`
- Modify: `genlab-core/src/genlab_core/intel/google_trends.py:28,37`
- Modify: `genlab-core/src/genlab_core/engagement/comment_processor.py:101-102`
- Modify: `genlab-core/src/genlab_core/scoring/composite_scorer.py:41`

- [ ] **Step 1: Replace ai_news with ai_creators in all 7 locations**

For each file, change `"ai_news"` key to `"ai_creators"`. Where it's a dict key in a lookup map, add alias:

```python
# At the top of any file with niche-keyed dicts, add a normalizer:
def _normalize_niche(niche_id: str) -> str:
    return {"ai_news": "ai_creators", "ai_tech": "ai_creators"}.get(niche_id, niche_id)
```

Or simpler — just duplicate the key:
```python
YOUTUBE_CATEGORIES: Dict[str, str] = {
    "gaming": "20",
    "sports": "17",
    "movies": "1",
    "entertainment": "24",
    "ai_creators": "28",
    "ai_news": "28",  # backward compat alias
}
```

- [ ] **Step 2: Verify no remaining ai_news outside aliases**

```bash
grep -rn "'ai_news'" genlab-core/src/ --include="*.py" | grep -v "alias\|compat\|test_\|__pycache__\|normalize\|ai_news.*ai_creators"
```

Expected: 0 results (only alias/compat lines remain)

- [ ] **Step 3: Run all genlab-core tests**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/ -x -q --tb=short 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add genlab-core/src/
git commit -m "refactor: replace ai_news with ai_creators in 7 genlab-core files

Canonical niche_id is ai_creators. ai_news kept as backward-compat alias
in lookup dicts so existing SharePoint data still works."
```

### Task 4: Wire rate_limiter into CW, SR, FD

**Files:**
- Modify: `ClutchWire/cw_strategies/content_research.py:76`
- Modify: `SpliceReel/sr_strategies/content_research.py:75`
- Modify: `FrameDrift/fd_strategies/content_research.py:74`

- [ ] **Step 1: Add TokenBucket to each content research strategy**

In each file, add at module level:

```python
from genlab_core.ratelimit.token_bucket import TokenBucket

_api_limiter = TokenBucket(rate=5.0, capacity=10)
```

Then in `execute()`, before external API calls:

```python
def execute(self, context):
    _api_limiter.acquire()
    # ... existing code
```

- [ ] **Step 2: Run niche tests**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest ClutchWire/tests/ SpliceReel/tests/ FrameDrift/tests/ -x -q --tb=short 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add ClutchWire/cw_strategies/content_research.py SpliceReel/sr_strategies/content_research.py FrameDrift/fd_strategies/content_research.py
git commit -m "feat(ratelimit): wire TokenBucket into CW, SR, FD content research

All 5 niches now have API rate limiting (5 req/s, burst 10).
Previously only CR had niche-level rate limiting (B17 fix)."
```
