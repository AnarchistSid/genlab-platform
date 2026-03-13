"""Tests for platform protocol definitions."""
from __future__ import annotations


def test_publisher_protocol_is_runtime_checkable():
    from genlab_core.platforms.protocols import Publisher

    class FakePublisher:
        platform_id = "fake"
        def publish(self, payload):
            pass

    assert isinstance(FakePublisher(), Publisher)


def test_non_publisher_fails_isinstance():
    from genlab_core.platforms.protocols import Publisher

    class NotAPublisher:
        pass

    assert not isinstance(NotAPublisher(), Publisher)


def test_engageable_protocol_is_runtime_checkable():
    from genlab_core.platforms.protocols import Engageable

    class FakeEngageable:
        def post_reply(self, parent_id: str, text: str, *, context_id: str = "") -> bool:
            return True
        def like(self, target_id: str, *, context_id: str = "") -> bool:
            return True

    assert isinstance(FakeEngageable(), Engageable)


def test_trackable_protocol_is_runtime_checkable():
    from genlab_core.platforms.protocols import Trackable

    class FakeTrackable:
        def get_metrics(self, post_id, published_at):
            return None

    assert isinstance(FakeTrackable(), Trackable)


def test_healthcheckable_protocol_is_runtime_checkable():
    from genlab_core.platforms.protocols import HealthCheckable

    class FakeHealthCheckable:
        def check_token_health(self):
            return None

    assert isinstance(FakeHealthCheckable(), HealthCheckable)
