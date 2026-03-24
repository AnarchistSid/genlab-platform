-- Content Pool table for Shared Ingestion + Intelligent Router
-- Stores stories fetched by the shared ingestion pipeline,
-- classified and routed to 1+ niches for per-niche pipeline consumption.

CREATE TABLE IF NOT EXISTS content_pool (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Dedup key (sha256 of source URL + published date)
    content_hash TEXT NOT NULL,

    -- Story data
    title TEXT,
    summary TEXT,
    source_url TEXT,
    source_name TEXT,
    source_platform TEXT,              -- youtube, reddit, rss
    video_url TEXT,
    video_id TEXT,                      -- YouTube video ID
    thumbnail_url TEXT,
    published_at TIMESTAMPTZ,
    duration_seconds INT,
    view_count BIGINT,
    view_velocity FLOAT,

    -- Routing metadata
    source_affinity TEXT[],            -- from shared_sources.yaml affinity tags
    youtube_category_id TEXT,
    niche_scores JSONB NOT NULL DEFAULT '{}',
    routed_niches TEXT[] NOT NULL DEFAULT '{}',
    routing_reason TEXT,

    -- Lifecycle
    status TEXT NOT NULL DEFAULT 'available',
    claimed_by TEXT,
    claimed_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '48 hours',

    -- Metadata
    extra JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_content_pool_hash UNIQUE (content_hash)
);

-- Indexes for efficient niche pipeline reads
CREATE INDEX IF NOT EXISTS idx_cp_routed_status ON content_pool USING GIN(routed_niches) WHERE status = 'available';
CREATE INDEX IF NOT EXISTS idx_cp_status ON content_pool(status);
CREATE INDEX IF NOT EXISTS idx_cp_expires ON content_pool(expires_at) WHERE status = 'available';
CREATE INDEX IF NOT EXISTS idx_cp_fetched ON content_pool(fetched_at DESC);
