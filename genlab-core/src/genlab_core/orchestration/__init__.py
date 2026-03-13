"""Gen Lab orchestration layer — Prefect 3.x integration.

Provides shared infrastructure for wrapping niche pipelines as Prefect
flows with per-stage task observability, deployment management, and
a REST API helper for dashboard polling.

All Prefect imports are lazy so this package doesn't break when
Prefect isn't installed.
"""
