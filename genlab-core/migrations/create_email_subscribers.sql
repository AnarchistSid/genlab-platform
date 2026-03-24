CREATE TABLE IF NOT EXISTS email_subscribers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL,
    channel_slug TEXT NOT NULL,
    niche_id TEXT NOT NULL DEFAULT '',
    source TEXT DEFAULT 'link_in_bio',
    subscribed_at TIMESTAMPTZ DEFAULT now(),
    unsubscribed_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT true,
    UNIQUE(email, channel_slug)
);
CREATE INDEX IF NOT EXISTS idx_es_niche ON email_subscribers(niche_id) WHERE is_active = true;
