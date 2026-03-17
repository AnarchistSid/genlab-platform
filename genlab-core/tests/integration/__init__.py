"""Integration tests for the GenLab pipeline.

These tests verify that pipeline stages compose correctly end-to-end
with all external APIs mocked. They are excluded from normal pytest
runs and only execute with ``pytest -m integration``.
"""
