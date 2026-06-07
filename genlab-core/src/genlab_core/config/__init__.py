"""Loaders for platform-wide config files.

Currently exposes :func:`get_tuning_config` for the tunable constants —
see :mod:`genlab_core.config.tuning` for the schema and migration plan.
"""

from .tuning import TuningConfig, get_tuning_config

__all__ = ["TuningConfig", "get_tuning_config"]
