from __future__ import annotations

import pytest
from flask import Flask
from genlab_core.auth.models import Permission
from server.middleware.auth import AuthMiddleware


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def test_single_admin_passthrough(app):
    auth = AuthMiddleware(mode="single_admin")

    @app.route("/test")
    @auth.require_permission(Permission.ADMIN)
    def protected():
        return "OK"

    with app.test_client() as client:
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.data == b"OK"


def test_multi_team_rejects_without_token(app):
    auth = AuthMiddleware(mode="multi_team")

    @app.route("/test")
    @auth.require_permission(Permission.PUBLISHER)
    def protected():
        return "OK"

    with app.test_client() as client:
        resp = client.get("/test")
        assert resp.status_code == 401
