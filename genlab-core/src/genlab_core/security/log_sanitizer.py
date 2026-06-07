"""Redact credentials before they hit logs.

Originally lived as ``_scrub_token`` inside :mod:`genlab_core.engagement.poller`
after PR #65 — which was added because ``requests.HTTPError.__str__()`` puts
the failing URL (including the ``access_token=`` query string) into the
exception message, and every poll-loop ``logger.warning('... %s', e)`` was
silently emitting live bearer tokens into journalctl for 10 minutes at a
clip per niche.

Moved here as part of refactor #6 from the senior-engineer review so the
same defensive layer is one import away for any other HTTP caller — the
poller is not the only place this pattern can recur (any
``logger.warning('%s', e)`` after a 4xx on a URL-tokened API risks the
same leak).

Public surface: :func:`scrub_token`. Single-purpose, no I/O, no external
deps — safe to import from anywhere.
"""

from __future__ import annotations

import re
from typing import Final

# Match common in-URL credential query-string parameters. The regex is
# intentionally permissive on the credential value (anything up to the next
# ``&``, whitespace, or quote) so it catches whatever shape Meta / Google /
# Twitter / Threads pick for their token format.
_TOKEN_QUERY_RE: Final[re.Pattern[str]] = re.compile(
    r"(access_token|key|api_key|bearer)=[^&\s'\"]+",
    re.IGNORECASE,
)


def scrub_token(msg: object) -> str:
    """Redact ``access_token=...`` (and a few common variants) from ``msg``.

    Pass any object (an exception, a URL string, a formatted log line) —
    the result is its ``str()`` representation with credential query-string
    values replaced by ``<REDACTED>``. The parameter names are preserved so
    log lines stay legible (you can still see *which* credential failed,
    just not its value).

    Examples::

        scrub_token("...?access_token=ABC&other=safe")
        # → "...?access_token=<REDACTED>&other=safe"

        try:
            resp.raise_for_status()  # noqa: F821
        except requests.HTTPError as e:  # noqa: F821
            logger.warning("API call failed: %s", scrub_token(e))
            # The failing URL in str(e) is sanitised before reaching the log.
    """
    return _TOKEN_QUERY_RE.sub(lambda m: f"{m.group(1)}=<REDACTED>", str(msg))
