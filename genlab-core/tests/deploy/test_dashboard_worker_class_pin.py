"""U-07: pin the dashboard gunicorn worker class + version constraint.

Motivation (SYSTEM-RESEARCH.md U-07):

    gunicorn v26 (upcoming release) removes the ``eventlet`` worker class
    that the GenLab dashboard previously ran. This is a latent breaker:
    until the next ``uv lock --upgrade`` bumps gunicorn past 25.x the
    dashboard keeps working; the moment it does, ``gunicorn`` fails to
    start with ``Class not found: eventlet`` and the operator loses the
    review UI (no approvals → backlog stalls → no publishes).

    PR P preempted this by:

      1. Pinning ``gunicorn<26`` in every package that declares it
         (``dashboard/pyproject.toml`` is the canonical one; the BB
         extras pin matches as a belt-and-suspenders).
      2. Migrating the dashboard systemd unit from ``--worker-class
         eventlet`` to ``--worker-class gthread --threads 4``. psycopg
         (sync) cooperates cleanly with gthread; the only behavioral
         change is that Flask-SocketIO's async_mode falls back from
         ``eventlet`` to ``threading``, which means Socket.IO runs on
         HTTP long-polling only. The production deployment already runs
         behind a Cloudflare tunnel that forces polling
         (``allow_upgrades=False``), so the user-visible behavior is
         unchanged.

These three pins encode the contract so a future drift (someone
re-introduces eventlet, drops ``--threads``, or relaxes the version
upper bound) fails CI instead of the next ``uv lock --upgrade``.
"""

from __future__ import annotations

from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_DASHBOARD_UNIT = _GENLAB_ROOT / "deploy" / "systemd-phase2" / "genlab-dashboard.service"
_DASHBOARD_PYPROJECT = _GENLAB_ROOT / "dashboard" / "pyproject.toml"


def test_systemd_unit_uses_gthread_worker() -> None:
    """U-07: the dashboard systemd unit must use the gthread worker, not
    eventlet. eventlet is removed in gunicorn v26 — if this regresses,
    the dashboard will fail to start the moment gunicorn is upgraded
    past 25.x.
    """
    unit = _DASHBOARD_UNIT.read_text()
    assert "--worker-class gthread" in unit, (
        "U-07 regression: deploy/systemd-phase2/genlab-dashboard.service "
        "must use ``--worker-class gthread``. eventlet is removed in "
        "gunicorn v26 and the next ``uv lock --upgrade`` would brick the "
        "dashboard at startup."
    )
    assert "--worker-class eventlet" not in unit, (
        "U-07 regression: ``--worker-class eventlet`` is present in the "
        "dashboard systemd unit. This worker class is removed in "
        "gunicorn v26. Switch to ``--worker-class gthread --threads 4``."
    )


def test_systemd_unit_specifies_thread_count() -> None:
    """gthread is a synchronous worker — it must be told how many
    threads to spawn per worker. Without ``--threads`` gunicorn defaults
    to 1 which collapses concurrency to a single in-process queue
    (any slow handler blocks every other request). Pin that the unit
    sets a non-default thread count.
    """
    unit = _DASHBOARD_UNIT.read_text()
    assert "--threads" in unit, (
        "U-07 regression: deploy/systemd-phase2/genlab-dashboard.service "
        "uses the gthread worker but does not pass ``--threads N``. "
        "gthread needs an explicit thread count; without it the dashboard "
        "serves requests strictly sequentially."
    )


def test_gunicorn_pinned_below_v26() -> None:
    """The dashboard pyproject must keep gunicorn pinned below v26 until
    the v26 eventlet-removal is irrelevant (or eventlet is restored as
    a third-party plugin). Silent unpinning would re-arm the U-07
    breaker the moment ``uv lock --upgrade`` runs.
    """
    pyproject = _DASHBOARD_PYPROJECT.read_text()
    assert "gunicorn" in pyproject, (
        "dashboard/pyproject.toml no longer declares gunicorn as a "
        "dependency. If gunicorn moved to another package, update this "
        "test to point at that package's pyproject."
    )
    # Accept either ``gunicorn<26`` or ``gunicorn>=X,<26`` etc.; the
    # operative constraint is the ``<26`` upper bound.
    assert "<26" in pyproject, (
        "U-07 regression: dashboard/pyproject.toml must keep gunicorn "
        "constrained to ``<26`` (eventlet was removed in v26). If the "
        "dashboard no longer needs the eventlet→gthread guardrail "
        "because async_mode is fixed in code, update this test and "
        "SYSTEM-RESEARCH.md U-07 together."
    )
