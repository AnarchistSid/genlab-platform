"""LLM error classification — assigns a short reason string to any
exception raised by Anthropic / OpenAI SDKs (or their fallback path).

Motivating pattern: at direct-call sites like PersonaEngine, LLM judge,
or the writer, when the SDK raises, the caller usually catches and
returns None. Downstream can't tell WHICH LLM failure mode occurred
without inspecting the exception class + str(e). This module gives
those sites a single ``classify_llm_error(exc)`` function that returns
one of the enumerated reason strings — safe to log, safe to store,
safe to grep.

See [[class-of-bug-signal-loss-through-merged-failure-paths]] — same
class as ``format_meta_error`` for Meta APIs but scoped to LLM providers.
"""

from __future__ import annotations

# Categories used by callers. Kept as string constants so grep against
# journals + DB columns finds them regardless of Python version.
LLM_ERROR_CIRCUIT_OPEN = "circuit_open"
LLM_ERROR_CREDIT_EXHAUSTED = "credit_exhausted"
LLM_ERROR_RATE_LIMIT = "rate_limit"
LLM_ERROR_AUTH = "auth"
LLM_ERROR_INVALID_REQUEST = "invalid_request"
LLM_ERROR_TIMEOUT = "timeout"
LLM_ERROR_CONNECTION = "connection"
LLM_ERROR_OVERLOADED = "overloaded"
LLM_ERROR_CONTENT_FILTER = "content_filter"
LLM_ERROR_UNKNOWN = "unknown"


# Exception class-name → category. Matches SDK exception class *names*
# (not their runtime types) so we don't import anthropic / openai here
# — this file must load in environments where either SDK is absent.
_CLASS_NAME_TO_CATEGORY: dict[str, str] = {
    # Anthropic SDK
    "RateLimitError": LLM_ERROR_RATE_LIMIT,
    "AuthenticationError": LLM_ERROR_AUTH,
    "PermissionDeniedError": LLM_ERROR_AUTH,
    "NotFoundError": LLM_ERROR_INVALID_REQUEST,
    "APIConnectionError": LLM_ERROR_CONNECTION,
    "APITimeoutError": LLM_ERROR_TIMEOUT,
    "InternalServerError": LLM_ERROR_UNKNOWN,
    # OpenAI SDK (shares many names with Anthropic)
    "APIError": LLM_ERROR_UNKNOWN,
    "APIStatusError": LLM_ERROR_UNKNOWN,
    "BadRequestError": LLM_ERROR_INVALID_REQUEST,
    # Ours
    "CircuitOpen": LLM_ERROR_CIRCUIT_OPEN,
    "CircuitOpenError": LLM_ERROR_CIRCUIT_OPEN,
}


# String markers in the exception message that ESCALATE the category
# beyond the class-name mapping. e.g. a 400 BadRequestError with
# "credit balance too low" is really credit exhaustion, not a generic
# invalid request.
#
# Order matters: first match wins.
_MESSAGE_MARKERS: tuple[tuple[str, str], ...] = (
    # Credit exhaustion (Anthropic + OpenAI variants)
    ("credit balance is too low", LLM_ERROR_CREDIT_EXHAUSTED),
    ("credit balance too low", LLM_ERROR_CREDIT_EXHAUSTED),
    ("insufficient credits", LLM_ERROR_CREDIT_EXHAUSTED),
    ("insufficient_quota", LLM_ERROR_CREDIT_EXHAUSTED),
    ("you have exceeded your quota", LLM_ERROR_CREDIT_EXHAUSTED),
    # Rate limit (both providers)
    ("rate_limit_error", LLM_ERROR_RATE_LIMIT),
    ("rate limit exceeded", LLM_ERROR_RATE_LIMIT),
    # Content filter (Anthropic + OpenAI safety)
    ("content_policy_violation", LLM_ERROR_CONTENT_FILTER),
    ("content filter", LLM_ERROR_CONTENT_FILTER),
    # Overloaded (Anthropic 529)
    ("overloaded_error", LLM_ERROR_OVERLOADED),
    # Timeout messages that come through Exception (not APITimeoutError)
    ("read timed out", LLM_ERROR_TIMEOUT),
    ("connection timeout", LLM_ERROR_TIMEOUT),
    # Auth (message-level in case the SDK returns a generic APIError)
    ("401 unauthorized", LLM_ERROR_AUTH),
    ("invalid api key", LLM_ERROR_AUTH),
)


def classify_llm_error(exc: Exception) -> str:
    """Return the reason category string for an LLM SDK exception.

    Callers should pass this into their own attribution field
    (e.g. ``self._last_error_reason``, ``error_message``,
    ``pending_engagement.extra.error``). Downstream can filter by
    category to distinguish "provider is broken" from "content was
    rejected" from "our circuit tripped".

    Never raises. Unknown exceptions map to ``LLM_ERROR_UNKNOWN``.

    Examples:
        >>> classify_llm_error(RuntimeError("credit balance is too low"))
        'credit_exhausted'
        >>> classify_llm_error(RuntimeError("something else"))
        'unknown'
    """
    if exc is None:
        return LLM_ERROR_UNKNOWN

    # First check the exception message — markers can escalate a
    # generic class (e.g. BadRequestError) to a specific category
    # (credit_exhausted).
    msg = str(exc).lower()
    for marker, category in _MESSAGE_MARKERS:
        if marker.lower() in msg:
            return category

    # Fall back to class-name mapping.
    class_name = type(exc).__name__
    return _CLASS_NAME_TO_CATEGORY.get(class_name, LLM_ERROR_UNKNOWN)
