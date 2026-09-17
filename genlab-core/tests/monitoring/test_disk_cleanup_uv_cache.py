"""The uv cache must be MEASURED, not just pruned.

`uv cache prune --ci` ran hourly from 2026-07-15 and the cache still reached
6.1 GB on 2026-09-17, taking prod to 96% disk (4.8 GB free) on the volume
Postgres lives on. Prod PG crashed at 100% disk on 2026-07-01, so this is the
failure mode that matters. The prune was never reporting a number, which is how
it grew unnoticed twice.
"""

import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "disk_cleanup.sh"


@pytest.fixture(scope="module")
def sh():
    return SRC.read_text()


def test_the_cache_size_is_logged_every_run(sh):
    assert "uv cache before:" in sh and "uv cache after prune:" in sh, (
        "an unreported size is how this grew to 6.1 GB twice")


def test_a_threshold_exists_and_is_overridable_without_a_deploy(sh):
    assert "UV_CACHE_MAX_GB" in sh
    assert "GENLAB_UV_CACHE_MAX_GB" in sh, "no env override for the threshold"
    assert ':-2}' in sh, "threshold default is not 2 GB"


def test_prune_alone_is_not_the_whole_remedy(sh):
    """The measured fact: hourly prune did not bound the cache."""
    assert "uv cache clean" in sh, (
        "only `prune --ci` present; that ran hourly for two months and the "
        "cache still hit 6.1 GB")


def test_the_full_clean_is_conditional_not_unconditional(sh):
    """Emptying the cache every hour would make every deploy slower for nothing."""
    body = sh[sh.index("UV_CACHE_MAX_GB="):]
    clean_at = body.index("uv cache clean")
    guard = body[:clean_at]
    assert "if awk" in guard and "uv_after >" in guard, (
        "the clean is not gated on the measured size")


def test_every_uv_invocation_sets_HOME(sh):
    """Without HOME, uv reads /root/uv.toml as the genlab user and dies with a
    permission error -- measured 2026-09-17.

    Asserted on the INVOCATION lines, not on a substring search: the phrase
    "uv cache clean" also appears inside a log message, and a naive search finds
    that first and tests nothing.
    """
    calls = [ln for ln in sh.splitlines()
             if "sudo -u genlab" in ln and "uv cache" in ln]
    assert len(calls) >= 2, f"expected prune and clean invocations, found {len(calls)}"
    for ln in calls:
        assert "HOME=/opt/genlab" in ln, f"no HOME in: {ln.strip()[:90]}"


def test_the_script_still_exits_zero_on_failure(sh):
    """systemd reads non-zero as an incident (rule #26); cleanup failures are
    data-side signals, not pages."""
    assert "exit 0" in sh or "always exits 0" in sh


def test_the_history_block_records_why_v4_exists(sh):
    assert "2026-09-17 v4" in sh and "6.1 GB" in sh
