"""External-transport integrations.

Purpose-built adapters for third-party APIs the platform needs to
push OUT to (rather than the read-heavy adapters in http/). Each
adapter wraps its own auth + rate-limit + fail-open semantics so
callers don't need to hand-roll those concerns.

Modules:

  * ``outlook_sender`` — Phase 3.C session 2 (2026-08-14). Sends
    sponsorship-outreach emails via Microsoft Graph
    ``users/{upn}/sendMail`` using the existing Azure app's client
    credentials (same tenant/client/secret as ``BacklogClient``).
    Requires ``Mail.Send`` app permission.
"""
