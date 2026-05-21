"""Dashboard test configuration — use SharePoint mocks, not live Postgres."""

import os

# Disable Postgres so dashboard tests use their SharePoint mocks
os.environ.pop("DATABASE_URL", None)
os.environ.pop("GENLAB_USE_POSTGRES", None)
