"""Tests for genlab_core.observability.durable_error.write_durable_error.

Motivating pattern: journal rotation on the 4 GB Hetzner VPS eats
stderr tracebacks within days. The durable file survives — operators
can cat it any time after the incident.

See [[class-of-bug-signal-loss-through-merged-failure-paths]] for
the class-of-bug context.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_runtime(tmp_path: Path) -> Path:
    """Isolated runtime root — never touches /opt/genlab/.runtime."""
    return tmp_path / "runtime"


def test_writes_traceback_to_expected_path(tmp_runtime: Path):
    """Happy path: exception + script_name produce
    <runtime_root>/<script_name>_last_error.txt with the traceback."""
    from genlab_core.observability.durable_error import write_durable_error

    try:
        raise ValueError("synthetic bug")
    except ValueError as exc:
        write_durable_error("test_script", exc, runtime_root=tmp_runtime)

    expected = tmp_runtime / "test_script_last_error.txt"
    assert expected.exists()
    content = expected.read_text()
    assert "ERROR: synthetic bug" in content
    assert "ValueError" in content
    assert "script: test_script" in content
    # ISO timestamp on first line
    first_line = content.splitlines()[0]
    assert "T" in first_line and ":" in first_line


def test_context_dict_written_before_traceback(tmp_runtime: Path):
    """Context keys/values must appear ABOVE the traceback so operators
    see the identifying info (niche_id, batch_id, etc.) first."""
    from genlab_core.observability.durable_error import write_durable_error

    try:
        raise RuntimeError("multi-niche fail")
    except RuntimeError as exc:
        write_durable_error(
            "publisher",
            exc,
            context={"niche_id": "gaming", "batch_id": "abc-123"},
            runtime_root=tmp_runtime,
        )

    content = (tmp_runtime / "publisher_last_error.txt").read_text()
    assert "niche_id: gaming" in content
    assert "batch_id: abc-123" in content
    # Context must appear BEFORE the ERROR line
    niche_pos = content.index("niche_id: gaming")
    err_pos = content.index("ERROR: multi-niche fail")
    assert niche_pos < err_pos


def test_creates_missing_runtime_dir(tmp_path: Path):
    """runtime_root doesn't need to exist — mkdir(parents=True)
    handles first-run bootstrap."""
    from genlab_core.observability.durable_error import write_durable_error

    nested = tmp_path / "deeply" / "nested" / "runtime"
    assert not nested.exists()

    try:
        raise Exception("bootstrap test")
    except Exception as exc:
        write_durable_error("test", exc, runtime_root=nested)

    assert nested.exists()
    assert (nested / "test_last_error.txt").exists()


def test_permission_error_does_not_raise(tmp_path: Path, monkeypatch):
    """Durable-write failure MUST NOT raise — the caller is already
    handling an exception and needs to exit cleanly with its original
    signal. Rule #15 sibling: permission drift on state file dir must
    not swallow the actual failure."""
    from genlab_core.observability import durable_error

    def _fail_mkdir(*args, **kwargs):
        raise PermissionError("simulated /opt/genlab/.runtime chown drift")

    monkeypatch.setattr(Path, "mkdir", _fail_mkdir)

    try:
        raise ValueError("original exception")
    except ValueError as exc:
        # Must not raise.
        durable_error.write_durable_error(
            "test", exc, runtime_root=tmp_path / "runtime"
        )


def test_overwrites_previous_file(tmp_runtime: Path):
    """Only the LAST error matters for triage — the file overwrites on
    each write. Systemd's alarm carries the incident timestamp
    separately; the durable file is the current diagnostic."""
    from genlab_core.observability.durable_error import write_durable_error

    for msg in ("first fail", "second fail", "third fail"):
        try:
            raise ValueError(msg)
        except ValueError as exc:
            write_durable_error("test", exc, runtime_root=tmp_runtime)

    content = (tmp_runtime / "test_last_error.txt").read_text()
    assert "ERROR: third fail" in content
    assert "ERROR: first fail" not in content


def test_helper_exposes_stable_filename_shape(tmp_runtime: Path):
    """Pin the filename convention `<script>_last_error.txt` so
    operator runbooks + monitoring dashboards can rely on it."""
    from genlab_core.observability.durable_error import write_durable_error

    try:
        raise Exception("boom")
    except Exception as exc:
        write_durable_error("check_affiliate_links", exc, runtime_root=tmp_runtime)

    assert (tmp_runtime / "check_affiliate_links_last_error.txt").exists()


class TestClearDurableError:
    """QB-FIX-12: clear_durable_error companion helper.

    Motivating incident: parse_testable_predictions + auto_accept_strategist_
    proposals were fixed by dd376829 on 2026-07-23 but the pre-fix durable
    error files sat for 14 days causing daily stale_durable_error warnings.
    If those scripts had called clear_durable_error() on their next successful
    run after the fix landed, the alerts would have self-resolved.
    """

    def test_clear_removes_existing_file(self, tmp_runtime: Path):
        from genlab_core.observability.durable_error import (
            clear_durable_error,
            write_durable_error,
        )

        try:
            raise ValueError("simulated failure")
        except Exception as exc:
            write_durable_error("myscript", exc, runtime_root=tmp_runtime)

        assert (tmp_runtime / "myscript_last_error.txt").exists()
        clear_durable_error("myscript", runtime_root=tmp_runtime)
        assert not (tmp_runtime / "myscript_last_error.txt").exists()

    def test_clear_noop_when_file_missing(self, tmp_runtime: Path):
        """clear on nonexistent file must not raise (missing_ok=True)."""
        from genlab_core.observability.durable_error import clear_durable_error

        tmp_runtime.mkdir(exist_ok=True)
        # No pre-write; ensure no crash.
        clear_durable_error("never_ran", runtime_root=tmp_runtime)
        assert not (tmp_runtime / "never_ran_last_error.txt").exists()

    def test_clear_survives_missing_runtime_dir(self, tmp_runtime: Path):
        """Runtime root may not exist yet — clear must not error."""
        from genlab_core.observability.durable_error import clear_durable_error

        # tmp_runtime intentionally not created
        clear_durable_error("script", runtime_root=tmp_runtime)
        # Assertion: reached this line without exception
        assert True
