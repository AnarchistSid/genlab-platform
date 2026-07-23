"""Shared helper for writing exception tracebacks to a durable file
before a script exits non-zero.

Motivating pattern: systemd captures stderr in journalctl, but the
journal rotates within days on the 4 GB Hetzner VPS. Any script that
exits non-zero and relies on stderr for its traceback loses the
diagnostic once the journal rotates. Multiple failures across
publisher / nightly-scheduler / shared_ingestion this session all
had the same signature — an alert fires but the operator can't
diagnose from journal.

The durable file survives journal rotation. Operators can
``cat /opt/genlab/.runtime/<script>_last_error.txt`` any time after
the incident.

See:
* [[class-of-bug-signal-loss-through-merged-failure-paths]] — same
  class as content_pool + LLM merged-return-path bugs, applied at
  the systemd-exit boundary
* Rule #19 (CLAUDE.md): never elevate silent-fail without a
  WARNING — the durable file is the persistence layer that lets
  the WARNING actually survive to be read later.
"""

from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# All durable error files land under this directory. Owned by
# genlab:genlab per rule #15.
_RUNTIME_ROOT = Path("/opt/genlab/.runtime")


def write_durable_error(
    script_name: str,
    exc: BaseException,
    *,
    context: dict[str, Any] | None = None,
    runtime_root: Path | None = None,
) -> None:
    """Write an exception traceback to a durable file for post-hoc
    triage.

    Callers typically wrap their main() body in ``try/except`` and
    call this before returning a non-zero exit code. Same pattern
    as ``scripts/nightly_schedule_top_per_niche.py`` (2026-07-23
    origin site).

    Args:
        script_name: Slug used in the filename. Should identify the
            script uniquely — e.g. ``"publisher"`` for
            publish_all_platforms.py or ``"check_affiliate_links"``
            for check_affiliate_links.py.
        exc: The caught exception.
        context: Optional key/value pairs written above the traceback
            in the file. Useful for scripts that iterate niches or
            batches — pass ``{"niche_id": nid, "batch": batch_id}``.
        runtime_root: Override the durable-file directory. Default
            /opt/genlab/.runtime. Test-only knob.

    Never raises. If the durable-write itself fails (permissions
    drift per rule #15, filesystem full, etc.), the failure is
    logged as a WARNING to stderr + logger but the original
    ``exc`` is NOT masked — the caller's exit-code signal survives.
    """
    root = runtime_root or _RUNTIME_ROOT
    error_path = root / f"{script_name}_last_error.txt"

    # Print to stderr first so systemd's journal captures it too.
    # journalctl gets rotated but is fast to grep in the near-term
    # window; the durable file is the belt-and-suspenders backup.
    print(f"ERROR: {exc}", file=sys.stderr)
    try:
        traceback.print_exc(file=sys.stderr)
    except Exception:  # noqa: BLE001 — stderr write is best-effort
        pass

    try:
        root.mkdir(parents=True, exist_ok=True)
        with error_path.open("w") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"script: {script_name}\n")
            if context:
                for k, v in context.items():
                    f.write(f"{k}: {v}\n")
            f.write(f"\nERROR: {exc}\n\n")
            traceback.print_exc(file=f)
    except Exception as write_exc:
        # Nested-except guard — durable-write failure must NOT hide
        # the real error the caller is about to exit with.
        print(
            f"(also failed to write error file: {write_exc})",
            file=sys.stderr,
        )
        try:
            logger.warning(
                "[durable_error] Failed to write %s: %s",
                error_path,
                write_exc,
            )
        except Exception:  # noqa: BLE001
            pass
