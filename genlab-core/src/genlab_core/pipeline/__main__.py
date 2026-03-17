"""Enable ``python -m genlab_core.pipeline`` execution.

Delegates to the unified CLI entry point.
"""
from genlab_core.pipeline.cli import main

raise SystemExit(main())
