"""Atomically update KEY=VALUE lines in a .env file.

Used by token-refresh code paths so refreshed credentials persist across
process boundaries. Without this, mutations to ``os.environ`` are lost
when the pipeline oneshot process exits — the next run reads stale values
from disk.

Rules:
- Atomic: write to a temp file in the same dir, then ``os.replace``.
- Preserves non-matching lines untouched.
- Quotes values that contain whitespace or special chars.
- Never logs the value itself — tokens must not leak into logs.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _shell_quote(value: str) -> str:
    """Minimal double-quote escape for .env compatibility."""
    if not value:
        return '""'
    if re.match(r"^[A-Za-z0-9._:/=+@-]+$", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def update_env_file(updates: dict[str, str], env_path: str | os.PathLike | None = None) -> bool:
    """Apply ``updates`` to a .env file atomically.

    Args:
        updates: Mapping of KEY -> new value. Keys must be valid env var
            names (uppercase letters, digits, underscores). Values are
            written as-is (double-quoted when they contain special chars).
        env_path: Path to the .env file. Defaults to
            ``$GENLAB_PROJECT_ROOT/.env`` and falls back to ``./.env``.

    Returns:
        True on success, False if the file could not be updated.
    """
    if not updates:
        return True
    for key in updates:
        if not _KEY_RE.match(key):
            logger.error("[env_writer] invalid env key: %r", key)
            return False

    if env_path is None:
        root = os.environ.get("GENLAB_PROJECT_ROOT", "")
        env_path = Path(root) / ".env" if root else Path(".env")
    env_path = Path(env_path)

    try:
        existing_lines = (
            env_path.read_text().splitlines()
            if env_path.exists()
            else []
        )
    except OSError as exc:
        logger.error("[env_writer] cannot read %s: %s", env_path, exc)
        return False

    # Replace in place; track which keys we've seen so we can append new ones
    remaining = dict(updates)
    out_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in remaining:
            out_lines.append(f"{key}={_shell_quote(remaining.pop(key))}")
        else:
            out_lines.append(line)

    # Append any keys that weren't in the file yet
    if remaining:
        if out_lines and out_lines[-1].strip():
            out_lines.append("")
        for key, val in remaining.items():
            out_lines.append(f"{key}={_shell_quote(val)}")

    # Atomic replace: write temp file in same dir, rename over original
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=".env.", dir=str(env_path.parent), text=True,
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write("\n".join(out_lines))
                if out_lines and not out_lines[-1].endswith("\n"):
                    fh.write("\n")
            # Preserve original file's mode if it exists; otherwise use 0600
            try:
                mode = env_path.stat().st_mode & 0o7777
            except FileNotFoundError:
                mode = 0o600
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, env_path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.error("[env_writer] atomic write failed for %s: %s", env_path, exc)
        return False

    logger.info(
        "[env_writer] updated %d key(s) in %s",
        len(updates), env_path,
    )
    return True
