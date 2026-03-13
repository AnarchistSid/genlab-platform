"""Human-like timing jitter for engagement replies."""
from __future__ import annotations

import random


def human_delay() -> float:
    """Compute a human-like delay using log-normal distribution.

    Log-normal with mu=2.5, sigma=0.8 produces:
      median ~ 12 seconds
      mean   ~ 20 seconds
      95th%  ~ 55 seconds

    Hard-capped at 120s to prevent outliers.
    """
    return min(random.lognormvariate(mu=2.5, sigma=0.8), 120.0)
