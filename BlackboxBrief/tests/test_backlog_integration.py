#!/usr/bin/env python3
"""Integration tests for BacklogClient (Microsoft Lists).

These tests require a live Microsoft Graph connection AND an explicit opt-in
via RUN_INTEGRATION_TESTS=1 environment variable. This prevents accidental
execution against production data during normal test runs.

SAFETY: These tests hit REAL Microsoft Lists (production data). They are
skipped by default during `pytest tests/` runs. To run them explicitly:

    RUN_INTEGRATION_TESTS=1 python -m pytest tests/test_backlog_integration.py -v
    python tests/test_backlog_integration.py   # Interactive mode (always runs)
"""

import os
from datetime import datetime

import pytest
from dotenv import load_dotenv

load_dotenv(override=True)

# SAFETY: Require BOTH credentials AND explicit opt-in.
# This prevents integration tests from ever running during normal `pytest tests/`
# which caused catastrophic data loss when test cleanup deleted all production records.
LISTS_CONFIGURED = bool(
    os.getenv("AZURE_TENANT_ID")
    and os.getenv("AZURE_CLIENT_ID")
    and os.getenv("AZURE_CLIENT_SECRET")
    and os.getenv("SHAREPOINT_SITE_ID")
)
INTEGRATION_OPT_IN = os.getenv("RUN_INTEGRATION_TESTS", "").strip() == "1"

pytestmark = pytest.mark.skipif(
    not (LISTS_CONFIGURED and INTEGRATION_OPT_IN),
    reason=(
        "Integration tests require Azure credentials AND "
        "RUN_INTEGRATION_TESTS=1 env var (safety: prevents accidental "
        "execution against production data)"
    ),
)


@pytest.fixture(scope="module")
def client():
    """Create BacklogClient for tests."""
    from genlab_core.http.backlog_client import BacklogClient
    return BacklogClient()


@pytest.fixture(scope="module")
def test_story_id():
    """Generate a unique test story ID."""
    from genlab_core.cache.stable_ids import generate_story_id
    return generate_story_id(
        f"https://test.example.com/integration-{datetime.now().timestamp()}",
        datetime.now().isoformat()
    )


class TestConnection:
    def test_health_check(self, client):
        assert client.health_check() is True


class TestStories:
    def test_create_story(self, client, test_story_id):
        record_id = client.create_story({
            "story_id": test_story_id,
            "title": "[TEST] Integration Test Story - Safe to Delete",
            "url": "https://test.example.com/integration",
            "source": "Other",
            "published_at": datetime.now().isoformat(),
            "summary": "This is an automated integration test story. Safe to delete.",
            "priority": 0.75,
            "themes": ["llm", "agents"],
            "scores": {"authority": 0.8, "recency": 1.0, "novelty": 0.9},
        })
        assert record_id is not None
        assert len(record_id) > 0

    def test_find_story(self, client, test_story_id):
        found = client.find_story_by_story_id(test_story_id)
        assert found is not None
        assert found["fields"]["story_id"] == test_story_id
        assert "[TEST]" in found["fields"]["title"]

    def test_update_story_status(self, client, test_story_id):
        client.update_story_status(test_story_id, "VALIDATED", priority=0.9)
        found = client.find_story_by_story_id(test_story_id)
        assert found["fields"]["status"] == "VALIDATED"

    def test_duplicate_prevention(self, client, test_story_id):
        """Verify find returns existing record (for idempotent upserts)."""
        found = client.find_story_by_story_id(test_story_id)
        assert found is not None


class TestBlueprints:
    def test_create_blueprint(self, client, test_story_id):
        from genlab_core.cache.stable_ids import generate_candidate_id
        candidate_id = generate_candidate_id(test_story_id, "TPL_BRK1", "test_angle")

        record_id = client.create_blueprint({
            "candidate_id": candidate_id,
            "story_id": test_story_id,
            "topic": "AI News",
            "angle": "test_angle",
            "template_id": "TPL_BRK1",
            "format": "carousel",
            "hook": "[TEST] Integration test blueprint - safe to delete",
            "structure": ["Hook", "Context", "Detail", "CTA"],
            "cta": "Follow for more",
            "priority_score": 0.85,
            "why_this_will_work": "Automated test blueprint",
            "validation_status": {
                "constraints_passed": True,
                "claims_passed": True,
                "risk_acceptable": True,
            },
        })
        assert record_id is not None

    @pytest.mark.xfail(reason="Graph API eventual consistency — blueprint filter may lag after create")
    def test_find_blueprint(self, client, test_story_id):
        import time

        from genlab_core.cache.stable_ids import generate_candidate_id
        candidate_id = generate_candidate_id(test_story_id, "TPL_BRK1", "test_angle")

        # Graph API has eventual consistency — retry with increasing delay
        found = None
        for attempt in range(5):
            found = client.find_blueprint_by_candidate_id(candidate_id)
            if found is not None:
                break
            time.sleep(2 * (attempt + 1))

        assert found is not None, f"Blueprint {candidate_id[:16]}... not found after 5 attempts"
        assert "[TEST]" in found["fields"]["hook"]

    def test_get_blueprints_by_status(self, client):
        results = client.get_blueprints_by_status("INTEL_READY")
        assert isinstance(results, list)


class TestTemplates:
    def test_find_template(self, client):
        found = client.find_template_by_template_id("TPL_BRK1")
        if found:
            assert found["fields"]["template_id"] == "TPL_BRK1"

    def test_get_active_templates(self, client):
        results = client.get_active_templates()
        assert isinstance(results, list)


class TestCleanup:
    """Run last to clean up test data.

    SAFETY: Hard cap of 10 deletions per table. If the formula returns
    more than 10 records, something is wrong — abort to prevent data loss.
    """

    MAX_TEST_CLEANUP = 10  # Never delete more than this many records

    def test_cleanup_test_records(self, client):
        """Delete all records with [TEST] in title/hook."""
        test_stories = client.stories.all(
            formula="FIND('[TEST]', {title})"
        )
        assert len(test_stories) <= self.MAX_TEST_CLEANUP, (
            f"SAFETY: Found {len(test_stories)} test stories to delete "
            f"(max {self.MAX_TEST_CLEANUP}). Aborting to prevent data loss. "
            f"Check formula or increase MAX_TEST_CLEANUP if legitimate."
        )
        for story in test_stories:
            client.stories.delete(story["id"])

        test_blueprints = client.blueprints.all(
            formula="FIND('[TEST]', {hook})"
        )
        assert len(test_blueprints) <= self.MAX_TEST_CLEANUP, (
            f"SAFETY: Found {len(test_blueprints)} test blueprints to delete "
            f"(max {self.MAX_TEST_CLEANUP}). Aborting to prevent data loss. "
            f"Check formula or increase MAX_TEST_CLEANUP if legitimate."
        )
        for bp in test_blueprints:
            client.blueprints.delete(bp["id"])


# ===== Interactive runner =====

def run_interactive():
    """Run tests interactively with colored output."""
    PASS = "\033[92m\u2713\033[0m"
    FAIL = "\033[91m\u2717\033[0m"

    print("=" * 60)
    print("MICROSOFT LISTS INTEGRATION TESTS")
    print("=" * 60)

    if not LISTS_CONFIGURED:
        print(f"\n{FAIL} Microsoft Lists not configured.")
        print("  Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,")
        print("  and SHAREPOINT_SITE_ID in .env")
        return False

    from genlab_core.cache.stable_ids import generate_candidate_id, generate_story_id
    from genlab_core.http.backlog_client import BacklogClient

    client = BacklogClient()
    test_story_id = generate_story_id(
        f"https://test.example.com/interactive-{datetime.now().timestamp()}",
        datetime.now().isoformat()
    )
    all_pass = True

    # Test 1: Connection
    print("\n[1] Connection")
    try:
        assert client.health_check()
        print(f"  {PASS} Health check passed")
    except Exception as e:
        print(f"  {FAIL} {e}")
        return False

    # Test 2: Create story
    print("\n[2] Create Story")
    try:
        record_id = client.create_story({
            "story_id": test_story_id,
            "title": "[TEST] Interactive Test Story - Safe to Delete",
            "url": "https://test.example.com/interactive",
            "source": "Other",
            "published_at": datetime.now().isoformat(),
            "summary": "Automated interactive test. Safe to delete.",
            "priority": 0.75,
            "themes": ["llm"],
            "scores": {"authority": 0.8, "recency": 1.0, "novelty": 0.9},
        })
        print(f"  {PASS} Created story: {record_id}")
    except Exception as e:
        print(f"  {FAIL} {e}")
        all_pass = False

    # Test 3: Find story
    print("\n[3] Find Story")
    try:
        found = client.find_story_by_story_id(test_story_id)
        assert found is not None
        print(f"  {PASS} Found: {found['fields']['title'][:50]}")
    except Exception as e:
        print(f"  {FAIL} {e}")
        all_pass = False

    # Test 4: Create blueprint
    print("\n[4] Create Blueprint")
    candidate_id = generate_candidate_id(test_story_id, "TPL_BRK1", "test")
    try:
        record_id = client.create_blueprint({
            "candidate_id": candidate_id,
            "story_id": test_story_id,
            "topic": "test",
            "angle": "test",
            "template_id": "TPL_BRK1",
            "format": "carousel",
            "hook": "[TEST] Interactive test blueprint",
            "structure": ["Hook", "CTA"],
            "cta": "Test",
            "priority_score": 0.5,
            "why_this_will_work": "Test",
            "validation_status": {
                "constraints_passed": True,
                "claims_passed": True,
                "risk_acceptable": True,
            },
        })
        print(f"  {PASS} Created blueprint: {record_id}")
    except Exception as e:
        print(f"  {FAIL} {e}")
        all_pass = False

    # Test 5: Update status
    print("\n[5] Update Status")
    try:
        client.update_story_status(test_story_id, "VALIDATED")
        print(f"  {PASS} Updated story status to VALIDATED")
    except Exception as e:
        print(f"  {FAIL} {e}")
        all_pass = False

    # Summary
    print("\n" + "=" * 60)
    if all_pass:
        print(f"{PASS} All tests passed!")
    else:
        print(f"{FAIL} Some tests failed")
    print("=" * 60)

    return all_pass


if __name__ == "__main__":
    success = run_interactive()
    exit(0 if success else 1)
