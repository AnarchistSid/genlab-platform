CREATE INDEX IF NOT EXISTS idx_bp_video_niche ON blueprints(video_id, niche_id) WHERE video_id IS NOT NULL AND video_id != '';
CREATE INDEX IF NOT EXISTS idx_bp_hook_niche ON blueprints(hook, niche_id) WHERE hook IS NOT NULL AND hook != '';
CREATE INDEX IF NOT EXISTS idx_bp_status_niche ON blueprints(status, niche_id);
CREATE INDEX IF NOT EXISTS idx_pa_created_status ON publishing_analytics(created_at, status);
