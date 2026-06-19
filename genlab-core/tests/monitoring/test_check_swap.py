"""Swap-pressure check tests.

R-67: near-full swap must raise a CRITICAL alert (so it reaches notify(),
which forwards only criticals) — not sit as an unactioned warning.

2026-06-19 update: warning alerts now also require RAM pressure. Linux
opportunistically keeps idle pages in swap even when RAM is free, so
swap > 500MB alone was a noisy false-positive. The warning now fires
only when BOTH swap > warning_mb AND MemAvailable < 30% of total.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import check_swap

_MOD = "genlab_core.monitoring.health_monitor"


def _free(
    swap_total: int,
    swap_used: int,
    mem_total: int = 4_000_000_000,
    mem_available: int = 3_000_000_000,
) -> str:
    """Build a mock ``free -b`` output.

    Default mem values produce ``available/total = 75%`` — RAM-fine — so
    the swap-only false-positive case requires explicit override of
    mem_available to trigger the warning.
    """
    mem_used = mem_total - mem_available
    mem_free = mem_available
    return (
        "              total        used        free      shared  buff/cache  available\n"
        f"Mem:    {mem_total}  {mem_used}  {mem_free}     0           0           {mem_available}\n"
        f"Swap:   {swap_total}  {swap_used}  {swap_total - swap_used}\n"
    )


# ── Critical: imminent-OOM regardless of RAM ────────────────────────


def test_swap_above_90pct_is_critical():
    """R-67: 95% swap → CRITICAL even if RAM is plentiful."""
    with patch(
        f"{_MOD}.subprocess.run",
        return_value=MagicMock(stdout=_free(2_000_000_000, 1_900_000_000)),
    ):
        alerts = check_swap()
    assert any(a.check == "swap_pressure" and a.severity == "critical" for a in alerts)


def test_swap_critical_fires_even_with_abundant_ram():
    """The CRITICAL gate doesn't gate on RAM — full swap is full swap."""
    with patch(
        f"{_MOD}.subprocess.run",
        return_value=MagicMock(
            stdout=_free(
                swap_total=1_000_000_000,
                swap_used=950_000_000,
                mem_total=4_000_000_000,
                mem_available=3_800_000_000,  # 95% RAM free
            )
        ),
    ):
        alerts = check_swap()
    assert any(a.severity == "critical" for a in alerts)


# ── Warning: requires BOTH swap-high AND ram-pressure ──────────────


def test_swap_midrange_alone_does_not_warn_when_ram_fine():
    """2026-06-19 regression — the prod false-positive case.

    791MB swap (≈79%) but RAM at 1.7Gi free / 3.7Gi total (45% available)
    was triggering noisy warnings. With the new ram_pressure_pct=0.3
    gate, this case must NOT alert because RAM is fine.
    """
    with patch(
        f"{_MOD}.subprocess.run",
        return_value=MagicMock(
            stdout=_free(
                swap_total=1_000_000_000,
                swap_used=791_000_000,  # 79% — over the 500MB warn line
                mem_total=4_000_000_000,
                mem_available=1_800_000_000,  # 45% available — RAM fine
            )
        ),
    ):
        alerts = check_swap()
    assert alerts == [], f"Expected no alert (RAM fine), got: {alerts}"


def test_swap_midrange_warns_when_ram_also_under_pressure():
    """Genuine memory pressure case: both swap and RAM stressed."""
    with patch(
        f"{_MOD}.subprocess.run",
        return_value=MagicMock(
            stdout=_free(
                swap_total=2_000_000_000,
                swap_used=600_000_000,  # 30% — over 500MB warn line
                mem_total=4_000_000_000,
                mem_available=800_000_000,  # 20% available — below 30% gate
            )
        ),
    ):
        alerts = check_swap()
    assert any(a.severity == "warning" for a in alerts)
    assert not any(a.severity == "critical" for a in alerts)


def test_warning_message_mentions_both_swap_and_ram():
    """The new warning message includes both signals so operators can
    immediately see WHY the alert fired (vs the prior swap-only message)."""
    with patch(
        f"{_MOD}.subprocess.run",
        return_value=MagicMock(
            stdout=_free(
                swap_total=2_000_000_000,
                swap_used=600_000_000,
                mem_total=4_000_000_000,
                mem_available=800_000_000,  # 20% — RAM pressure
            )
        ),
    ):
        alerts = check_swap()
    warnings = [a for a in alerts if a.severity == "warning"]
    assert warnings, "Expected at least one warning"
    msg = warnings[0].message
    assert "Swap at" in msg
    assert "RAM available" in msg
    assert "memory pressure" in msg


# ── No alert ────────────────────────────────────────────────────────


def test_low_swap_no_alert():
    """100MB swap = below the warn line = no alert regardless of RAM."""
    with patch(
        f"{_MOD}.subprocess.run",
        return_value=MagicMock(stdout=_free(2_000_000_000, 100_000_000)),
    ):
        alerts = check_swap()
    assert alerts == []


def test_low_swap_with_ram_pressure_no_swap_alert():
    """RAM pressure alone (without high swap usage) doesn't trigger a
    swap_pressure alert — that's a different check entirely."""
    with patch(
        f"{_MOD}.subprocess.run",
        return_value=MagicMock(
            stdout=_free(
                swap_total=2_000_000_000,
                swap_used=100_000_000,  # 5% swap — under warn line
                mem_total=4_000_000_000,
                mem_available=400_000_000,  # 10% available — RAM pressure
            )
        ),
    ):
        alerts = check_swap()
    assert alerts == [], "swap_pressure shouldn't fire on RAM-only pressure"
