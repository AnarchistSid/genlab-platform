"""Allow running credential check via: python -m genlab_core.tools"""

from __future__ import annotations

import sys

from genlab_core.tools.credential_check import main

sys.exit(main())
