"""Smoke tests: verify publishing clients are importable from genlab-core."""


def test_tiktok_client_importable():
    from genlab_core.publishing.tiktok_client import TikTokClient

    assert TikTokClient is not None


def test_threads_client_importable():
    from genlab_core.publishing.threads_client import ThreadsClient

    assert ThreadsClient is not None


def test_threads_token_manager_importable():
    from genlab_core.publishing.threads_client import ThreadsTokenManager

    assert ThreadsTokenManager is not None


def test_cdn_upload_importable():
    from genlab_core.publishing.cdn_upload import LocalCDNUpload

    assert LocalCDNUpload is not None


def test_package_reexports():
    from genlab_core.publishing import (
        LocalCDNUpload,
        ThreadsClient,
        ThreadsTokenManager,
        TikTokClient,
    )

    assert all([TikTokClient, ThreadsClient, ThreadsTokenManager, LocalCDNUpload])
