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


# ---------------------------------------------------------------------------
# PR #507 (2026-06-24) — git log | head -20 | tee SIGPIPE fix
#
# With set -o pipefail (line 36) AND set -e, the pipeline `git log ... |
# head -20 | tee` returns exit 141 when git log produces >20 lines.
# `head -20` exits early, SIGPIPE fires upstream to `git log`, and the
# pipeline's exit status becomes 141 — set -e then kills the deploy.
# Hit on 2026-06-23 trying to deploy a 22-commit pull; operator had to
# bypass the script entirely and run git pull + systemctl restart manually.
# ---------------------------------------------------------------------------


def test_deploy_sh_scopes_pipefail_disable_in_subshell() -> None:
    """The git log pipeline must run inside a subshell with pipefail off.

    Pin the exact safe pattern — a refactor that simplifies back to the
    bare `git log ... | head -20 | tee` shape re-introduces the bug
    invisibly (a deploy with ≤20 commits succeeds, a deploy with >20
    commits exits 141).
    """
    src = _DEPLOY_SH.read_text()
    # Pin the subshell + set +o pipefail shape
    assert "set +o pipefail" in src, (
        "deploy.sh must contain `set +o pipefail` to scope the disable "
        "around the `git log | head -20 | tee` line (PR #507)."
    )
    assert 'git log --oneline "$HEAD_BEFORE..$REMOTE_HEAD" | head -20 | tee -a "$LOG"' in src, (
        "The git log show-the-gap pipeline must still exist (we're not "
        "removing it — just scoping pipefail off around it)."
    )


def test_deploy_sh_git_log_pipe_does_not_kill_script_when_head_truncates() -> None:
    """End-to-end behavioural pin: emulate the bug shape standalone.

    Pre-fix, this exact pipeline shape exits 141 under pipefail. Post-fix,
    the subshell-scoped `set +o pipefail` lets the pipeline exit 0 (tee's
    success) regardless of head truncation.
    """
    # Reproduce the script's prologue + the specific pattern. yes(1)
    # produces infinite output; head -20 truncates to 20 lines; tee writes
    # them to a tmp log.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".log", mode="w", delete=False) as tmp:
        tmp_log = tmp.name

    # The PRE-FIX shape — assert it DOES exit 141 (proving the test
    # reproduces the bug shape correctly)
    pre_fix_script = f"""
set -euo pipefail
yes "hello" | head -20 | tee {tmp_log} > /dev/null
"""
    pre_fix = subprocess.run(["bash", "-c", pre_fix_script], capture_output=True, text=True)
    assert pre_fix.returncode == 141, (
        f"Pre-fix shape should exit 141 (SIGPIPE) under set -o pipefail. "
        f"Got: returncode={pre_fix.returncode}, stderr={pre_fix.stderr!r}"
    )

    # The POST-FIX shape — assert it exits 0 (proving the fix works)
    post_fix_script = f"""
set -euo pipefail
(
    set +o pipefail
    yes "hello" | head -20 | tee {tmp_log} > /dev/null
)
"""
    post_fix = subprocess.run(["bash", "-c", post_fix_script], capture_output=True, text=True)
    assert post_fix.returncode == 0, (
        f"Post-fix shape must exit 0 — pipefail scoped to the subshell. "
        f"Got: returncode={post_fix.returncode}, stderr={post_fix.stderr!r}"
    )

    # Cleanup
    Path(tmp_log).unlink(missing_ok=True)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
