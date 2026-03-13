"""Verify that old import paths still work via shim."""
from __future__ import annotations


def test_old_import_path_postiz_client():
    """CriticalRush imports PostizClient via old path."""
    from genlab_core.platform.postiz_client import PostizClient, PublishResult
    assert PostizClient is not None
    assert PublishResult is not None


def test_old_import_path_platform_rules():
    """Multiple files import enforce_platform_rules via old path."""
    from genlab_core.platform.platform_rules import enforce_platform_rules
    assert callable(enforce_platform_rules)


def test_new_import_path_postiz():
    """New code uses platforms.postiz."""
    from genlab_core.platforms.postiz import PostizClient, PublishResult
    assert PostizClient is not None
    assert PublishResult is not None


def test_new_import_path_rules():
    from genlab_core.platforms.rules import enforce_platform_rules
    assert callable(enforce_platform_rules)


def test_old_and_new_are_same_class():
    from genlab_core.platform.postiz_client import PostizClient as OldPC
    from genlab_core.platforms.postiz import PostizClient as NewPC
    assert OldPC is NewPC
