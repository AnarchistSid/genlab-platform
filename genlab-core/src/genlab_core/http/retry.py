"""Simple retry decorator for HTTP and API calls.

Usage:
    from genlab_core.http.retry import retry

    @retry(max_attempts=3, backoff=2.0, exceptions=(TimeoutError, ConnectionError))
    def call_api():
        ...
"""

from __future__ import annotations

import functools
import logging
import time

logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    backoff: float = 2.0,
    initial_delay: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
):
    """Decorator: retry a function on failure with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including the first).
        backoff: Multiplier applied to delay after each failure.
        initial_delay: Seconds to wait before the first retry.
        exceptions: Tuple of exception types that trigger a retry.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    logger.warning(
                        "%s attempt %d/%d failed: %s — retrying in %.1fs",
                        fn.__qualname__,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= backoff
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
