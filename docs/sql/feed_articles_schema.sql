-- Feed Articles Schema for n8n Feed Aggregator
-- This table stores ingested RSS feed articles for deduplication and digest generation

-- Create the feed_articles table
CREATE TABLE IF NOT EXISTS feed_articles (
    id SERIAL PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    url_hash TEXT GENERATED ALWAYS AS (md5(url)) STORED,
    title TEXT NOT NULL,
    content TEXT,
    content_snippet TEXT,
    source_name TEXT NOT NULL,
    source_url TEXT,
    topic TEXT[] NOT NULL,  -- ['claude', 'anthropic'] or ['devops', 'kubernetes']
    category TEXT,          -- 'blog', 'community', 'infrastructure', 'official'
    pub_date TIMESTAMP WITH TIME ZONE,
    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB,         -- flexible field for extra data
    is_processed BOOLEAN DEFAULT FALSE,
    last_digest_date DATE   -- track which digest included this
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_feed_articles_topic ON feed_articles USING GIN(topic);
CREATE INDEX IF NOT EXISTS idx_feed_articles_pub_date ON feed_articles(pub_date DESC);
CREATE INDEX IF NOT EXISTS idx_feed_articles_source ON feed_articles(source_name);
CREATE INDEX IF NOT EXISTS idx_feed_articles_url_hash ON feed_articles(url_hash);
CREATE INDEX IF NOT EXISTS idx_feed_articles_ingested ON feed_articles(ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_articles_processed ON feed_articles(is_processed) WHERE is_processed = FALSE;

-- Maintenance function: Clean up articles older than 30 days
-- Run this weekly via a scheduled n8n workflow or cron job
CREATE OR REPLACE FUNCTION cleanup_old_articles(days_to_keep INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM feed_articles
    WHERE ingested_at < NOW() - (days_to_keep || ' days')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Example usage:
-- SELECT cleanup_old_articles(30);  -- Delete articles older than 30 days

-- Query helpers for digest generation:

-- Get unprocessed Claude articles from last 24 hours
-- SELECT url, title, content_snippet, source_name, category, pub_date
-- FROM feed_articles
-- WHERE 'claude' = ANY(topic)
--   AND pub_date >= NOW() - INTERVAL '24 hours'
--   AND (last_digest_date IS NULL OR last_digest_date < CURRENT_DATE)
-- ORDER BY pub_date DESC
-- LIMIT 30;

-- Get unprocessed DevOps articles from last 24 hours
-- SELECT url, title, content_snippet, source_name, category, pub_date
-- FROM feed_articles
-- WHERE 'devops' = ANY(topic)
--   AND pub_date >= NOW() - INTERVAL '24 hours'
--   AND (last_digest_date IS NULL OR last_digest_date < CURRENT_DATE)
-- ORDER BY pub_date DESC
-- LIMIT 30;

-- Mark articles as processed after digest generation
-- UPDATE feed_articles
-- SET last_digest_date = CURRENT_DATE, is_processed = true
-- WHERE url = ANY(ARRAY['url1', 'url2', ...]);
