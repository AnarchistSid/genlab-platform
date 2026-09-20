"""Where GenLab keeps its own state. One resolver, so the path is a variable.

CLEAN-03's rule is "project-owned state lives in the project", and the test of
who owns a directory is who wrote it. ``~/.genlab`` is written by GenLab
scripts -- trend_signals, posting_optimizer, viral_detector, content_memory,
token_health, youtube_quota -- so it is ours.

IT IS ALREADY IN THE RIGHT PLACE ON PROD, BY ACCIDENT OF THE SERVICE USER.
The `genlab` user's home IS ``/opt/genlab``, so ``Path.home() / ".genlab"``
resolves to ``/opt/genlab/.genlab`` -- inside the checkout. It holds a YouTube
quota counter, a token file and the morning briefing, all written today.
Repointing the default would migrate live state, which is a different and
riskier operation than tidying a directory. So the DEFAULT IS UNCHANGED and
this module exists to make the location overridable rather than assumed:

    GENLAB_STATE_DIR=/somewhere    # explicit wins
    else ~/.genlab                 # what every call site hardcoded

That is what makes moving the checkout possible later. Six files each built
the path themselves with `Path.home()`, so "where does state live" had six
answers and no way to change them together.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_STATE_DIR = "GENLAB_STATE_DIR"


def state_dir(*parts: str, create: bool = False) -> Path:
    """The state directory, or a path inside it.

    ``create=True`` makes the directory (not the file's parent -- callers that
    want a file pass its name and get the parent made for them).
    """
    root = Path(os.environ.get(ENV_STATE_DIR, "").strip() or (Path.home() / ".genlab"))
    p = root.joinpath(*parts) if parts else root
    if create:
        (p if not parts or not Path(parts[-1]).suffix else p.parent).mkdir(
            parents=True, exist_ok=True
        )
    return p
