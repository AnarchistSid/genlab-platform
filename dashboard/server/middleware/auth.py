"""Auth middleware — passthrough in single_admin mode, JWT-based in multi_team."""

from __future__ import annotations

from functools import wraps

from flask import request
from genlab_core.auth.models import Permission

from server.core.responses import api_error


class AuthMiddleware:
    def __init__(self, mode: str = "single_admin"):
        self.mode = mode

    def require_permission(self, min_permission: Permission):
        def decorator(f):
            @wraps(f)
            def wrapper(*args, **kwargs):
                if self.mode == "single_admin":
                    return f(*args, **kwargs)
                # multi_team: check JWT
                token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if not token:
                    return api_error(error="Missing auth token", code=401)
                # Future: decode JWT, check team membership, verify permission
                return api_error(error="multi_team auth not yet implemented", code=501)

            return wrapper

        return decorator
