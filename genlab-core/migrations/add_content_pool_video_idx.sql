-- Add video_id index for cross-entry dedup lookups
-- Enables efficient video_id queries when merging pool entries
-- and when per-niche pipelines check for duplicate videos
CREATE INDEX IF NOT EXISTS idx_cp_video_id
    ON content_pool(video_id)
    WHERE video_id IS NOT NULL;
