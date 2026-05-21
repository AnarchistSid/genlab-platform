"""Pre-flight config validator for niche directories.

Validates niche.yaml against a Pydantic schema and checks for the
presence of optional config files. Reports errors and warnings.

Usage:
    uv run python -m genlab_core.tools.validate_configs --niche-dir /path/to/FitPulse
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class NicheYamlSchema(BaseModel):
    """Pydantic schema for the required fields in niche.yaml."""

    niche_id: str
    brand_name: str
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    video_gate: str = "require"
    fallback_to_text_render: bool = False

    @field_validator("fallback_to_text_render")
    @classmethod
    def must_be_false(cls, v: bool) -> bool:
        if v:
            raise ValueError("fallback_to_text_render must be false (video-only platform)")
        return v


@dataclass
class ValidationResult:
    """Outcome of validating a niche directory."""

    niche_yaml_valid: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


# Optional config files that a well-configured niche should have.
_OPTIONAL_CONFIGS = [
    "sources.yaml",
    "publishing.yaml",
    "schedule.yaml",
    "scoring_weights.yaml",
    "visuals.yaml",
]


def validate_niche_dir(niche_dir: Path) -> ValidationResult:
    """Validate a niche directory's config files.

    Args:
        niche_dir: Root directory of the niche (must contain config/).

    Returns:
        ValidationResult with errors, warnings, and pass/fail flags.
    """
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
    for name in _OPTIONAL_CONFIGS:
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
