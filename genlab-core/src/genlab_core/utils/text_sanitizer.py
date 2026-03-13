"""Sanitize text for Microsoft Graph API SharePoint field values.

Non-BMP Unicode characters (emoji, U+10000+) and curly apostrophes
(U+2019) cause ``badArgument`` errors when writing to SharePoint via
Microsoft Graph.  The functions here strip or replace those characters
so that *all* string values reaching Graph are safe.

The primary integration point is ``GraphTableProxy._prepare_fields()``.
Individual ``push_to_backlog`` stages apply ``sanitize_for_graph_api``
to the ``title`` field as defense-in-depth.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Characters above U+FFFF (non-BMP): emoji, obscure CJK extensions, etc.
_NON_BMP_RE = re.compile(r"[\U00010000-\U0010FFFF]")

# Curly / smart quotes and apostrophes that Graph rejects
_SMART_QUOTES: Dict[str, str] = {
    "\u2018": "'",   # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",   # RIGHT SINGLE QUOTATION MARK (curly apostrophe)
    "\u201C": '"',   # LEFT DOUBLE QUOTATION MARK
    "\u201D": '"',   # RIGHT DOUBLE QUOTATION MARK
}


def sanitize_for_graph_api(text: Optional[str]) -> Optional[str]:
    """Make a string safe for Microsoft Graph SharePoint field values.

    * Strips non-BMP Unicode (emoji, symbols above U+FFFF)
    * Replaces curly/smart quotes with ASCII equivalents
    * Collapses resulting double-spaces and strips edges

    Returns ``None`` unchanged (callers don't need to guard).
    """
    if text is None:
        return None
    if not isinstance(text, str):
        return text  # type: ignore[return-value]

    # Replace smart quotes first (before BMP strip collapses context)
    for char, replacement in _SMART_QUOTES.items():
        text = text.replace(char, replacement)

    # Strip non-BMP characters (emoji etc.)
    text = _NON_BMP_RE.sub("", text)

    # Collapse any double spaces left by emoji removal
    text = re.sub(r"  +", " ", text).strip()

    return text


def sanitize_fields_for_graph_api(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize all string values in a fields dict for Graph API.

    Non-string values (bool, int, float, None) pass through unchanged.
    """
    sanitized: Dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, str):
            sanitized[key] = sanitize_for_graph_api(value)
        else:
            sanitized[key] = value
    return sanitized
