# Feed Aggregator Refactor Plan

## Overview

Refactor the two similar workflows (`claude-news-aggregator.json` and `devops-feed-aggregator.json`) into a shared architecture following the reference pattern from `ai_news_data_ingestion.json`.

## Current State Analysis

### Similarities Between Workflows
| Component | Claude Aggregator | DevOps Aggregator |
|-----------|-------------------|-------------------|
| Trigger | Daily 8 AM cron | Daily 8 AM cron |
| RSS Feeds | 4 feeds | 10 feeds |
| Merge Pattern | Same | Same |
| Filter/Sort Logic | Same (7-day window, dedup) | Same (7-day window, dedup) |
| LLM Preparation | Same structure | Same structure |
| AI Agent | Claude Haiku + Memory | Claude Haiku + Memory |
| Output | Notion child page | Notion child page |

### Problems with Current Approach
1. **Code Duplication**: 90% identical logic in two workflows
2. **No Persistence**: Data is fetched and discarded each run
3. **No Deduplication History**: Can't detect cross-day duplicates
4. **Single Point of Failure**: If one RSS feed fails, no retry
5. **Limited Freshness**: Only daily updates, miss breaking news

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SHARED DATA INGESTION                         │
│                   (runs every 4 hours)                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ Claude   │   │ DevOps   │   │ General  │   │ Company  │     │
│  │ Sources  │   │ Sources  │   │ AI News  │   │ Blogs    │     │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘     │
│       │              │              │              │            │
│       └──────────────┴──────────────┴──────────────┘            │
│                          │                                       │
│                    ┌─────▼─────┐                                 │
│                    │ Normalize │  (add topic tags, source meta) │
│                    └─────┬─────┘                                 │
│                          │                                       │
│                    ┌─────▼─────┐                                 │
│                    │  Dedup    │  (check PostgreSQL)            │
│                    └─────┬─────┘                                 │
│                          │                                       │
│                    ┌─────▼─────┐                                 │
│                    │  Store    │  (PostgreSQL + optional S3)    │
│                    └───────────┘                                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  DIGEST GENERATORS (daily 8 AM)                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────┐      ┌─────────────────────┐          │
│  │  Claude Digest      │      │  DevOps Digest      │          │
│  │  Generator          │      │  Generator          │          │
│  ├─────────────────────┤      ├─────────────────────┤          │
│  │ 1. Query DB         │      │ 1. Query DB         │          │
│  │    (topic=claude)   │      │    (topic=devops)   │          │
│  │ 2. AI Summarize     │      │ 2. AI Summarize     │          │
│  │ 3. Format Notion    │      │ 3. Format Notion    │          │
│  │ 4. Create Page      │      │ 4. Create Page      │          │
│  └─────────────────────┘      └─────────────────────┘          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: Database Setup

**Goal**: Create PostgreSQL schema for storing ingested articles.

#### 1.1 Create Database Table

```sql
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

CREATE INDEX idx_feed_articles_topic ON feed_articles USING GIN(topic);
CREATE INDEX idx_feed_articles_pub_date ON feed_articles(pub_date DESC);
CREATE INDEX idx_feed_articles_source ON feed_articles(source_name);
CREATE INDEX idx_feed_articles_url_hash ON feed_articles(url_hash);
```

#### 1.2 Create Vault Secret

Store PostgreSQL connection string in Vault at `k8s-secrets/n8n/postgres-feeds`.

#### 1.3 Create ExternalSecret

```yaml
# base-apps/n8n/external-secret-feeds-db.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: n8n-feeds-db
  namespace: n8n
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: n8n-feeds-db
  data:
  - secretKey: connection-string
    remoteRef:
      key: n8n/postgres-feeds
      property: connection-string
```

---

### Phase 2: Shared Data Ingestion Workflow

**Goal**: Create `feed-data-ingestion.json` workflow.

#### 2.1 Workflow Structure

```
Triggers (parallel, every 4 hours):
├── Claude Sources Group
│   ├── RSS r/ClaudeAI
│   ├── RSS r/anthropic
│   ├── RSS HN Claude
│   ├── RSS HN Anthropic
│   └── RSS Anthropic Blog (NEW)
│
├── DevOps Sources Group
│   ├── RSS DevOps.com
│   ├── RSS Agile Admin
│   ├── RSS InfoQ DevOps
│   ├── RSS HashiCorp
│   ├── RSS CNCF
│   ├── RSS Kubernetes
│   ├── RSS HN DevOps
│   ├── RSS r/devops
│   └── RSS r/kubernetes
│
└── Shared AI Sources (NEW)
    ├── RSS OpenAI Blog
    ├── RSS Google AI Blog
    └── RSS HN AI/ML

Processing Pipeline:
├── Merge All Feeds
├── Normalize (add topic tags, source metadata)
├── Check Existing (PostgreSQL lookup)
├── Filter New Items
├── Insert to PostgreSQL
└── (Optional) Slack notification on new high-priority items
```

#### 2.2 Key Nodes to Implement

**Node: Normalize Feed Items**
```javascript
// Normalize all feeds to consistent schema with topic tagging
const items = $input.all();

function getTopicTags(link, title) {
  const tags = [];
  const lowerTitle = (title || '').toLowerCase();
  const lowerLink = (link || '').toLowerCase();

  // Claude/Anthropic topics
  if (lowerLink.includes('claudeai') || lowerLink.includes('anthropic') ||
      lowerTitle.includes('claude') || lowerTitle.includes('anthropic')) {
    tags.push('claude');
  }

  // DevOps topics
  if (lowerLink.includes('devops') || lowerLink.includes('kubernetes') ||
      lowerLink.includes('terraform') || lowerLink.includes('hashicorp') ||
      lowerLink.includes('cncf') || lowerTitle.includes('devops') ||
      lowerTitle.includes('kubernetes') || lowerTitle.includes('gitops')) {
    tags.push('devops');
  }

  // General AI topics
  if (lowerTitle.includes('openai') || lowerTitle.includes('gpt') ||
      lowerTitle.includes('llm') || lowerTitle.includes('machine learning')) {
    tags.push('ai-general');
  }

  return tags.length > 0 ? tags : ['uncategorized'];
}

function getSourceCategory(link) {
  if (link.includes('reddit.com') || link.includes('news.ycombinator.com')) {
    return 'community';
  }
  if (link.includes('anthropic.com') || link.includes('hashicorp.com') ||
      link.includes('kubernetes.io') || link.includes('cncf.io')) {
    return 'official';
  }
  return 'blog';
}

function getSourceName(link) {
  const mapping = {
    'reddit.com/r/ClaudeAI': 'r/ClaudeAI',
    'reddit.com/r/anthropic': 'r/anthropic',
    'reddit.com/r/devops': 'r/devops',
    'reddit.com/r/kubernetes': 'r/kubernetes',
    'news.ycombinator.com': 'Hacker News',
    'anthropic.com': 'Anthropic Blog',
    'devops.com': 'DevOps.com',
    'hashicorp.com': 'HashiCorp',
    'kubernetes.io': 'Kubernetes',
    'cncf.io': 'CNCF',
    // ... add more mappings
  };

  for (const [pattern, name] of Object.entries(mapping)) {
    if (link.includes(pattern)) return name;
  }
  return 'Unknown';
}

return items.map(item => {
  const json = item.json;
  const link = json.link || json.guid || '';
  const title = json.title || 'No title';

  return {
    json: {
      url: link,
      title: title,
      content: json.content || json.description || '',
      content_snippet: (json.contentSnippet || json.content || '').substring(0, 500),
      source_name: getSourceName(link),
      source_url: json.feedUrl || '',
      topic: getTopicTags(link, title),
      category: getSourceCategory(link),
      pub_date: json.pubDate || json.isoDate || new Date().toISOString(),
      metadata: {
        author: json.creator || json.author,
        comments_count: json.comments,
        original_guid: json.guid
      }
    }
  };
}).filter(item => item.json.url); // Filter out items without URLs
```

**Node: Check Existing URLs (PostgreSQL)**
```sql
SELECT url FROM feed_articles
WHERE url_hash = md5($1)
LIMIT 1;
```

**Node: Insert New Article**
```sql
INSERT INTO feed_articles (url, title, content, content_snippet, source_name, source_url, topic, category, pub_date, metadata)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (url) DO NOTHING
RETURNING id;
```

#### 2.3 Error Handling

Add to each RSS feed node:
```json
{
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

---

### Phase 3: Claude Digest Generator Workflow

**Goal**: Create `claude-digest-generator.json` workflow.

#### 3.1 Workflow Structure

```
Trigger: Daily 8 AM cron
    │
    ▼
Query PostgreSQL (last 24h, topic contains 'claude')
    │
    ▼
Prepare for LLM (format posts)
    │
    ▼
AI Agent (Claude Haiku + Memory)
    │
    ▼
Mark Articles as Processed
    │
    ▼
Format for Notion
    │
    ▼
Create Notion Child Page
```

#### 3.2 Key Nodes

**Node: Query Recent Claude Articles**
```sql
SELECT
    url, title, content_snippet, source_name, category, pub_date,
    metadata->>'author' as author,
    metadata->>'comments_count' as engagement
FROM feed_articles
WHERE 'claude' = ANY(topic)
  AND pub_date >= NOW() - INTERVAL '24 hours'
  AND (last_digest_date IS NULL OR last_digest_date < CURRENT_DATE)
ORDER BY pub_date DESC
LIMIT 30;
```

**Node: Mark as Processed**
```sql
UPDATE feed_articles
SET last_digest_date = CURRENT_DATE, is_processed = true
WHERE url = ANY($1);
```

#### 3.3 AI Agent Prompt (reuse existing with minor updates)

```
You are a helpful assistant that creates daily news digests about Claude AI and Anthropic.

You have memory of your previous daily digest summaries. Use this memory to:
- Focus on NEW content not covered in previous summaries
- Avoid repeating information from previous digests
- Reference trends or ongoing discussions if relevant

## Today's Posts (from database):
{{ $json.posts }}

## Instructions:
Create a markdown-formatted daily digest with these sections:

1. **Key Updates** - Major announcements, new features, or important news
2. **Hot Discussions** - Interesting community discussions or debates
3. **Tips & Tricks** - Useful tips shared by the community
4. **Issues & Bugs** - Notable problems or bugs reported

For each item, include the source name in parentheses.
Keep summaries concise but informative.
If a section has no relevant NEW content, write "No notable items today."

Format the output as clean markdown suitable for Notion.
```

---

### Phase 4: DevOps Digest Generator Workflow

**Goal**: Create `devops-digest-generator.json` workflow.

Nearly identical to Phase 3, with these differences:

1. **Query filter**: `'devops' = ANY(topic)`
2. **AI prompt sections**:
   - Key Releases & Announcements
   - Infrastructure Trends
   - Community Highlights
   - Tools & Tutorials
   - Security Updates
3. **Notion page title**: `'DevOps - ' + $json.date`
4. **Memory session key**: `devops-daily-digest`

---

### Phase 5: Cleanup and Migration

#### 5.1 Migration Steps

1. Deploy database schema to PostgreSQL
2. Create and test `feed-data-ingestion.json`
3. Run ingestion for 2-3 days to populate database
4. Create and test `claude-digest-generator.json`
5. Create and test `devops-digest-generator.json`
6. Disable old workflows
7. Enable new workflows
8. Monitor for 1 week
9. Delete old workflow files

#### 5.2 Files to Create
```
n8n-workflows/
├── feed-data-ingestion.json        # NEW: Shared ingestion
├── claude-digest-generator.json    # NEW: Claude digest
├── devops-digest-generator.json    # NEW: DevOps digest
├── claude-news-aggregator.json     # DEPRECATED
└── devops-feed-aggregator.json     # DEPRECATED
```

#### 5.3 Database Maintenance

Add a cleanup job (weekly):
```sql
-- Delete articles older than 30 days
DELETE FROM feed_articles
WHERE ingested_at < NOW() - INTERVAL '30 days';

-- Vacuum to reclaim space
VACUUM ANALYZE feed_articles;
```

---

## Implementation Timeline

| Phase | Description | Estimated Effort |
|-------|-------------|------------------|
| Phase 1 | Database Setup | 2-3 hours |
| Phase 2 | Data Ingestion Workflow | 4-6 hours |
| Phase 3 | Claude Digest Generator | 2-3 hours |
| Phase 4 | DevOps Digest Generator | 1-2 hours |
| Phase 5 | Migration & Cleanup | 2-3 hours |
| **Total** | | **11-17 hours** |

---

## Future Enhancements (Post-MVP)

1. **Full Content Scraping**: Integrate Firecrawl to get full article text
2. **AI Relevance Scoring**: Pre-filter low-quality content before digest
3. **Slack Notifications**: Alert on breaking news (high-engagement posts)
4. **Weekly Rollup**: Generate weekly summary in addition to daily
5. **Topic Expansion**: Add more topics (security, cloud, etc.)
6. **Web Dashboard**: Build a simple UI to browse ingested articles
7. **Email Digest Option**: Send digest via email in addition to Notion

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| PostgreSQL unavailable | Ingestion fails | Add retry logic, consider SQLite fallback |
| RSS feeds rate-limited | Missing content | Stagger requests with Wait nodes |
| Duplicate detection fails | Repeated content | Use URL hash + title similarity |
| Memory grows unbounded | n8n performance | Limit memory window, periodic clear |
| Notion API rate limits | Page creation fails | Add retry with exponential backoff |

---

## Success Criteria

1. **Reliability**: 99%+ successful ingestion runs over 30 days
2. **Freshness**: Articles available within 4 hours of publication
3. **Deduplication**: <1% duplicate articles in digests
4. **Maintainability**: Single place to add new sources (ingestion workflow)
5. **Extensibility**: Easy to add new topic-specific digest generators
