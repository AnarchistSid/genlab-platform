"""Meta API error envelope extraction — shared across IG/FB/Threads.

Meta's Graph API returns errors under the ``error`` key with fields:
* ``message`` — human-readable summary (often generic like "An unknown
  error has occurred.")
* ``code`` — numeric error code (e.g. 190 = invalid token, 32 = rate
  limit, 1 = unknown)
* ``error_subcode`` — finer-grained sub-classification
* ``fbtrace_id`` — request-specific trace ID that Meta support can
  use to look up the exact request in their logs

Pre-2026-07-23 all three Meta clients (IG/FB/Threads) discarded
everything but ``message`` when serializing failures. 3 IG rows in
prod wrote "media_publish failed: An unknown error has occurred."
with zero attribution — impossible to distinguish transient
media-processing hiccup from token invalidation from rate limit.

This module preserves the extra fields as a grep-friendly suffix
"[code=X, subcode=Y, fbtrace_id=Z]" appended to the message.
Absent fields are omitted so no "[]" leak.

See also:
* [[class-of-bug-signal-loss-through-merged-failure-paths]]
"""

from __future__ import annotations

from typing import Any


def format_meta_error(payload: Any) -> str:
    """Extract Meta's error envelope into an attributed string.

    Args:
        payload: The parsed JSON body from a Meta API response. Should
            be a dict but tolerant of non-dict for defensiveness (the
            fallback is ``str(payload)`` which is what the pre-2026-07-23
            code did).

    Returns:
        String of the form ``"<message> [code=X, subcode=Y, fbtrace_id=Z]"``
        with only the present suffix fields. If ``payload`` has no
        ``error`` key, returns ``str(payload)``.

    Examples:
        >>> format_meta_error({"error": {
        ...     "message": "Unknown error",
        ...     "code": 1,
        ...     "error_subcode": 2207032,
        ...     "fbtrace_id": "Abc123",
        ... }})
        'Unknown error [code=1, subcode=2207032, fbtrace_id=Abc123]'

        >>> format_meta_error({"error": {"message": "Just a message"}})
        'Just a message'

        >>> format_meta_error({"unexpected": "shape"})
        "{'unexpected': 'shape'}"
    """
    if not isinstance(payload, dict):
        return str(payload)
    error = payload.get("error")
    if not isinstance(error, dict):
        return str(payload)

    message = error.get("message", "") or str(payload)
    parts: list[str] = []
    code = error.get("code")
    if code is not None:
        parts.append(f"code={code}")
    subcode = error.get("error_subcode")
    if subcode is not None:
        parts.append(f"subcode={subcode}")
    fbtrace = error.get("fbtrace_id")
    if fbtrace:
        parts.append(f"fbtrace_id={fbtrace}")

    if parts:
        return f"{message} [{', '.join(parts)}]"
    return message
