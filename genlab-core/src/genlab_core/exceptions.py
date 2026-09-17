"""Shared exceptions for genlab-core."""


class NicheConfigError(Exception):
    """Raised when a niche's YAML configuration is missing required fields
    or contains invalid values that prevent the pipeline from running."""


class ConfigError(Exception):
    """A required configuration value is absent.

    Distinct from a connection failure: nothing to retry, nothing to wait for.
    A pipeline started without a DSN cannot succeed later, so it should die in a
    second and say why rather than block on a connection that cannot exist.
    """
