"""CLI tool to scaffold a new niche from the _template directory.

Usage:
    uv run python -m genlab_core.tools.create_niche \
        --niche-id fitness \
        --brand-name "FitPulse" \
        --accent-color "#00FF88" \
        --output-dir /path/to/FitPulse
"""
from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parents[4] / "CriticalRush" / "niches" / "_template"

# Placeholder tokens used in the template files (none currently exist in
# the _template, but we handle them for forward compatibility) plus
# concrete niche-specific strings that need replacement.
_NICHE_SPECIFIC_STRINGS = {
    # niche.yaml, visuals.yaml, publishing.yaml, schedule.yaml
    "anime": "{NICHE_ID}",
    "FrameDrift": "{BRAND_NAME}",
    "#7B3FE4": "{ACCENT_COLOR}",
}

# Strategy class prefixes to rename
_STRATEGY_CLASS_PREFIXES = [
    "TemplateContentResearch",
    "TemplateScoring",
    "TemplateWriting",
    "TemplateHook",
    "TemplateVisualRender",
    "TemplatePlatformAdaptation",
]


def scaffold_niche(
    niche_id: str,
    brand_name: str,
    accent_color: str,
    output_dir: Path,
) -> None:
    """Copy template and replace placeholders.

    Args:
        niche_id: Lowercase niche identifier (e.g. "fitness").
        brand_name: Human-readable channel name (e.g. "FitPulse").
        accent_color: Hex color (e.g. "#00FF88").
        output_dir: Target directory for the new niche scaffold.

    Raises:
        FileExistsError: If output_dir already exists.
        FileNotFoundError: If _template directory is missing.
    """
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")

    if not _TEMPLATE_DIR.exists():
        raise FileNotFoundError(f"Template not found: {_TEMPLATE_DIR}")

    shutil.copytree(_TEMPLATE_DIR, output_dir)

    # Create assets directory
    (output_dir / "assets").mkdir(exist_ok=True)

    # Replacement map: template-specific content → new niche content
    brand_clean = brand_name.replace(" ", "")
    replacements = {
        # Placeholder tokens (forward compat)
        "{NICHE_ID}": niche_id,
        "{BRAND_NAME}": brand_name,
        "{ACCENT_COLOR}": accent_color,
        # Concrete FrameDrift strings from the template
        'niche_id: "anime"': f'niche_id: "{niche_id}"',
        "niche_id: anime": f"niche_id: {niche_id}",
        'NICHE_ID = "anime"': f'NICHE_ID = "{niche_id}"',
        "#7B3FE4": accent_color,
        "FrameDrift": brand_name,
        "@framedrift": f"@{brand_clean.lower()}",
        "@theframedrift": f"@{brand_clean.lower()}",
        # Strategy class renames
        "TemplateContentResearch": f"{brand_clean}ContentResearch",
        "TemplateScoring": f"{brand_clean}Scoring",
        "TemplateWriting": f"{brand_clean}Writing",
        "TemplateHook": f"{brand_clean}Hook",
        "TemplateVisualRender": f"{brand_clean}VisualRender",
        "TemplatePlatformAdaptation": f"{brand_clean}PlatformAdaptation",
        # Pipeline stage class names (Anime → Brand)
        "AnimeContentResearchStrategy": f"{brand_clean}ContentResearchStrategy",
        "AnimeScoringStrategy": f"{brand_clean}ScoringStrategy",
        "AnimeWritingStrategy": f"{brand_clean}WritingStrategy",
        "AnimeHookStrategy": f"{brand_clean}HookStrategy",
        "AnimeVisualRenderStrategy": f"{brand_clean}VisualRenderStrategy",
        "AnimePlatformAdaptationStrategy": f"{brand_clean}PlatformAdaptationStrategy",
        # Module references in niche.yaml
        "fd_strategies": f"{niche_id}_strategies",
        # Also replace lowercase niche reference
        "template_niche": niche_id,
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

        if content != original:
            f.write_text(content)

    logger.info("Scaffolded niche '%s' at %s", niche_id, output_dir)
    print(f"\nNiche '{niche_id}' scaffolded at {output_dir}")
    print("\nNext steps:")
    print(f"  1. Add to pipeline_runner.NICHE_ROOTS: '{niche_id}': '{output_dir}'")
    print(f"  2. Add to dashboard/configs/niches_registry.yaml")
    print(f"  3. Add platform credentials to .env ({brand_clean.upper()}_* vars)")
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
