-- Migration: Feed Articles Enhancements
-- Description: Add columns for full content scraping and AI relevance filtering
-- Date: 2024-12-26

BEGIN;

-- Enhancement 1: Full Content Scraping columns
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS full_content TEXT;
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS full_content_markdown TEXT;
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS external_links JSONB DEFAULT '[]';
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS image_urls JSONB DEFAULT '[]';
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS scraped_at TIMESTAMPTZ;
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS scrape_error TEXT;

-- Enhancement 2: AI Relevance Filtering columns
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS relevance_score FLOAT;
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS relevance_reasoning TEXT;
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS ai_evaluated_at TIMESTAMPTZ;
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS is_relevant BOOLEAN DEFAULT NULL;

-- Enhancement 3: Source Priority for official sources
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS source_priority INT DEFAULT 5;

-- Create index for relevance-based queries
CREATE INDEX IF NOT EXISTS idx_feed_articles_relevance
ON feed_articles (is_relevant, relevance_score DESC)
WHERE is_relevant = TRUE;

-- Create index for scraping status
CREATE INDEX IF NOT EXISTS idx_feed_articles_scrape_status
ON feed_articles (scraped_at)
WHERE scraped_at IS NULL AND scrape_error IS NULL;

-- Add comment for documentation
COMMENT ON COLUMN feed_articles.full_content IS 'Raw text content extracted via Firecrawl';
COMMENT ON COLUMN feed_articles.full_content_markdown IS 'Markdown-formatted content from Firecrawl';
COMMENT ON COLUMN feed_articles.external_links IS 'JSON array of external URLs found in article';
COMMENT ON COLUMN feed_articles.image_urls IS 'JSON array of image URLs found in article';
COMMENT ON COLUMN feed_articles.relevance_score IS 'AI-evaluated relevance score 0.0-1.0';
COMMENT ON COLUMN feed_articles.is_relevant IS 'Boolean flag for digest inclusion';
COMMENT ON COLUMN feed_articles.source_priority IS '1=official, 3=major blog, 5=community';

COMMIT;
