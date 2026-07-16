"""Pins for the ``genlab-publisher.service`` systemd unit.

The publisher runs sequentially over niches
(``publish_all_platforms.py:688 for nid in niches:``). Per-blueprint,
``parallel_publish.execute_parallel_publish`` uses a
ThreadPoolExecutor with ``DEFAULT_PUBLISH_TIMEOUT_SECONDS=600``.
Worst-case per niche is 600s (dominated by IG's polling budget when
a reel gets stuck in Meta's processing queue).

5 niches × 600s = 3000s. TimeoutSec MUST be ≥ 3600s to guarantee
all 5 niches get processed even in the worst-case-hang scenario
plus a 600s margin. Any lower and anime (which runs last
alphabetically or last in the ordering) risks getting TERMed before
processing — happened on 2026-07-07 with the old 1800s value.

If TimeoutSec regresses below 3600, this test fires at CI time
rather than after another live-fire miss.
"""

from __future__ import annotations

import configparser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_FILE = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-publisher.service"


def _read_unit(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.read(path)
    return cfg


class TestPublisherServiceUnit:
    def test_unit_file_present(self):
        assert SERVICE_FILE.is_file(), (
            f"Publisher service unit missing at {SERVICE_FILE} — "
            "this file is the source of truth deployed to prod."
        )

    def test_timeout_sec_covers_worst_case_niche_x_600s(self):
        """5 niches × 600s per-niche worst-case = 3000s minimum. Old
        value 1800s TERMed publisher after 4 niches on 2026-07-07,
        anime never processed. Must be at least 3600s (5 niches +
        one full extra niche of headroom).

        If a future change reduces this below 3600 without also
        reducing DEFAULT_PUBLISH_TIMEOUT_SECONDS below 600, the
        publisher risks re-hitting the 2026-07-07 anime-miss bug.
        """
        cfg = _read_unit(SERVICE_FILE)
        timeout = int(cfg["Service"]["TimeoutSec"])
        assert timeout >= 3600, (
            f"TimeoutSec={timeout}s below 3600s threshold. Publisher "
            f"processes niches sequentially; 5 niches × 600s per-niche "
            f"worst case = 3000s. Anything under 3600s risks a TERM "
            f"before the last-in-order niche gets processed (2026-07-07 "
            f"bug: anime never published). If you MEANT to reduce this, "
            f"also reduce DEFAULT_PUBLISH_TIMEOUT_SECONDS in "
            f"parallel_publish.py and update this test's floor."
        )

    def test_runs_as_genlab_user(self):
        """Publisher must NOT run as root — writes to media paths owned
        by the genlab user."""
        cfg = _read_unit(SERVICE_FILE)
        assert cfg["Service"]["User"] == "genlab"

    def test_service_is_oneshot(self):
        """Each fire is a discrete run, not a daemon."""
        cfg = _read_unit(SERVICE_FILE)
        assert cfg["Service"]["Type"] == "oneshot"

    def test_service_loads_env_file(self):
        """DATABASE_URL, GENLAB_* flags, and platform tokens all live
        in .env — without this, the publisher can't even connect."""
        cfg = _read_unit(SERVICE_FILE)
        assert cfg["Service"]["EnvironmentFile"] == "/opt/genlab/.env"

    def test_success_exit_status_allowlists_benign_codes(self):
        """publish_all_platforms.py returns codes 1 (NO_BLUEPRINTS),
        3 (DAILY_CAP), 4 (LOCK_HELD) for benign non-success. These
        must NOT trigger OnFailure — otherwise the operator gets
        alerted on every empty-queue day.

        Exit 2 (ALL_FAILED) is intentionally NOT in the allowlist —
        it's a real failure and should fire OnFailure."""
        cfg = _read_unit(SERVICE_FILE)
        raw = cfg["Service"]["SuccessExitStatus"]
        codes = set(raw.split())
        assert codes >= {"0", "1", "3", "4"}, (
            f"SuccessExitStatus must include 0, 1, 3, 4. Got: {raw}"
        )
        assert "2" not in codes, (
            "Exit code 2 (ALL_FAILED) must NOT be in SuccessExitStatus — "
            "it's the real-failure signal that OnFailure should fire on."
        )

    def test_on_failure_hook_present(self):
        """CriticalAlertsBanner subscribes to
        genlab-service-failure-alert@ — removing this hook silences
        every real publisher failure from the dashboard."""
        content = SERVICE_FILE.read_text()
        assert "OnFailure=genlab-service-failure-alert@" in content, (
            "OnFailure hook missing — publisher failures would go "
            "unalerted to CriticalAlertsBanner."
        )
