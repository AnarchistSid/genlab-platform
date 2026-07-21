"""Pin — publisher's inter-niche jitter (item C of the 2026-07-22 Meta
anti-fingerprint pack).

Full main() has too many dependencies to mock cleanly for a focused
jitter test; instead we pin the jitter wire at the source level
(structural guard against accidental removal) + validate the env-var
override contract.
"""

from __future__ import annotations

import inspect

from genlab_core.publishing import publish_all_platforms as pap


class TestJitterSourceWiring:
    """Structural pins — the jitter block must remain in the publisher
    main() loop. Guards against a future refactor that inadvertently
    strips the anti-fingerprint delay (regression to per-niche
    minute-slot clustering)."""

    def test_random_module_imported(self) -> None:
        """`random` must be imported at module level."""
        assert hasattr(pap, "random"), (
            "publish_all_platforms.py must import `random` for inter-niche jitter"
        )

    def test_time_module_imported(self) -> None:
        """`time` must be imported for the sleep call."""
        assert hasattr(pap, "time"), (
            "publish_all_platforms.py must import `time` for the sleep()"
        )

    def test_main_contains_jitter_env_lookup(self) -> None:
        """main() body must read GENLAB_PUBLISH_INTER_NICHE_JITTER_MIN/MAX
        env vars — that's the operator's escape hatch."""
        src = inspect.getsource(pap.main)
        assert "GENLAB_PUBLISH_INTER_NICHE_JITTER_MIN" in src
        assert "GENLAB_PUBLISH_INTER_NICHE_JITTER_MAX" in src

    def test_main_contains_uniform_call(self) -> None:
        """main() must call random.uniform() to compute the jitter
        delay — pin the actual randomness (uniform, not randint or
        constant sleep)."""
        src = inspect.getsource(pap.main)
        assert "random.uniform" in src

    def test_main_contains_time_sleep(self) -> None:
        """main() must call time.sleep() with the jitter delay."""
        src = inspect.getsource(pap.main)
        assert "time.sleep" in src

    def test_main_skips_first_niche(self) -> None:
        """First niche must NOT sleep — pinned via source presence of
        the `first_niche` flag pattern."""
        src = inspect.getsource(pap.main)
        assert "first_niche" in src, (
            "Missing first-niche-skip guard — every niche would sleep, "
            "including the first (unnecessary latency + no fingerprint gain)"
        )


class TestJitterEnvOverrideValues:
    """Contract: env var defaults are documented as 30s/180s.
    Regression guard against 'someone raised to 3600s and left it'."""

    def test_default_min_is_reasonable(self) -> None:
        """The default GENLAB_PUBLISH_INTER_NICHE_JITTER_MIN in source
        should be small enough not to add multi-minute publisher
        latency, big enough to spread niches across seconds."""
        src = inspect.getsource(pap.main)
        # Documented default = 30 seconds
        assert '"30"' in src or "'30'" in src, (
            "Default JITTER_MIN should be 30 seconds"
        )

    def test_default_max_is_reasonable(self) -> None:
        """Max default = 180 seconds (3 min max delay per niche)."""
        src = inspect.getsource(pap.main)
        assert '"180"' in src or "'180'" in src, (
            "Default JITTER_MAX should be 180 seconds"
        )
