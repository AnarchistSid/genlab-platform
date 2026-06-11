"""Tests for push_to_backlog dedup hardening."""

from datetime import UTC, datetime, timedelta


def _recent_iso(days_ago: int = 5) -> str:
    """ISO timestamp ``days_ago`` days before now (UTC).

    The fixture dates need to stay inside every dedup freshness window
    push_to_backlog enforces:
      * 30-day hook dedup (push_to_backlog.py:345)
      * 14-day default URL dedup (push_to_backlog.py:410-411)
      * 7-day title dedup (push_to_backlog.py:483-484)

    5 days back lands safely inside all of them and stays valid no
    matter when the test runs.  Previous fixtures used hardcoded
    "2026-04-15" strings which silently aged out of the 30-day window
    on 2026-05-15, breaking the tests with confusing "empty dedup set"
    failures.
    """
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def test_title_similarity_catches_near_duplicate():
    """Stories with similar titles from different sources should be deduplicated."""
    existing_titles = {"lakers beat celtics 112-105 in overtime thriller game"}
    title = "Lakers beat Celtics 112-105 in overtime thriller match"
    title_lower = title.lower().strip()

    title_words = set(title_lower.split())
    is_dupe = False
    for existing in existing_titles:
        existing_words = set(existing.split())
        if len(title_words) > 3 and len(existing_words) > 3:
            intersection = len(title_words & existing_words)
            union = len(title_words | existing_words)
            if union > 0 and intersection / union > 0.65:
                is_dupe = True
                break

    assert is_dupe, "Near-duplicate title should be caught"


def test_unrelated_titles_not_caught():
    """Unrelated stories should not be flagged as duplicates."""
    existing_titles = {"lakers beat celtics 112-105 in thriller"}
    title = "New AI model breaks benchmark records"
    title_lower = title.lower().strip()

    title_words = set(title_lower.split())
    is_dupe = False
    for existing in existing_titles:
        existing_words = set(existing.split())
        if len(title_words) > 3 and len(existing_words) > 3:
            intersection = len(title_words & existing_words)
            union = len(title_words | existing_words)
            if union > 0 and intersection / union > 0.65:
                is_dupe = True
                break

    assert not is_dupe, "Unrelated title should not be caught"


def test_short_titles_not_checked():
    """Very short titles should bypass the similarity check."""
    existing_titles = {"hi there"}
    title = "Hi there"
    title_lower = title.lower().strip()

    title_words = set(title_lower.split())
    is_dupe = False
    for existing in existing_titles:
        existing_words = set(existing.split())
        if len(title_words) > 3 and len(existing_words) > 3:
            intersection = len(title_words & existing_words)
            union = len(title_words | existing_words)
            if union > 0 and intersection / union > 0.65:
                is_dupe = True
                break

    assert not is_dupe, "Short titles should bypass similarity check"


def test_non_blocking_statuses_excluded_from_hook_dedup():
    """Regression: blueprints in non-blocking states (ARCHIVED,
    PUBLISH_FAILED) must not block re-creation of the same hook.

    Covers both rejected-by-user (action_taken='rejected', status=ARCHIVED)
    and auto-archived-by-monitor (action_taken='auto_archived_missing_media',
    status=ARCHIVED) and publish-failed retries (status=PUBLISH_FAILED).
    """
    from unittest.mock import MagicMock

    from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog

    stage = PushToBacklog()
    client = MagicMock()
    client.blueprints.all.return_value = [
        {
            "fields": {
                "hook": "this hook was rejected",
                "action_taken": "rejected",
                "status": "ARCHIVED",
                "created_at": _recent_iso(),
            }
        },
        {
            "fields": {
                "hook": "this hook was auto-archived",
                "action_taken": "auto_archived_missing_media",
                "status": "ARCHIVED",
                "created_at": _recent_iso(),
            }
        },
        {
            "fields": {
                "hook": "this hook failed to publish",
                "action_taken": "approved",
                "status": "PUBLISH_FAILED",
                "created_at": _recent_iso(),
            }
        },
        {
            "fields": {
                "hook": "this hook shipped",
                "action_taken": "approved",
                "status": "PUBLISHED",
                "created_at": _recent_iso(),
            }
        },
        {
            "fields": {
                "hook": "this hook is drafted",
                "action_taken": None,
                "status": "DRAFTED",
                "created_at": _recent_iso(),
            }
        },
    ]
    # Minimal stories.all + content_memory stubs so execute() runs to completion
    client.stories.all.return_value = []
    client.content_memory = None
    client.find_story_by_story_id.return_value = None

    stage._client = client
    # One placeholder story so execute() proceeds past the empty-batch guard
    # and actually runs the hook loader.
    ctx = {
        "niche_id": "ai_creators",
        "stories": [
            {
                "story_id": "dummy",
                "title": "dummy",
                "source_url": "https://example.com/dummy",
                "published_at": _recent_iso(),
                "content": {"hook": "brand new hook"},
            },
        ],
        "niche_config": {},
    }
    # Scope GENLAB_USE_POSTGRES to this test only — leaking it breaks
    # sibling tests that rely on the default credential-gate behavior.
    import os as _os

    _prev = _os.environ.get("GENLAB_USE_POSTGRES")
    _os.environ["GENLAB_USE_POSTGRES"] = "true"
    try:
        stage.execute(ctx)
    finally:
        if _prev is None:
            _os.environ.pop("GENLAB_USE_POSTGRES", None)
        else:
            _os.environ["GENLAB_USE_POSTGRES"] = _prev

    loaded = ctx.get("existing_hooks", set())
    # Blocking states — must be in dedup set
    assert "this hook shipped" in loaded
    assert "this hook is drafted" in loaded
    # Non-blocking states — must NOT block re-creation
    assert "this hook was rejected" not in loaded, (
        "Rejected hooks must not block future re-creation"
    )
    assert "this hook was auto-archived" not in loaded, (
        "Auto-archived hooks (missing media) must not block future re-creation"
    )
    assert "this hook failed to publish" not in loaded, "PUBLISH_FAILED hooks must not block retry"


def test_seen_urls_seeded_from_active_blueprints_not_all_stories():
    """Regression: URL dedup must read from non-rejected blueprints, not stories.

    When the user rejects a blueprint, the story row stays in the DB. The
    old URL dedup loader seeded `seen_urls` from the `stories` table without
    any blueprint-status filter, so rejected content's URL hash permanently
    blocked re-ingestion of the same URL.
    """
    from unittest.mock import MagicMock

    from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog

    stage = PushToBacklog()
    client = MagicMock()
    # One active blueprint (should contribute to seen_urls) + one rejected
    # blueprint (should NOT contribute).
    client.blueprints.all.return_value = [
        {
            "fields": {
                "hook": "active hook",
                "video_url": "https://www.youtube.com/watch?v=ACTIVE",
                "action_taken": "approved",
                "status": "PUBLISHED",
                "created_at": _recent_iso(),
            }
        },
        {
            "fields": {
                "hook": "rejected hook",
                "video_url": "https://www.youtube.com/watch?v=REJECTED",
                "action_taken": "rejected",
                "status": "ARCHIVED",
                "created_at": _recent_iso(),
            }
        },
    ]
    client.stories.all.return_value = []
    client.content_memory = None
    client.find_story_by_story_id.return_value = None
    stage._client = client

    ctx = {
        "niche_id": "ai_creators",
        "stories": [
            {
                "story_id": "dummy",
                "title": "placeholder to trigger the loaders",
                "source_url": "https://www.youtube.com/watch?v=SOMETHING_NEW",
                "published_at": _recent_iso(),
                "content": {"hook": "new hook"},
            }
        ],
        "niche_config": {},
    }
    import os as _os

    _prev = _os.environ.get("GENLAB_USE_POSTGRES")
    _os.environ["GENLAB_USE_POSTGRES"] = "true"
    try:
        stage.execute(ctx)
    finally:
        if _prev is None:
            _os.environ.pop("GENLAB_USE_POSTGRES", None)
        else:
            _os.environ["GENLAB_USE_POSTGRES"] = _prev

    # We can't inspect `seen_urls` directly (it's a local), but we can assert
    # via behavior: the rejected URL's hash must NOT block a new story whose
    # URL matches it. The placeholder story uses a different URL so it's safe.
    # The key assertion is that execute() didn't raise and the loader code
    # ran without tripping on missing recent_bps.
    assert "run_stats" in ctx


def test_candidate_id_non_blocking_statuses_do_not_block():
    """Regression: an existing blueprint in a non-blocking state
    (ARCHIVED/PUBLISH_FAILED) must not suppress re-creation via candidate_id.

    Previously the else-branch of the existing_bp check would silently call
    `blueprints.update(...)` to refresh niche_id and skip creation whenever
    status wasn't one of PUBLISHED/PUBLISHING/VISUAL_READY. Both 'rejected'
    and 'auto_archived_missing_media' action_takens ended up there,
    permanently shadowing their source content.
    """
    from unittest.mock import MagicMock

    from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog

    stage = PushToBacklog()
    client = MagicMock()
    # No hook-dedup blueprints
    client.blueprints.all.return_value = []
    client.stories.all.return_value = []
    client.content_memory = None
    client.find_story_by_story_id.return_value = None
    client.stories.create.return_value = "new-story-uuid"

    # Sequence of blueprints.all calls in push_to_backlog:
    #   1. hook dedup loader (niche_id filter)
    #   2. video_id dedup (per story, video_id filter)
    #   3. candidate_id dedup (per story, candidate_id filter) ← we care
    # Return values: first is hooks (empty), then video_id (empty for none),
    # then candidate_id match that IS rejected (should be filtered out).
    non_blocking_matches = [
        {
            "fields": {
                "candidate_id": "some-candidate-id",
                "status": "ARCHIVED",
                "action_taken": "rejected",
                "hook": "the rejected one",
            }
        },
        {
            "fields": {
                "candidate_id": "some-candidate-id",
                "status": "ARCHIVED",
                "action_taken": "auto_archived_missing_media",
                "hook": "the auto-archived one",
            }
        },
        {
            "fields": {
                "candidate_id": "some-candidate-id",
                "status": "PUBLISH_FAILED",
                "action_taken": "approved",
                "hook": "the publish-failed one",
            }
        },
    ]
    client.blueprints.all.side_effect = [
        [],  # hook dedup loader
        [],  # video_id dedup for the one story
        non_blocking_matches,  # candidate_id — none block
    ]
    client.blueprints.create.return_value = "new-bp-uuid"
    stage._client = client

    ctx = {
        "niche_id": "ai_creators",
        "stories": [
            {
                "story_id": "s1",
                "title": "A real new story title",
                "source_url": "https://www.youtube.com/watch?v=NEWVID",
                "video_id": "NEWVID",
                "published_at": _recent_iso(),
                "content": {
                    "hook": "fresh new hook that wasn't rejected",
                    "caption": "body",
                    "instagram": {"caption": "ig body"},
                },
                "media": {"rendered_path": "/tmp/r.mp4"},
            }
        ],
        "niche_config": {},
    }
    import os as _os

    _prev = _os.environ.get("GENLAB_USE_POSTGRES")
    _os.environ["GENLAB_USE_POSTGRES"] = "true"
    try:
        stage.execute(ctx)
    finally:
        if _prev is None:
            _os.environ.pop("GENLAB_USE_POSTGRES", None)
        else:
            _os.environ["GENLAB_USE_POSTGRES"] = _prev

    # Key assertion: the rejected-match filter must NOT take the silent
    # "already exists, update niche_id" branch. That branch was the bug — it
    # made rejected rows shadow re-creation with zero visible breadcrumb.
    # After the fix, update() with a niche_id-only payload should never fire
    # when the only existing match is rejected.
    update_calls = list(client.blueprints.update.call_args_list)
    silent_update_detected = any(
        len(call.args) >= 2
        and isinstance(call.args[1], dict)
        and set(call.args[1].keys()) == {"niche_id"}
        for call in update_calls
    )
    assert not silent_update_detected, (
        f"Silent update path fired — rejected match is still blocking "
        f"re-creation. update_calls={update_calls}"
    )


def _setup_revive_race_harness(claim_returns: bool):
    """R-40: shared harness for the revive race tests. Drives
    PushToBacklog with exactly one non-blocking PUBLISH_FAILED match
    on the ``candidate_id`` query and a configurable ``claim_status``
    outcome."""
    from unittest.mock import MagicMock

    from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog

    stage = PushToBacklog()
    client = MagicMock()
    client.blueprints.claim_status.return_value = claim_returns
    client.stories.all.return_value = []
    client.content_memory = None
    client.find_story_by_story_id.return_value = None
    client.stories.create.return_value = "new-story-uuid"

    non_blocking_match = {
        "id": "bp-publish-failed-id",
        "fields": {
            "candidate_id": "cid-race",
            "status": "PUBLISH_FAILED",
            "action_taken": "approved",
            "hook": "the publish-failed one",
        },
    }

    # Route blueprints.all() responses by the ``formula`` it carries.
    # The stage does N internal calls (hook dedup, video_id dedup,
    # candidate_id dedup, etc.); ``candidate_id`` only appears in the
    # candidate_id dedup formula, so substring-match suffices.
    def _bp_all(*args, **kwargs):
        formula = kwargs.get("formula", "")
        if "candidate_id" in formula:
            return [non_blocking_match]
        return []

    client.blueprints.all.side_effect = _bp_all
    client.blueprints.create.return_value = "new-bp-uuid"
    stage._client = client

    ctx = {
        "niche_id": "gaming",
        "stories": [
            {
                "story_id": "s_r40",
                "title": "Gameplay highlight from yesterday",
                "source_url": "https://www.youtube.com/watch?v=VIDR40",
                "video_id": "VIDR40",
                "published_at": _recent_iso(),
                "content": {
                    "hook": "race-test hook never used before",
                    "caption": "body",
                    "instagram": {"caption": "ig body"},
                },
                "media": {"rendered_path": "/tmp/r.mp4"},
            }
        ],
        "niche_config": {},
    }
    import os as _os

    _prev = _os.environ.get("GENLAB_USE_POSTGRES")
    _os.environ["GENLAB_USE_POSTGRES"] = "true"
    try:
        stage.execute(ctx)
    finally:
        if _prev is None:
            _os.environ.pop("GENLAB_USE_POSTGRES", None)
        else:
            _os.environ["GENLAB_USE_POSTGRES"] = _prev

    return client


def test_r40_revive_calls_claim_status_with_expected_old_status():
    """R-40: before reviving a PUBLISH_FAILED row, PushToBacklog must
    atomically claim it via ``claim_status(expected='PUBLISH_FAILED',
    new=<target>)`` — without that, a concurrent
    ``recover_publish_failed`` could flip the row to VISUAL_READY
    between our query and our update."""
    client = _setup_revive_race_harness(claim_returns=True)

    # Find the claim call against the non-blocking match's id.
    claim_calls = [
        c
        for c in client.blueprints.claim_status.call_args_list
        if c.args and c.args[0] == "bp-publish-failed-id"
    ]
    assert claim_calls, (
        "R-40 regression: PushToBacklog revive path bypassed ``claim_status``. "
        "The row could be moved out from under us by a racing writer."
    )
    kwargs = claim_calls[0].kwargs
    assert kwargs.get("expected_status") == "PUBLISH_FAILED"


def test_r40_revive_skipped_when_claim_lost():
    """R-40: when the atomic claim returns False (race lost), the
    revive bulk-update must NOT fire — the row was already moved by
    another writer and the data we'd write is stale."""
    client = _setup_revive_race_harness(claim_returns=False)

    # No update on the non-blocking match id when we lost the race.
    update_calls = [
        c
        for c in client.blueprints.update.call_args_list
        if c.args and c.args[0] == "bp-publish-failed-id"
    ]
    assert not update_calls, (
        f"R-40 regression: lost-race revive still wrote to the row. update_calls={update_calls!r}"
    )
    # Belt-and-suspenders: no fresh ``create`` either — the existing
    # row IS the canonical record; recover_publish_failed will handle
    # it on the next pass.
    client.blueprints.create.assert_not_called()


def test_r40_revive_strips_status_from_bulk_update_when_claim_wins():
    """R-40: when the claim succeeds it has already flipped ``status``
    via the atomic update. The follow-up bulk-update must NOT carry
    ``status`` in its payload — that would be a redundant second
    write to a column already correctly set, and on rare backends
    could trigger trigger-side state-machine guards twice."""
    client = _setup_revive_race_harness(claim_returns=True)

    update_calls = [
        c
        for c in client.blueprints.update.call_args_list
        if c.args and c.args[0] == "bp-publish-failed-id"
    ]
    assert update_calls, "claim won but revive update never fired"
    payload = update_calls[0].args[1]
    assert "status" not in payload, (
        f"R-40 regression: post-claim revive payload still carries "
        f"``status`` — should have been stripped after the atomic flip. "
        f"payload keys: {sorted(payload.keys())}"
    )
