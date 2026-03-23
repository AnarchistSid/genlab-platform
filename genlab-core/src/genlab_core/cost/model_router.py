"""Config-driven LiteLLM model router.

Reads configs/model_routing.yaml once per process (lru_cache). Pipeline stages
call get_model(task_type) to get the correct model string.

    from genlab_core.cost.model_router import get_model
    model = get_model("generate_hooks")       # -> "claude-sonnet-4-6"
    model = get_model("unknown_task")          # -> default_model
    model = get_model("generate_hooks", budget_ratio=0.28)  # budget override
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "model_routing.yaml"


@lru_cache(maxsize=1)
def _load_routing_config(config_path: str) -> dict[str, Any]:
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("[model_router] Config not found at %s — using defaults", config_path)
        return {}
    except Exception as e:
        log.error("[model_router] Failed to load config: %s — using defaults", e)
        return {}


def _get_config() -> dict[str, Any]:
    path = os.environ.get("MODEL_ROUTING_CONFIG", str(_DEFAULT_CONFIG_PATH))
    return _load_routing_config(path)


def get_model(task_type: str, budget_ratio: float = 0.0) -> str:
    """Return the model string for a pipeline task type. Never raises."""
    cfg = _get_config()
    default = cfg.get("default_model", "claude-haiku-4-5-20251001")

    if budget_ratio >= 0.25:
        return cfg.get("budget_fallbacks", {}).get("mid_to_cheapest", default)
    elif budget_ratio >= 0.10:
        return cfg.get("budget_fallbacks", {}).get("expensive_to_mid", default)

    return cfg.get("task_routing", {}).get(task_type, default)
