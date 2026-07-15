"""Pin test — auto_approver except-handlers must print tracebacks to stderr.

Background — 2026-07-15:
    The auto_approver CLI runs under systemd Type=oneshot with no
    configured Python logging handler. Python falls back to
    ``logging.lastResort`` which uses a hardcoded
    ``"%(levelname)s: %(message)s"`` formatter — tracebacks passed
    via ``exc_info=True`` are silently dropped.

    Result: an ``errors=1`` flap on ai_creators went undiagnosed
    for HOURS because the ``logger.warning(..., exc_info=True)``
    at the gate_evaluate except-handler produced no traceback in
    journalctl. Confirmed by fresh manual fires that hit the
    exception path but showed only the summary line.

Fix pattern (applied to all 5 except-handlers in run_pass +
_approve_blueprint that append to result.errors):

    except Exception as exc:
        import sys, traceback
        result.errors.append(...)
        print(f"... {traceback.format_exc()}",
              file=sys.stderr, flush=True)

Under systemd oneshot, stderr goes directly to journalctl
regardless of logging configuration.

Pins:
    - each except-handler that appends to result.errors ALSO
      contains ``traceback.format_exc()`` + ``file=sys.stderr``
    - regression path: if a future refactor swaps the print for
      logger.warning(exc_info=True), this test fails and the
      author is forced to re-establish the wire (or explicitly
      configure a logging handler that renders tracebacks).
"""

from __future__ import annotations

import inspect

from genlab_core.scheduling import auto_approver


def _get_source() -> str:
    return inspect.getsource(auto_approver)


class TestErrorHandlersPrintTraceback:
    """Each ``result.errors.append(...)`` site must be paired with
    a ``traceback.format_exc()`` + ``file=sys.stderr`` print call.
    """

    def test_source_contains_traceback_format_exc_calls(self):
        """The module MUST import + use traceback.format_exc()."""
        src = _get_source()
        # We expect at least 5 traceback.format_exc() calls — one per
        # except-handler that appends to result.errors:
        #   1. blueprint query failed (line ~723)
        #   2. gate evaluation failed (line ~788)
        #   3. strategy layer error (line ~825)
        #   4. slot lookup failed (line ~962)
        #   5. backlog update failed (line ~1006)
        count = src.count("traceback.format_exc()")
        assert count >= 5, (
            f"expected >= 5 traceback.format_exc() calls in auto_approver.py, "
            f"found {count}. Every except-handler that appends to result.errors "
            "MUST print the traceback to stderr — logger.warning(exc_info=True) "
            "is silent under systemd's logging.lastResort handler."
        )

    def test_source_uses_stderr_for_error_prints(self):
        """The traceback prints MUST target stderr, not stdout.

        systemd routes both to journalctl by default, but keeping
        errors on stderr matches Unix conventions and separates
        the summary-line output (stdout print in _cli) from the
        error traceback stream.
        """
        src = _get_source()
        count = src.count("file=sys.stderr")
        assert count >= 5, (
            f"expected >= 5 file=sys.stderr prints in auto_approver.py, "
            f"found {count}. Error tracebacks should target stderr."
        )

    def test_exception_error_appends_have_traceback_nearby(self):
        """Every ``result.errors.append(...)`` that lives inside an
        ``except`` block must have a ``traceback.format_exc()`` within
        15 lines after it.

        In-band appends (no upstream exception — e.g. "no slot available"
        being a legitimate business condition, not a bug) are exempt
        because there IS no traceback to print. They rely on the
        logger.info that precedes them for observability.

        Guards against future refactors that split the error-append
        from the traceback-print into different code paths, or that
        add a new exception-path error site without wiring traceback
        observability.
        """
        src_lines = _get_source().splitlines()
        error_append_lines = [
            i for i, line in enumerate(src_lines) if "result.errors.append(" in line
        ]
        assert error_append_lines, "no result.errors.append() sites found — did the module rename?"

        for line_no in error_append_lines:
            # Determine whether this append is inside an ``except`` block
            # by scanning up to 10 lines backward for ``except``. If we
            # hit an ``if``/``elif``/``else`` at the same/lower indent
            # first, or a fresh ``try:`` (start of a new block), then it
            # is NOT inside an except.
            in_except = False
            for back_off in range(1, 11):
                if back_off > line_no:
                    break
                back_line = src_lines[line_no - back_off].strip()
                if back_line.startswith("except "):
                    in_except = True
                    break
                if back_line.startswith(("if ", "elif ", "else:", "try:")):
                    # Different control-flow parent; stop looking.
                    break

            if not in_except:
                # In-band error — no traceback expected. e.g. "no slot
                # available" is a legitimate business condition, not
                # an unexpected exception.
                continue

            # Look up to 15 lines below (the fix pattern places the
            # traceback print immediately after the append)
            window = src_lines[line_no : line_no + 15]
            joined = "\n".join(window)
            assert "traceback.format_exc()" in joined, (
                f"result.errors.append at line {line_no + 1} is inside an "
                "except block but has no traceback.format_exc() within the "
                f"next 15 lines. Line: {src_lines[line_no].strip()!r}. "
                "Every exception-path error-append MUST be paired with a "
                "traceback print — otherwise the failure is undiagnosable "
                "in prod under systemd oneshot (Python's logging.lastResort "
                "strips tracebacks)."
            )
