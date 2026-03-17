"""genlab_core.http — HTTP/Graph API client infrastructure.

Modules:
    async_bridge     — Persistent event loop for sync-over-async Graph SDK calls.
    graph_proxy      — Generic Microsoft Graph Lists CRUD proxy.
    backlog_client   — Domain-specific backlog client for content workflows.
    retry            — Simple retry decorator for HTTP calls.
    circuit_breaker  — Shared circuit breaker + @resilient decorator.
"""
