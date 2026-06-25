"""Source pins for the systemd units that activate PRs #580 + #585.

Background: PRs #580 (niche_pause_sweeper) and #585 (compliance_
digest_sender) shipped CLI modules but no systemd timer files.
The CLIs were therefore DEPLOYED but NEVER FIRED on prod — a
"shipped-but-not-activated" pattern surfaced by the 2026-06-25
prod audit.

This PR adds:
  * deploy/systemd-phase2/genlab-niche-pause-sweeper.{service,timer}
  * deploy/systemd-phase2/genlab-compliance-digest-sender.{service,timer}

scripts/install.sh already loops through deploy/systemd-phase2/*.{service,
timer} so the files are picked up automatically on the next install run.
Operator enables each with `systemctl enable --now <unit>.timer`.

These source pins guarantee the unit files exist with the
expected ExecStart targets so a future refactor that renames a
CLI doesn't silently leave the systemd timer pointing at a
removed module.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SYSTEMD_DIR = _REPO_ROOT / "deploy" / "systemd-phase2"


def _read(unit_name: str) -> str:
    path = _SYSTEMD_DIR / unit_name
    assert path.exists(), f"Missing systemd unit file: {path}"
    return path.read_text()


# ── niche_pause_sweeper (PR #580 activation) ──────────────────────


def test_niche_pause_sweeper_service_file_exists_and_invokes_module():
    """The .service unit must exec the niche_pause_sweeper module.
    Pin so a future rename of the CLI module surfaces here, not
    as silent prod inaction."""
    content = _read("genlab-niche-pause-sweeper.service")
    assert "[Service]" in content
    assert "ExecStart=" in content
    assert "genlab_core.scheduling.niche_pause_sweeper" in content
    # Runs as genlab user (not root) — same as sibling services
    assert "User=genlab" in content
    # oneshot, not long-running — matches the sweeper's one-pass design
    assert "Type=oneshot" in content


def test_niche_pause_sweeper_timer_runs_daily_off_peak():
    """The .timer must fire daily, not too close to gate-tuner
    (01:00 UTC) or AI pipeline (02:30 UTC). Pin so a future
    operator's schedule shuffle doesn't accidentally overlap."""
    content = _read("genlab-niche-pause-sweeper.timer")
    assert "[Timer]" in content
    assert "OnCalendar=" in content
    # 02:00 UTC = clean slot between gate-tuner (01:00) and
    # AI pipeline (02:30)
    assert "02:00:00 UTC" in content
    # Catch up after reboot — sweep is idempotent + missed nights
    # are operationally fine
    assert "Persistent=true" in content


# ── compliance_digest_sender (PR #585 activation) ─────────────────


def test_compliance_digest_sender_service_file_exists_and_invokes_module():
    content = _read("genlab-compliance-digest-sender.service")
    assert "[Service]" in content
    assert "ExecStart=" in content
    assert "genlab_core.compliance.compliance_digest_sender" in content
    assert "User=genlab" in content
    assert "Type=oneshot" in content


def test_compliance_digest_sender_timer_fires_at_operator_morning():
    """Digest must hit Slack BEFORE operator opens the dashboard.
    Operator's morning IST = 08:00–10:00 IST = 02:30–04:30 UTC.
    The 03:30 UTC = 09:00 IST slot is in the middle of that
    window and after the publisher's 06:30 UTC fire from the
    PRIOR day completed."""
    content = _read("genlab-compliance-digest-sender.timer")
    assert "[Timer]" in content
    assert "OnCalendar=" in content
    assert "03:30:00 UTC" in content
    assert "Persistent=true" in content


# ── install.sh integration ────────────────────────────────────────


def test_units_will_be_picked_up_by_install_script():
    """scripts/install.sh phase 7 loops through
    deploy/systemd-phase2/*.service and *.timer files. Both
    new units must land in that directory so the next install
    run copies them to /etc/systemd/system/ automatically.

    Pin: the unit files live in the right dir."""
    expected = [
        "genlab-niche-pause-sweeper.service",
        "genlab-niche-pause-sweeper.timer",
        "genlab-compliance-digest-sender.service",
        "genlab-compliance-digest-sender.timer",
    ]
    for name in expected:
        assert (_SYSTEMD_DIR / name).exists(), (
            f"{name} not found in {_SYSTEMD_DIR} — won't be installed by scripts/install.sh phase 7"
        )


def test_unit_files_reference_existing_python_modules():
    """The ExecStart=/opt/genlab/.venv/bin/python -m <module> calls
    target real modules in the genlab-core package. Verify both
    modules exist so a future renaming surfaces here, not at
    systemd-fire time on prod."""
    import importlib.util

    for module_path in [
        "genlab_core.scheduling.niche_pause_sweeper",
        "genlab_core.compliance.compliance_digest_sender",
    ]:
        spec = importlib.util.find_spec(module_path)
        assert spec is not None, (
            f"systemd unit references {module_path!r} but the module is not "
            f"importable. systemd timer fire on prod would exit non-zero."
        )
