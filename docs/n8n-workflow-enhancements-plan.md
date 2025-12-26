# n8n Workflow Enhancements Implementation Plan

## Overview

This plan covers four enhancements to the feed aggregation and digest generation workflows:

1. **Full Content Scraping** - Deep content extraction using Firecrawl
2. **AI Relevance Filtering** - LLM-based content evaluation at ingestion
3. **Add Anthropic Blog RSS** - New official source for Claude digest
4. **Extract Shared Notion Formatter** - Reusable sub-workflow

## Current Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Feed Data Ingestion                          │
│  ┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐ │
│  │ Schedule │ ─► │ 13 RSS    │ ─► │Normalize │ ─► │ Postgres │ │
│  │ Trigger  │    │ Feeds     │    │ & Tag    │    │ Insert   │ │
│  └──────────┘    └───────────┘    └──────────┘    └──────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Claude/DevOps Digest Generator                   │
│  ┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐ │
│  │ Schedule │ ─► │ Query DB  │ ─► │ AI Agent │ ─► │ Notion   │ │
│  │ 8 AM     │    │ by topic  │    │ + Memory │    │ Page     │ │
│  └──────────┘    └───────────┘    └──────────┘    └──────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Feed Data Ingestion                              │
│  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐            │
│  │ Schedule │─► │ 14 RSS    │─► │Normalize │─► │ Postgres │            │
│  │ Trigger  │   │ Feeds     │   │ & Tag    │   │ Insert   │            │
│  └──────────┘   └───────────┘   └──────────┘   └────┬─────┘            │
│                      ▲                              │                   │
│                      │                              ▼                   │
│              ┌───────┴───────┐              ┌──────────────┐           │
│              │ Anthropic     │              │ Execute:     │           │
│              │ Blog RSS      │              │ Scrape URL   │◄─── NEW   │
│              └───────────────┘              └──────┬───────┘           │
│                    NEW                             │                   │
│                                                    ▼                   │
│                                            ┌──────────────┐            │
│                                            │ AI Relevance │◄─── NEW   │
│                                            │ Evaluation   │            │
│                                            └──────┬───────┘            │
│                                                   │                    │
│                                                   ▼                    │
│                                            ┌──────────────┐            │
│                                            │ Update with  │            │
│                                            │ full content │            │
│                                            └──────────────┘            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Claude/DevOps Digest Generator                        │
│  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────────────────┐│
│  │ Schedule │─► │ Query DB  │─► │ AI Agent │─► │ Execute: Format      ││
│  │ 8 AM     │   │ by topic  │   │ + Memory │   │ Markdown to Notion   ││◄─ NEW
│  └──────────┘   └───────────┘   └──────────┘   └──────────┬───────────┘│
│                                                           │            │
│                                                           ▼            │
│                                                    ┌──────────────┐    │
│                                                    │ Notion Page  │    │
│                                                    └──────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Enhancement 1: Full Content Scraping

### Goal
Extract full article content, images, and external links using Firecrawl API instead of relying on RSS snippets.

### Database Schema Changes

```sql
-- Add new columns to feed_articles table
ALTER TABLE feed_articles ADD COLUMN full_content TEXT;
ALTER TABLE feed_articles ADD COLUMN full_content_markdown TEXT;
ALTER TABLE feed_articles ADD COLUMN external_links JSONB DEFAULT '[]';
ALTER TABLE feed_articles ADD COLUMN image_urls JSONB DEFAULT '[]';
ALTER TABLE feed_articles ADD COLUMN scraped_at TIMESTAMPTZ;
ALTER TABLE feed_articles ADD COLUMN scrape_error TEXT;
```

### New Sub-Workflow: `Scrape URL`

**File**: `n8n-workflows/shared-scrape-url.json`

**Nodes**:
1. **Workflow Trigger** - Receives `{ url: string }`
2. **HTTP Request to Firecrawl** - POST to `https://api.firecrawl.dev/v1/scrape`
   ```json
   {
     "url": "{{ $json.url }}",
     "formats": ["markdown", "links"],
     "onlyMainContent": true,
     "excludeTags": ["nav", "header", "footer", "iframe"]
   }
   ```
3. **Extract Data** - Parse response for markdown, links, images
4. **Return Output** - `{ markdown, links, images, error }`

**Credentials Required**:
- Firecrawl API key (HTTP Header Auth: `Authorization: Bearer <key>`)

### Integration with Feed Data Ingestion

Add after "Insert Article" node:

```
[Insert Article]
      │
      ▼
[Execute Workflow: Scrape URL]
      │
      ▼
[Update Article with Full Content]
      │
      ▼
[Loop back to Process One at a Time]
```

### Rate Limiting Considerations
- Firecrawl: ~100 requests/minute on paid plans
- Add 1-second delay between scrapes
- Process in batches of 10 articles max per run

---

## Enhancement 2: AI Relevance Filtering

### Goal
Use an LLM to evaluate if scraped content is truly relevant before marking as ready for digest.

### Database Schema Changes

```sql
ALTER TABLE feed_articles ADD COLUMN relevance_score FLOAT;
ALTER TABLE feed_articles ADD COLUMN relevance_reasoning TEXT;
ALTER TABLE feed_articles ADD COLUMN ai_evaluated_at TIMESTAMPTZ;
ALTER TABLE feed_articles ADD COLUMN is_relevant BOOLEAN DEFAULT NULL;
```

### Implementation in Feed Data Ingestion

Add after scraping, before final update:

**Node: Evaluate Relevance (LLM Chain)**
```javascript
// Prompt
`You are evaluating content relevance for a ${topic} newsletter.

Topic criteria:
- "claude": Content about Claude AI, Anthropic, Claude Code, or related AI assistants
- "devops": Content about DevOps, Kubernetes, GitOps, IaC, CI/CD, platform engineering

Article Title: ${title}
Article Content (truncated): ${content.substring(0, 2000)}

Evaluate:
1. Is this content relevant to the topic? (true/false)
2. Relevance score (0.0 to 1.0)
3. Brief reasoning (1-2 sentences)

Output as JSON: { "is_relevant": boolean, "score": number, "reasoning": string }`
```

**Model**: Claude Haiku 4.5 (cost-effective for classification)

**Structured Output Parser**:
```json
{
  "type": "object",
  "properties": {
    "is_relevant": { "type": "boolean" },
    "score": { "type": "number" },
    "reasoning": { "type": "string" }
  }
}
```

### Update Digest Query

Modify digest generators to filter by relevance:

```sql
SELECT url, title, full_content_markdown, ...
FROM feed_articles
WHERE 'claude' = ANY(topic)
  AND pub_date >= NOW() - INTERVAL '24 hours'
  AND is_relevant = TRUE
  AND relevance_score >= 0.6
  AND (last_digest_date IS NULL OR last_digest_date < CURRENT_DATE)
ORDER BY relevance_score DESC, pub_date DESC
LIMIT 20;
```

---

## Enhancement 3: Add Anthropic Blog RSS

### Goal
Add official Anthropic blog as a high-priority source for Claude digest.

### Changes to Feed Data Ingestion

**New Node**: `RSS Anthropic Blog`
```json
{
  "parameters": {
    "url": "https://www.anthropic.com/feed",
    "options": {}
  },
  "id": "rss-anthropic-blog",
  "name": "RSS Anthropic Blog",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, -112],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

**Update Merge Node**: Change `numberInputs` from 10 to 11 in Merge Group 1

**Update Normalize Function**: Add mapping for Anthropic blog
```javascript
// Add to getSourceName function
'anthropic.com': 'Anthropic Blog',

// Add to getTopicTags function
if (lowerLink.includes('anthropic.com/blog') ||
    lowerLink.includes('anthropic.com/news')) {
  tags.push('claude');
  tags.push('official');  // New tag for official sources
}
```

**Update Connection**: Trigger → RSS Anthropic Blog → Merge Group 1 (index 10)

### Source Priority

Add `source_priority` to help digest prefer official sources:

```sql
ALTER TABLE feed_articles ADD COLUMN source_priority INT DEFAULT 5;
```

Priority values:
- 1: Official blogs (Anthropic, Kubernetes, HashiCorp)
- 3: Major tech blogs (InfoQ, DevOps.com)
- 5: Community (Reddit, HN)

---

## Enhancement 4: Extract Shared Notion Formatter

### Goal
Create a reusable sub-workflow for converting markdown to Notion blocks.

### New Sub-Workflow: `Format Markdown to Notion`

**File**: `n8n-workflows/shared-format-notion.json`

**Input Schema**:
```json
{
  "markdown": "string - The markdown content to convert",
  "maxBlocks": "number - Optional, default 98"
}
```

**Output Schema**:
```json
{
  "notionChildren": "array - Notion block objects",
  "blockCount": "number - Total blocks created",
  "wasTruncated": "boolean - If content was truncated"
}
```

**Nodes**:
1. **Workflow Trigger** - Receives markdown input
2. **Code Node** - Parse markdown to Notion blocks (extract existing logic)
3. **Set Output** - Return structured output

### Refactor Digest Generators

Replace the duplicate "Format for Notion" code nodes with:

```json
{
  "parameters": {
    "workflowId": {
      "__rl": true,
      "value": "SHARED_FORMAT_NOTION_WORKFLOW_ID",
      "mode": "id"
    },
    "workflowInputs": {
      "mappingMode": "defineBelow",
      "value": {
        "markdown": "={{ $json.output || $json.text }}"
      }
    }
  },
  "id": "execute-format-notion",
  "name": "Format Markdown to Notion",
  "type": "n8n-nodes-base.executeWorkflow",
  "typeVersion": 1.2
}
```

---

## Implementation Order

### Phase 1: Foundation (Day 1)
1. Create database migration script for new columns
2. Create `shared-scrape-url.json` sub-workflow
3. Create `shared-format-notion.json` sub-workflow
4. Test sub-workflows independently

### Phase 2: Add New Source (Day 1)
5. Add Anthropic Blog RSS to feed-data-ingestion.json
6. Update normalize function with new source mappings
7. Test ingestion with new source

### Phase 3: Content Scraping Integration (Day 2)
8. Integrate scrape sub-workflow into feed-data-ingestion
9. Add rate limiting and error handling
10. Test end-to-end scraping flow

### Phase 4: AI Relevance (Day 2)
11. Add relevance evaluation after scraping
12. Update digest queries to filter by relevance
13. Test relevance scoring accuracy

### Phase 5: Formatter Integration (Day 3)
14. Refactor claude-digest-generator to use shared formatter
15. Refactor devops-digest-generator to use shared formatter
16. Test digest generation with full content

### Phase 6: Testing & Deployment (Day 3)
17. Run full integration test
18. Deploy to n8n instance
19. Monitor first few runs

---

## Files to Create/Modify

### New Files
- `n8n-workflows/shared-scrape-url.json`
- `n8n-workflows/shared-format-notion.json`
- `scripts/db-migration-feed-articles-v2.sql`

### Modified Files
- `n8n-workflows/feed-data-ingestion.json`
- `n8n-workflows/claude-digest-generator.json`
- `n8n-workflows/devops-digest-generator.json`

---

## Rollback Plan

Each enhancement is independent. If issues arise:

1. **Scraping fails**: Articles still have `content_snippet`, digests work with existing data
2. **Relevance filter too aggressive**: Set `is_relevant = NULL` and query ignores filter
3. **New RSS feed broken**: Remove from connections, other feeds continue
4. **Shared formatter issues**: Revert to inline code in digest generators

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Content per article | ~500 chars | Full article |
| Digest quality | RSS snippets | Full context |
| Irrelevant articles | ~30% | <10% |
| Official source coverage | 0 | 100% of Anthropic posts |
| Code duplication | 2x Notion formatter | 1 shared workflow |

---

## Credentials Required

1. **Firecrawl API** - For content scraping
   - Type: HTTP Header Auth
   - Header: `Authorization`
   - Value: `Bearer <FIRECRAWL_API_KEY>`

2. **Anthropic API** - For relevance evaluation (already exists)

3. **PostgreSQL** - Already configured

4. **Notion** - Already configured
