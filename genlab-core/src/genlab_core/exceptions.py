"""Shared exceptions for genlab-core."""


class NicheConfigError(Exception):
    """Raised when a niche's YAML configuration is missing required fields
    or contains invalid values that prevent the pipeline from running."""
