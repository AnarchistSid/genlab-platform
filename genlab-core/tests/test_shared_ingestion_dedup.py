"""Tests for shared ingestion dedup hardening."""


def test_youtube_id_extracted_from_reddit_summary():
    """Reddit posts linking to YouTube should have video_id extracted."""
    from genlab_core.pipeline.shared_ingestion import _extract_youtube_id

    assert _extract_youtube_id("Check this out https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_youtube_id("https://youtu.be/dQw4w9WgXcQ cool stuff") == "dQw4w9WgXcQ"
    assert _extract_youtube_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_youtube_id("no youtube link here") is None
    assert _extract_youtube_id("") is None
    assert _extract_youtube_id(None) is None


def test_routed_niches_merge_not_overwrite():
    """Upsert SQL must merge routed_niches arrays, not overwrite."""
    import inspect

    from genlab_core.pipeline.shared_ingestion import SharedIngestionPipeline

    source = inspect.getsource(SharedIngestionPipeline._write_to_pool)
    assert "content_pool.routed_niches || EXCLUDED.routed_niches" in source, \
        "Upsert must merge routed_niches arrays, not overwrite"
