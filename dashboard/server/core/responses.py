"""Standardized API response helpers.

All dashboard endpoints should use these instead of raw jsonify().
"""
from __future__ import annotations

from flask import jsonify


def api_success(data=None, message="OK", code=200):
    return jsonify({"status": "success", "code": code, "data": data, "message": message}), code


def api_error(error=None, message="Request failed", code=400):
    return jsonify({
        "status": "error",
        "code": code,
        "error": str(error) if error else None,
        "message": message,
    }), code


def api_not_found(message="Resource not found"):
    return api_error(message=message, code=404)
