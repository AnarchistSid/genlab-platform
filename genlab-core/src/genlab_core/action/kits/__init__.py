"""Sport-family kits for the ACTION template.

The template is two layers:

  SKELETON (sport-agnostic, one implementation)
      classify -> SAM2 matte from a derived colour seed -> music -> beat grid
      -> per-shot punch-in from the subject's own size -> effects on the beat
      -> one drawing flash -> reference-measured gates

  KIT (per sport family)
      which effects, what colour, what they attach to

A kit is YAML plus one small effect module. Nothing in the skeleton branches on
sport; it reads the kit.
"""

from .registry import KIT_BY_FAMILY, KitSpec, family_for_niche, load_kit

__all__ = ["KIT_BY_FAMILY", "KitSpec", "load_kit", "family_for_niche"]
