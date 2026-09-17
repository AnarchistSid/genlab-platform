"""Per-niche scheduling horizon, and the two implementers that must agree.

T-53 recorded the lookahead as a hardcoded range(0, 8) in BOTH
auto_approver.py and dashboard/server/core/publishing_queue.py -- one contract,
two implementers. These pins fail if either goes back to a literal.
"""

import pathlib
import re

import pytest
from genlab_core.scheduling.queue_policy import DEFAULT_LOOKAHEAD_DAYS, lookahead_days


def test_anime_is_five_days(monkeypatch):
    monkeypatch.delenv("GENLAB_QUEUE_LOOKAHEAD_DAYS", raising=False)
    assert lookahead_days("anime") == 5


@pytest.mark.parametrize("niche", ["ai_creators", "gaming", "movies", "sports"])
def test_other_niches_keep_current_behaviour(niche, monkeypatch):
    """Only anime was decided. Shortening the rest would silently expire work."""
    monkeypatch.delenv("GENLAB_QUEUE_LOOKAHEAD_DAYS", raising=False)
    assert lookahead_days(niche) == DEFAULT_LOOKAHEAD_DAYS == 8


def test_unknown_niche_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("GENLAB_QUEUE_LOOKAHEAD_DAYS", raising=False)
    assert lookahead_days("brand_new_channel") == 8
    assert lookahead_days(None) == 8


def test_env_override_wins_for_every_niche(monkeypatch):
    monkeypatch.setenv("GENLAB_QUEUE_LOOKAHEAD_DAYS", "3")
    assert lookahead_days("anime") == 3 and lookahead_days("gaming") == 3


@pytest.mark.parametrize("bad", ["0", "abc", "-2", "999", ""])
def test_a_bad_override_is_ignored_not_obeyed(bad, monkeypatch):
    """A lookahead of 0 would stop all scheduling. Refuse it loudly, don't take it."""
    monkeypatch.setenv("GENLAB_QUEUE_LOOKAHEAD_DAYS", bad)
    assert lookahead_days("anime") == 5


def _src(rel: str) -> str:
    root = pathlib.Path(__file__).resolve().parents[3]
    return (root / rel).read_text()


@pytest.mark.parametrize("rel", [
    "genlab-core/src/genlab_core/scheduling/auto_approver.py",
    "dashboard/server/core/publishing_queue.py",
])
def test_neither_implementer_hardcodes_the_horizon(rel):
    src = _src(rel)
    assert not re.search(r"range\(\s*0\s*,\s*8\s*\)", src), (
        f"{rel} went back to a literal horizon; T-53 says both implementers "
        f"move together, so a literal here silently desyncs the dashboard from "
        f"the approver")
    assert "lookahead_days" in src, f"{rel} does not read the shared policy"
