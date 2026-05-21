"""Tests for standardized API response helpers."""

from __future__ import annotations

import json

import pytest
from flask import Flask
from server.core.responses import api_error, api_not_found, api_success


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def test_api_success_default(app):
    with app.app_context():
        response, code = api_success(data={"items": [1, 2, 3]})
        body = json.loads(response.get_data())
        assert code == 200
        assert body["status"] == "success"
        assert body["data"] == {"items": [1, 2, 3]}
        assert body["message"] == "OK"


def test_api_success_custom_message(app):
    with app.app_context():
        response, code = api_success(data=None, message="Created", code=201)
        body = json.loads(response.get_data())
        assert code == 201
        assert body["message"] == "Created"


def test_api_error_default(app):
    with app.app_context():
        response, code = api_error(error="Something broke")
        body = json.loads(response.get_data())
        assert code == 400
        assert body["status"] == "error"
        assert body["error"] == "Something broke"


def test_api_not_found(app):
    with app.app_context():
        response, code = api_not_found()
        body = json.loads(response.get_data())
        assert code == 404
        assert body["message"] == "Resource not found"
