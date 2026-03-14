"""Register Prefect deployments for all Gen Lab flows.

Run as a module to register/update deployments:
    python -m genlab_core.orchestration.deployment_manager

Each niche defines its flow entry point and schedule. This module
discovers them and calls flow.serve() or flow.deploy() as appropriate.
"""

from __future__ import annotations

import importlib
import logging
import os

logger = logging.getLogger(__name__)

# Registry of known Gen Lab flows.
# Each entry: (module_path, flow_function_name, deployment_configs)
_FLOW_REGISTRY: list[tuple[str, str, list[dict]]] = [
    (
        "niches.ai_creators.flows.ai_creators_flow",
        "ai_creators_pipeline",
        [
            {
                "name": "ai-creators-scheduled",
                "cron": "0 6 * * *",  # 6 AM UTC daily
                "tags": ["ai_creators", "scheduled"],
            },
            {
                "name": "ai-creators-manual",
                "tags": ["ai_creators", "manual"],
            },
        ],
    ),
    (
        "niches.gaming.flows.gaming_flow",
        "gaming_pipeline",
        [
            {
                "name": "gaming-scheduled",
                "cron": "0 8,20 * * *",  # 8 AM and 8 PM UTC
                "tags": ["gaming", "scheduled"],
            },
            {
                "name": "gaming-manual",
                "tags": ["gaming", "manual"],
            },
        ],
    ),
    (
        "niches.gaming.flows.spike_detector_flow",
        "steam_spike_detector",
        [
            {
                "name": "spike-detector",
                "interval": 300,  # 5 minutes
                "tags": ["gaming", "spike-detector"],
            },
        ],
    ),
]


def register_all() -> None:
    """Import each flow and create deployments."""
    from prefect.deployments.runner import EntrypointType

    work_pool = os.environ.get("PREFECT_WORK_POOL", "genlab-process-pool")

    for module_path, func_name, dep_configs in _FLOW_REGISTRY:
        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            logger.warning(
                "[Deploy] Cannot import %s: %s — skipping", module_path, e
            )
            continue

        flow_fn = getattr(mod, func_name, None)
        if flow_fn is None:
            logger.warning(
                "[Deploy] %s has no function '%s' — skipping",
                module_path, func_name,
            )
            continue

        for cfg in dep_configs:
            dep_name = cfg["name"]
            try:
                deploy_kwargs: dict = {
                    "name": dep_name,
                    "work_pool_name": work_pool,
                    "tags": cfg.get("tags", []),
                    "entrypoint_type": EntrypointType.MODULE_PATH,
                    "ignore_warnings": True,
                }
                if "cron" in cfg:
                    deploy_kwargs["cron"] = cfg["cron"]
                elif "interval" in cfg:
                    deploy_kwargs["interval"] = cfg["interval"]

                flow_fn.deploy(**deploy_kwargs)
                logger.info(
                    "[Deploy] Registered %s/%s (pool=%s)",
                    func_name, dep_name, work_pool,
                )
            except Exception as e:
                logger.error(
                    "[Deploy] Failed to register %s/%s: %s",
                    func_name, dep_name, e,
                )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    register_all()
