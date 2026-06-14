"""Regression pins for the deploy.sh backup + backup_db.sh DSN bug
discovered during the 2026-06-14 PR #183 migration rollout.

The bug had two coupled halves:

  1. ``scripts/deploy.sh`` piped ``backup_db.sh`` into ``tee``. With
     ``set -o pipefail`` and ``set -e``, a non-zero backup exit aborted
     the whole deploy before alembic ran — leaving prod with code on
     a new HEAD and schema on an old revision.

  2. ``scripts/backup_db.sh`` invoked ``pg_dump genlab`` with no
     connection args. On prod the script runs as root via deploy.sh;
     PostgreSQL has no ``root`` role, so the local-socket auth failed
     with ``FATAL: role "root" does not exist``. The fix is to prefer
     ``DATABASE_URL`` (sourced from ``.env`` if needed) and pass it as
     the positional dbname arg (libpq URLs accepted since pg 9.6).

These tests pin both halves so they can't quietly regress.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_DEPLOY_SH = _GENLAB_ROOT / "scripts" / "deploy.sh"
_BACKUP_SH = _GENLAB_ROOT / "scripts" / "backup_db.sh"


# ---------------------------------------------------------------------------
# Half 1 — deploy.sh: backup failure MUST be non-fatal
# ---------------------------------------------------------------------------


def test_deploy_sh_syntax() -> None:
    """Catches typos / unclosed quotes / heredoc mistakes cheaply."""
    result = subprocess.run(
        ["bash", "-n", str(_DEPLOY_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


def test_deploy_sh_uses_safe_backup_invocation() -> None:
    """Pin the fix: backup is invoked inside an ``if ... ; then ... ; else``
    so the exit code is captured explicitly rather than propagated
    through a pipe."""
    src = _DEPLOY_SH.read_text()

    # The exact safe pattern: redirect-append into LOG, then branch on
    # exit code. Anchored on ``backup_db.sh`` to avoid drift if the
    # log path variable is renamed.
    assert 'if "$GENLAB/scripts/backup_db.sh" >>"$LOG" 2>&1; then' in src, (
        "deploy.sh must invoke backup_db.sh inside an if/then/else so "
        "its exit code is captured instead of propagating through a "
        "pipefail-sensitive pipe. The fixed pattern is:\n\n"
        '  if "$GENLAB/scripts/backup_db.sh" >>"$LOG" 2>&1; then\n'
        '      log "Backup ✓"\n'
        "  else\n"
        "      ...\n"
        "  fi"
    )

    # And the WARN-on-failure branch must exist — silently swallowing
    # the failure would be just as bad as crashing.
    assert "WARN: backup_db.sh exited" in src, (
        "deploy.sh must log a WARN with the backup exit code on failure "
        "so operators see the failure even when the deploy proceeds."
    )


def test_deploy_sh_does_not_pipe_backup_into_tee() -> None:
    """Anti-pin: the original buggy pattern (``backup_db.sh | tee``) must
    never reappear. With ``set -o pipefail`` it kills the deploy on a
    non-zero backup exit — exactly what the fix removes."""
    src = _DEPLOY_SH.read_text()

    # The bug shape was literally: `"$GENLAB/scripts/backup_db.sh" 2>&1 | tee -a "$LOG"`
    # Pin against the substring `backup_db.sh" 2>&1 | tee`.
    assert 'backup_db.sh" 2>&1 | tee' not in src, (
        "deploy.sh must not pipe backup_db.sh into tee — with pipefail "
        "this propagates a non-zero backup exit and aborts the deploy "
        "before alembic runs. Use ``>>$LOG 2>&1`` + explicit if/then "
        "instead."
    )


# ---------------------------------------------------------------------------
# Half 2 — backup_db.sh: DATABASE_URL preferred over socket auth
# ---------------------------------------------------------------------------


def test_backup_sh_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(_BACKUP_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


def test_backup_sh_uses_database_url_when_set(tmp_path: Path) -> None:
    """End-to-end behavioural pin: with DATABASE_URL set, backup_db.sh
    must invoke ``pg_dump <URL>`` (positional libpq URL), not
    ``pg_dump genlab`` (which silently falls back to OS-user socket
    auth and breaks under root)."""

    # Stub pg_dump on PATH — it just records its argv to a file and
    # writes a tiny payload to stdout (which backup_db.sh will gzip).
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    argv_log = tmp_path / "pg_dump_argv.log"
    stub = stub_dir / "pg_dump"
    stub.write_text(
        f"""#!/bin/bash
printf '%s\\n' "$@" > {argv_log}
echo "-- stub dump"
"""
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # GENLAB_ROOT for the script — resolved via `dirname BASH_SOURCE`
    # then `..`. Place backup_db.sh inside a tmp_path/scripts/ dir and
    # let it write backups into tmp_path/.tmp/backups/.
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    test_backup_sh = scripts_dir / "backup_db.sh"
    test_backup_sh.write_text(_BACKUP_SH.read_text())
    test_backup_sh.chmod(0o755)

    dsn = "postgresql://genlab:secret@example.invalid:5432/genlab"
    env = {
        **os.environ,
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "DATABASE_URL": dsn,
    }

    result = subprocess.run(
        [str(test_backup_sh)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, (
        f"backup_db.sh failed unexpectedly:\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    # The stub must have been invoked with the DSN as a positional arg.
    assert argv_log.exists(), "pg_dump stub was never invoked"
    captured_argv = argv_log.read_text().strip().splitlines()
    assert captured_argv == [dsn], (
        f"Expected pg_dump to be called with exactly [{dsn}], got {captured_argv}. "
        "If this fails, backup_db.sh has regressed to the bare "
        "``pg_dump genlab`` form that breaks under root on prod."
    )


def test_backup_sh_sources_dotenv_when_dsn_absent(tmp_path: Path) -> None:
    """Operator runs ``./scripts/backup_db.sh`` directly without
    exporting DATABASE_URL. The script must source ``$GENLAB_ROOT/.env``
    so the deploy.sh-invocation path on prod still works without
    requiring the caller to pre-export env vars."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    argv_log = tmp_path / "pg_dump_argv.log"
    stub = stub_dir / "pg_dump"
    stub.write_text(
        f"""#!/bin/bash
printf '%s\\n' "$@" > {argv_log}
echo "-- stub dump"
"""
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    test_backup_sh = scripts_dir / "backup_db.sh"
    test_backup_sh.write_text(_BACKUP_SH.read_text())
    test_backup_sh.chmod(0o755)

    # Drop a .env at the synthetic GENLAB_ROOT with a recognisable DSN.
    dsn = "postgresql://envuser:envpass@envhost:5432/envdb"
    (tmp_path / ".env").write_text(f"DATABASE_URL={dsn}\n")

    env = {
        # Deliberately strip DATABASE_URL — the script must find it
        # in the .env file.
        k: v
        for k, v in os.environ.items()
        if k != "DATABASE_URL"
    }
    env["PATH"] = f"{stub_dir}:{env['PATH']}"

    result = subprocess.run(
        [str(test_backup_sh)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, (
        f"backup_db.sh failed unexpectedly:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert argv_log.exists(), "pg_dump stub was never invoked"
    captured_argv = argv_log.read_text().strip().splitlines()
    assert captured_argv == [dsn], (
        f"Expected pg_dump to be called with the .env-sourced DSN [{dsn}], "
        f"got {captured_argv}. The .env-sourcing block has regressed."
    )


def test_backup_sh_warns_when_falling_back_to_socket(tmp_path: Path) -> None:
    """The legacy ``pg_dump genlab`` fallback is kept for local dev but
    must emit a WARN so the prod-style failure mode (root → no role)
    is visible in deploy logs if it ever fires."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "pg_dump"
    # Stub exits 0 so the script completes — we only care about the
    # WARN message on stderr.
    stub.write_text('#!/bin/bash\necho "-- stub"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    test_backup_sh = scripts_dir / "backup_db.sh"
    test_backup_sh.write_text(_BACKUP_SH.read_text())
    test_backup_sh.chmod(0o755)

    # No DATABASE_URL anywhere — no env, no .env file.
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PATH"] = f"{stub_dir}:{env['PATH']}"

    result = subprocess.run(
        [str(test_backup_sh)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0
    assert "DATABASE_URL not set" in result.stderr, (
        "When falling back to socket auth, backup_db.sh must warn so "
        "the visible-failure-on-prod case (root → no role) is debugged "
        "from deploy logs."
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
