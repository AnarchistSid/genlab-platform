"""R-67: near-full swap must raise a CRITICAL alert (so it reaches notify(),
which forwards only criticals) — not sit as an unactioned warning.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import check_swap

_MOD = "genlab_core.monitoring.health_monitor"


def _free(total: int, used: int) -> str:
    return (
        "              total        used        free\n"
        "Mem:    4000000000  3000000000  1000000000\n"
        f"Swap:   {total}  {used}  {total - used}\n"
    )


def test_swap_above_90pct_is_critical():
    with patch(
        f"{_MOD}.subprocess.run", return_value=MagicMock(stdout=_free(2_000_000_000, 1_900_000_000))
    ):
        alerts = check_swap()
    assert any(a.check == "swap_pressure" and a.severity == "critical" for a in alerts)


def test_swap_midrange_is_warning_not_critical():
    # 600MB / 2GB = 30% — over the 500MB warn line, under the 90% critical line.
    with patch(
        f"{_MOD}.subprocess.run", return_value=MagicMock(stdout=_free(2_000_000_000, 600_000_000))
    ):
        alerts = check_swap()
    assert any(a.severity == "warning" for a in alerts)
    assert not any(a.severity == "critical" for a in alerts)


def test_low_swap_no_alert():
    with patch(
        f"{_MOD}.subprocess.run", return_value=MagicMock(stdout=_free(2_000_000_000, 100_000_000))
    ):
        alerts = check_swap()
    assert alerts == []
