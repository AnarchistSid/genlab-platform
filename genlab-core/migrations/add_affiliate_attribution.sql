-- Add attribution columns to affiliate_clicks
ALTER TABLE affiliate_clicks ADD COLUMN IF NOT EXISTS blueprint_id TEXT DEFAULT '';
ALTER TABLE affiliate_clicks ADD COLUMN IF NOT EXISTS channel_id TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_ac_blueprint ON affiliate_clicks (blueprint_id);
CREATE INDEX IF NOT EXISTS idx_ac_channel ON affiliate_clicks (channel_id);
