"""Pin the 2026-06-21 A2 fix — per-niche token health validation.

Pre-fix, ``monitoring/token_health.py:_run_native_platform_checks``
called ``get_client(pid)`` with no ``niche_id`` kwarg → platform
client constructors fell back to the GLOBAL env vars (``META_ACCESS_TOKEN``
/ ``YOUTUBE_REFRESH_TOKEN`` / etc). Production has per-niche prefixed
credentials (``CLUTCHWIRE_META_ACCESS_TOKEN``, ``CRITICALRUSH_META_ACCESS_TOKEN``,
``SPLICEREEL_*``, ``FRAMEDRIFT_*``, ``BLACKBOXBRIEF_*``), but health
check only validated the GLOBAL token.

**Production impact** (the silent failure mode this fix closes):
if SpliceReel's IG token expires, the health check still reports
``instagram: healthy`` because BB's global token IS valid. The
expired SpliceReel token would only surface as a publish failure
days later when the operator notices SR posts stopped landing.

**Fix**: ``_run_native_platform_checks`` now iterates ``(niche × platform)``
by default. Each per-niche check passes ``niche_id=`` to the platform
client constructor — which resolves the appropriate prefixed
credential via ``resolve_meta_credentials`` / ``resolve_youtube_credentials``
/ etc. Result list grows from ~5 entries to ~25 entries, each
labelled with ``niche_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch


@dataclass
class _FakeTokenStatus:
    """Stand-in for ``TokenStatus`` returned by platform health-check.

    Field set matches what ``_token_status_to_dict()`` reads:
    ``valid``, ``platform``, ``expires_at``, ``message``,
    ``needs_refresh``, ``details``.
    """

    valid: bool = True
    platform: str = "instagram"
    expires_at: int | None = None
    message: str = "Token valid"
    needs_refresh: bool = False
    details: dict | None = None


class TestPerNicheIteration:
    """Pin that ``_run_native_platform_checks`` iterates over all 5 niches
    × all HealthCheckable platforms, with niche-aware labelling.
    """

    def test_default_runs_5_niches_x_platforms(self):
        """With default ``_DEFAULT_NICHE_IDS``, each platform gets
        ``len(niches) == 5`` distinct check invocations."""
        from genlab_core.monitoring import token_health

        # Mock a single HealthCheckable platform to keep the test fast
        # and isolated from real network calls. Use side_effect to
        # capture the kwargs each get_client invocation was called with.
        call_log = []

        class FakeHealthCheckable:
            """Stand-in client whose check_token_health always returns valid."""

            def __init__(self, niche_id=""):
                self.niche_id = niche_id

            def check_token_health(self):
                return _FakeTokenStatus(platform="fake_platform")

        def fake_get_client(pid, **kwargs):
            call_log.append((pid, kwargs))
            return FakeHealthCheckable(**kwargs)

        with (
            patch("genlab_core.platforms.get_client", side_effect=fake_get_client),
            patch("genlab_core.platforms.list_platforms", return_value=["fake_platform"]),
            patch(
                "genlab_core.platforms.protocols.HealthCheckable",
                FakeHealthCheckable,
            ),
        ):
            results = token_health._run_native_platform_checks()

        # 5 niches × 1 platform = 5 results
        assert len(results) == 5, (
            f"Expected 5 (niches=5) × 1 (platforms=1) = 5 results; got {len(results)}. "
            f"Per-niche iteration is the headline fix — without it, only the global "
            f"check fires and per-niche token expiry stays invisible."
        )
        # Each call passed niche_id from _DEFAULT_NICHE_IDS
        called_niches = [kw.get("niche_id") for _, kw in call_log]
        assert sorted(called_niches) == sorted(token_health._DEFAULT_NICHE_IDS), (
            f"All 5 default niches must be checked: expected "
            f"{sorted(token_health._DEFAULT_NICHE_IDS)}, got {sorted(called_niches)}"
        )

    def test_per_niche_results_carry_niche_id_label(self):
        """Each per-niche result has ``niche_id`` field set + the
        ``platform`` field includes the niche suffix (e.g.,
        ``instagram/gaming``) so the dashboard can attribute correctly.
        """
        from genlab_core.monitoring import token_health

        class FakeHealthCheckable:
            def __init__(self, niche_id=""):
                self.niche_id = niche_id

            def check_token_health(self):
                return _FakeTokenStatus(platform="instagram")

        with (
            patch(
                "genlab_core.platforms.get_client",
                side_effect=lambda pid, **k: FakeHealthCheckable(**k),
            ),
            patch("genlab_core.platforms.list_platforms", return_value=["instagram"]),
            patch("genlab_core.platforms.protocols.HealthCheckable", FakeHealthCheckable),
        ):
            results = token_health._run_native_platform_checks(niche_ids=("gaming", "sports"))

        assert len(results) == 2
        # Each result has niche_id + platform suffix
        niches = {r["niche_id"] for r in results}
        platforms = {r["platform"] for r in results}
        assert niches == {"gaming", "sports"}
        assert platforms == {"instagram/gaming", "instagram/sports"}, (
            "Platform labels must include the niche suffix so operators "
            "eyeballing logs see 'instagram/gaming' not just 'instagram' "
            "appearing 5 times indistinguishably"
        )

    def test_empty_niches_falls_back_to_global_only(self):
        """``niche_ids=()`` is the legacy escape hatch — runs a single
        global pass with no ``niche_id`` kwarg. Pin backward compat for
        any callers that don't want per-niche iteration (e.g., a smoke
        test that doesn't care which credential set is checked)."""
        from genlab_core.monitoring import token_health

        call_log = []

        class FakeHealthCheckable:
            def __init__(self, niche_id=""):
                self.niche_id = niche_id

            def check_token_health(self):
                return _FakeTokenStatus(platform="anywhere")

        def fake_get_client(pid, **kwargs):
            call_log.append((pid, kwargs))
            return FakeHealthCheckable(**kwargs)

        with (
            patch("genlab_core.platforms.get_client", side_effect=fake_get_client),
            patch("genlab_core.platforms.list_platforms", return_value=["anywhere"]),
            patch("genlab_core.platforms.protocols.HealthCheckable", FakeHealthCheckable),
        ):
            results = token_health._run_native_platform_checks(niche_ids=())

        # Legacy single-pass: 1 result, no niche_id kwarg passed
        assert len(results) == 1
        assert call_log == [("anywhere", {})], (
            "Legacy single-pass MUST call get_client(pid) with NO kwargs — "
            "preserves the pre-fix call shape exactly. Otherwise existing "
            "tests/callers that don't pass niche_id might silently break."
        )
        # Result should NOT have niche_id label
        assert "niche_id" not in results[0]
        # Platform name should NOT have niche suffix
        assert results[0]["platform"] == "anywhere"

    def test_niche_id_unaware_client_falls_back_to_global_check(self):
        """2026-06-21 hotfix regression: not every platform client accepts
        the ``niche_id`` kwarg yet (TikTokClient is the headline case —
        placeholder ``__init__(self)`` that takes no args). Without
        graceful fallback, every per-niche iteration for those clients
        TypeError-ed and the WHOLE run crashed (KeyError on the summary
        keys because the exception propagated).

        Fix: if ``get_client(pid, niche_id=...)`` raises ``TypeError``
        with ``niche_id`` in the message, fall back to ``get_client(pid)``
        without kwargs (legacy global check) and log a warning once per
        platform naming the gap.

        Pin both invariants:
          * The fallback runs successfully (no exception propagates)
          * The warning surfaces the platform name so operators can
            extend the constructor later
        """

        from genlab_core.monitoring import token_health

        # Platform A: accepts niche_id (modern client)
        # Platform B: does NOT accept niche_id (legacy placeholder like TikTok)
        class ModernClient:
            def __init__(self, niche_id=""):
                self.niche_id = niche_id

            def check_token_health(self):
                return _FakeTokenStatus(platform="modern")

        class LegacyClient:
            def __init__(self):  # NO kwargs — like TikTokClient
                pass

            def check_token_health(self):
                return _FakeTokenStatus(platform="legacy")

        def fake_get_client(pid, **kwargs):
            if pid == "modern":
                return ModernClient(**kwargs)
            if pid == "legacy":
                if "niche_id" in kwargs:
                    raise TypeError(
                        "LegacyClient.__init__() got an unexpected keyword argument 'niche_id'"
                    )
                return LegacyClient()
            raise ValueError(pid)

        # Patch HealthCheckable to match both fake classes so isinstance works
        class FakeHealthCheckable:
            pass

        with (
            patch("genlab_core.platforms.get_client", side_effect=fake_get_client),
            patch("genlab_core.platforms.list_platforms", return_value=["modern", "legacy"]),
            # Make both client classes register as HealthCheckable
            patch(
                "genlab_core.platforms.protocols.HealthCheckable",
                new=type(
                    "FakeHCProtocol",
                    (object,),
                    {"__instancecheck__": lambda self, inst: True},
                )(),
            ),
        ):
            # caplog captures the once-per-platform warning
            results = token_health._run_native_platform_checks(niche_ids=("gaming", "sports"))

        # Both platforms × both niches = 4 results (NO crash)
        assert len(results) == 4, (
            f"Expected 4 results (2 platforms × 2 niches); got {len(results)}. "
            f"If <4, the TypeError propagated and broke the run."
        )
        # Modern client results are properly per-niche labelled
        modern_results = [r for r in results if r["platform"].startswith("modern")]
        assert len(modern_results) == 2
        for r in modern_results:
            assert r["niche_id"] in ("gaming", "sports")
        # Legacy client results — fallback hit, still produced rows
        legacy_results = [r for r in results if r["platform"].startswith("legacy")]
        assert len(legacy_results) == 2
        # Legacy still gets the niche_id label (so dashboard can attribute)
        # even though the check itself was global. This is a deliberate
        # design tradeoff: operator-visible attribution stays consistent
        # across the result list; the warning log carries the "this was
        # actually a global check" caveat.
        for r in legacy_results:
            assert r["niche_id"] in ("gaming", "sports")

    def test_per_niche_check_failure_isolated_per_niche(self):
        """When ONE niche's check raises, the OTHER niches' checks
        still complete + appear in the result list. Pin failure
        isolation — a broken FrameDrift token shouldn't prevent
        validation of the other 4 niches."""
        from genlab_core.monitoring import token_health

        class FakeHealthCheckable:
            def __init__(self, niche_id=""):
                self.niche_id = niche_id

            def check_token_health(self):
                if self.niche_id == "anime":
                    raise RuntimeError("anime credentials expired")
                return _FakeTokenStatus(platform="instagram")

        with (
            patch(
                "genlab_core.platforms.get_client",
                side_effect=lambda pid, **k: FakeHealthCheckable(**k),
            ),
            patch("genlab_core.platforms.list_platforms", return_value=["instagram"]),
            patch("genlab_core.platforms.protocols.HealthCheckable", FakeHealthCheckable),
        ):
            results = token_health._run_native_platform_checks()

        # All 5 niches still produce a result row
        assert len(results) == 5
        # The anime row is an error, the rest are healthy
        anime_results = [r for r in results if r.get("niche_id") == "anime"]
        assert len(anime_results) == 1
        assert anime_results[0]["status"] == "error"
        assert "anime credentials expired" in anime_results[0]["message"]
        # Other 4 are NOT marked error
        for r in results:
            if r.get("niche_id") != "anime":
                assert r["status"] != "error"


class TestRunAllChecksForwardsNiches:
    """``run_all_checks`` forwards the ``niche_ids`` arg to
    ``_run_native_platform_checks``. Pin the wiring."""

    def test_default_passes_default_niches(self):
        """No-arg call uses the default 5-niche tuple."""
        from genlab_core.monitoring import token_health

        with (
            patch(
                "genlab_core.monitoring.token_health.check_anthropic",
                return_value={"platform": "anthropic", "status": "healthy", "message": "ok"},
            ),
            patch(
                "genlab_core.monitoring.token_health.check_openai",
                return_value={"platform": "openai", "status": "healthy", "message": "ok"},
            ),
            patch(
                "genlab_core.monitoring.token_health.check_backlog",
                return_value={"platform": "database", "status": "healthy", "message": "ok"},
            ),
            patch("genlab_core.monitoring.token_health._run_native_platform_checks") as mock_native,
        ):
            mock_native.return_value = []
            token_health.run_all_checks()

        mock_native.assert_called_once_with(niche_ids=token_health._DEFAULT_NICHE_IDS)

    def test_explicit_niches_overrides_default(self):
        """Caller can pass a subset / override list."""
        from genlab_core.monitoring import token_health

        with (
            patch(
                "genlab_core.monitoring.token_health.check_anthropic",
                return_value={"platform": "anthropic", "status": "healthy", "message": "ok"},
            ),
            patch(
                "genlab_core.monitoring.token_health.check_openai",
                return_value={"platform": "openai", "status": "healthy", "message": "ok"},
            ),
            patch(
                "genlab_core.monitoring.token_health.check_backlog",
                return_value={"platform": "database", "status": "healthy", "message": "ok"},
            ),
            patch("genlab_core.monitoring.token_health._run_native_platform_checks") as mock_native,
        ):
            mock_native.return_value = []
            token_health.run_all_checks(niche_ids=("gaming",))

        mock_native.assert_called_once_with(niche_ids=("gaming",))

    def test_empty_niches_passed_through(self):
        """Empty tuple is the legacy escape hatch — forwarded as-is."""
        from genlab_core.monitoring import token_health

        with (
            patch(
                "genlab_core.monitoring.token_health.check_anthropic",
                return_value={"platform": "anthropic", "status": "healthy", "message": "ok"},
            ),
            patch(
                "genlab_core.monitoring.token_health.check_openai",
                return_value={"platform": "openai", "status": "healthy", "message": "ok"},
            ),
            patch(
                "genlab_core.monitoring.token_health.check_backlog",
                return_value={"platform": "database", "status": "healthy", "message": "ok"},
            ),
            patch("genlab_core.monitoring.token_health._run_native_platform_checks") as mock_native,
        ):
            mock_native.return_value = []
            token_health.run_all_checks(niche_ids=())

        mock_native.assert_called_once_with(niche_ids=())
