"""Shared pipeline stages for all Gen Lab niches.

Each stage follows the execute(context: dict) -> dict interface
expected by GenericPipelineRunner. Stages are loaded dynamically
via importlib from niche.yaml pipeline.stages declarations.
"""
