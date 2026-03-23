"""Persistent event loop for running async coroutines from sync code.

The Microsoft Graph SDK uses httpx (async HTTP) internally. Calling
asyncio.run() repeatedly creates and destroys event loops, which kills
httpx connections tied to the old loop. This module keeps one loop
running on a background daemon thread so connections survive.

Usage:
    from genlab_core.http.async_bridge import run_async

    result = run_async(some_async_function())
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading

_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOOP_LOCK = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent background event loop, creating it lazily."""
    global _LOOP, _LOOP_THREAD
    if _LOOP is not None and _LOOP.is_running():
        return _LOOP
    with _LOOP_LOCK:
        if _LOOP is not None and _LOOP.is_running():
            return _LOOP
        if _LOOP is not None:
            try:
                _LOOP.close()
            except Exception:
                pass
        _LOOP = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(_LOOP)
            _LOOP.run_forever()

        _LOOP_THREAD = threading.Thread(target=_run_loop, daemon=True)
        _LOOP_THREAD.start()
        return _LOOP


def _shutdown_loop() -> None:
    """Stop the background event loop on process exit."""
    if _LOOP is not None and _LOOP.is_running():
        _LOOP.call_soon_threadsafe(_LOOP.stop)


import atexit
atexit.register(_shutdown_loop)


def run_async(coro, timeout: float = 60.0):
    """Run an async coroutine synchronously via the persistent loop.

    Works from any context: plain scripts, Flask, Jupyter, etc.
    The persistent loop keeps the Graph SDK's httpx connections alive.
    """
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise TimeoutError(
            f"Async call timed out after {timeout}s"
        ) from None
