"""Thin REST client for querying the Prefect 3.x server API.

Used by the dashboard backend polling thread. Avoids pulling in the full
Prefect SDK (which requires async) — just plain httpx against the REST API.

Usage:
    from genlab_core.orchestration.prefect_api import PrefectRESTClient

    client = PrefectRESTClient()
    runs = client.list_flow_runs(limit=20)
    run = client.get_flow_run("run-uuid")
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "http://127.0.0.1:4200/api"


class PrefectRESTClient:
    """Synchronous REST client for the Prefect server API."""

    def __init__(self, api_url: str | None = None):
        self._api_url = (
            api_url
            or os.environ.get("PREFECT_API_URL")
            or _DEFAULT_API_URL
        ).rstrip("/")
        self._session: Any = None

    def _get_session(self) -> Any:
        if self._session is None:
            import httpx
            self._session = httpx.Client(
                base_url=self._api_url,
                timeout=10.0,
            )
        return self._session

    def healthy(self) -> bool:
        """Check if the Prefect server is reachable."""
        try:
            resp = self._get_session().get("/health")
            return resp.status_code == 200
        except Exception:
            return False

    def list_flow_runs(
        self,
        *,
        limit: int = 20,
        flow_name: str | None = None,
        state_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List recent flow runs, newest first.

        Args:
            limit: Max runs to return.
            flow_name: Filter by flow name (optional).
            state_type: Filter by state type, e.g. "COMPLETED" (optional).
        """
        body: dict[str, Any] = {
            "sort": "EXPECTED_START_TIME_DESC",
            "limit": limit,
        }

        filters: dict[str, Any] = {}
        if flow_name:
            # Need flow ID first
            flow_id = self._get_flow_id(flow_name)
            if flow_id:
                filters["flow_runs"] = {
                    "flow_id": {"any_": [flow_id]}
                }
        if state_type:
            filters.setdefault("flow_runs", {})["state"] = {
                "type": {"any_": [state_type]}
            }
        if filters:
            body["flow_runs"] = filters.get("flow_runs", {})

        try:
            resp = self._get_session().post("/flow_runs/filter", json=body)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("[PrefectAPI] list_flow_runs failed: %s", e)
            return []

    def get_flow_run(self, run_id: str) -> dict[str, Any] | None:
        """Get a single flow run by ID."""
        try:
            resp = self._get_session().get(f"/flow_runs/{run_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("[PrefectAPI] get_flow_run failed: %s", e)
            return None

    def get_task_runs_for_flow(self, flow_run_id: str) -> list[dict[str, Any]]:
        """Get all task runs belonging to a flow run."""
        body = {
            "task_runs": {
                "flow_run_id": {"any_": [flow_run_id]}
            },
            "sort": "EXPECTED_START_TIME_ASC",
            "limit": 50,
        }
        try:
            resp = self._get_session().post("/task_runs/filter", json=body)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("[PrefectAPI] get_task_runs failed: %s", e)
            return []

    def trigger_deployment(
        self,
        deployment_name: str,
        *,
        parameters: dict[str, Any] | None = None,
    ) -> str | None:
        """Trigger a deployment run, return flow_run_id or None."""
        dep_id = self._get_deployment_id(deployment_name)
        if not dep_id:
            logger.error("[PrefectAPI] Deployment '%s' not found", deployment_name)
            return None

        body: dict[str, Any] = {}
        if parameters:
            body["parameters"] = parameters

        try:
            resp = self._get_session().post(
                f"/deployments/{dep_id}/create_flow_run", json=body
            )
            resp.raise_for_status()
            return resp.json().get("id")
        except Exception as e:
            logger.error("[PrefectAPI] trigger_deployment failed: %s", e)
            return None

    def _get_flow_id(self, flow_name: str) -> str | None:
        """Look up a flow ID by name."""
        body = {
            "flows": {"name": {"any_": [flow_name]}},
            "limit": 1,
        }
        try:
            resp = self._get_session().post("/flows/filter", json=body)
            resp.raise_for_status()
            flows = resp.json()
            return flows[0]["id"] if flows else None
        except Exception:
            return None

    def _get_deployment_id(self, deployment_name: str) -> str | None:
        """Look up a deployment ID by name (format: 'flow-name/deployment-name')."""
        body = {
            "deployments": {"name": {"any_": [deployment_name.split("/")[-1]]}},
            "limit": 5,
        }
        try:
            resp = self._get_session().post("/deployments/filter", json=body)
            resp.raise_for_status()
            deps = resp.json()
            if not deps:
                return None
            # If deployment_name has flow prefix, match it
            if "/" in deployment_name:
                flow_prefix = deployment_name.split("/")[0]
                for dep in deps:
                    flow_id = dep.get("flow_id", "")
                    flow = self.get_flow_by_id(flow_id)
                    if flow and flow.get("name") == flow_prefix:
                        return dep["id"]
            return deps[0]["id"]
        except Exception:
            return None

    def get_flow_by_id(self, flow_id: str) -> dict[str, Any] | None:
        """Get flow metadata by ID."""
        try:
            resp = self._get_session().get(f"/flows/{flow_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None
