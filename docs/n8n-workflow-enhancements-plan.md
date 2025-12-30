# n8n Workflow Enhancements Implementation Plan

## Overview

This plan covers four enhancements to the feed aggregation and digest generation workflows:

1. **Full Content Scraping** - Deep content extraction using Firecrawl
2. **AI Relevance Filtering** - LLM-based content evaluation at ingestion
3. **Add Anthropic Blog RSS** - New official source for Claude digest
4. **Extract Shared Notion Formatter** - Reusable sub-workflow

---

## Current Architecture

```text
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

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                         Feed Data Ingestion                              │
│  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐            │
│  │ Schedule │─► │ 29 RSS    │─► │Normalize │─► │ Postgres │            │
│  │ Trigger  │   │ Feeds     │   │ & Tag    │   │ Insert   │            │
│  └──────────┘   └───────────┘   └──────────┘   └────┬─────┘            │
│                      ▲                              │                   │
│                      │                              ▼                   │
│              ┌───────┴───────┐              ┌──────────────┐           │
│              │ +16 New RSS:  │              │ Execute:     │           │
│              │ - Anthropic   │              │ Scrape URL   │◄─── NEW   │
│              │ - 7 Medium    │              └──────┬───────┘           │
│              │ - 8 Substack  │                     │                   │
│              └───────────────┘                     │                   │
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

## Phase 1: Foundation

### 1.1 Database Migration

**File**: `scripts/db-migrations/001-feed-articles-enhancements.sql`

```sql
-- Migration: Feed Articles Enhancements
-- Description: Add columns for full content scraping and AI relevance filtering

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

COMMIT;
```

---

### 1.2 Shared Workflow: Scrape URL

**File**: `n8n-workflows/shared-scrape-url.json`

This sub-workflow uses Firecrawl to extract full content from URLs.

```json
{
  "name": "Shared - Scrape URL",
  "nodes": [
    {
      "parameters": {
        "inputSource": "passthrough"
      },
      "id": "workflow-trigger",
      "name": "Execute Workflow Trigger",
      "type": "n8n-nodes-base.executeWorkflowTrigger",
      "typeVersion": 1.1,
      "position": [0, 240]
    },
    {
      "parameters": {
        "method": "POST",
        "url": "https://api.firecrawl.dev/v1/scrape",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            {
              "name": "Content-Type",
              "value": "application/json"
            }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={\n  \"url\": \"{{ $json.url }}\",\n  \"formats\": [\"markdown\", \"links\"],\n  \"onlyMainContent\": true,\n  \"excludeTags\": [\"nav\", \"header\", \"footer\", \"iframe\", \"script\", \"style\"],\n  \"timeout\": 30000\n}",
        "options": {
          "timeout": 60000
        }
      },
      "id": "firecrawl-scrape",
      "name": "Firecrawl Scrape",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [220, 240],
      "retryOnFail": true,
      "maxTries": 2,
      "waitBetweenTries": 3000,
      "onError": "continueRegularOutput",
      "credentials": {
        "httpHeaderAuth": {
          "id": "FIRECRAWL_CREDENTIAL_ID",
          "name": "Firecrawl API"
        }
      }
    },
    {
      "parameters": {
        "conditions": {
          "options": {
            "caseSensitive": true,
            "leftValue": "",
            "typeValidation": "strict"
          },
          "conditions": [
            {
              "id": "check-success",
              "leftValue": "={{ $json.success }}",
              "rightValue": true,
              "operator": {
                "type": "boolean",
                "operation": "equals"
              }
            }
          ],
          "combinator": "and"
        },
        "options": {}
      },
      "id": "if-success",
      "name": "Scrape Successful?",
      "type": "n8n-nodes-base.if",
      "typeVersion": 2.2,
      "position": [440, 240]
    },
    {
      "parameters": {
        "jsCode": "const response = $input.first().json;\nconst data = response.data || {};\n\n// Extract image URLs from markdown content\nconst imageRegex = /!\\[.*?\\]\\((https?:\\/\\/[^)]+)\\)/g;\nconst images = [];\nlet match;\nwhile ((match = imageRegex.exec(data.markdown || '')) !== null) {\n  images.push(match[1]);\n}\n\n// Also check for img tags in any HTML\nconst imgTagRegex = /<img[^>]+src=[\"'](https?:\\/\\/[^\"']+)[\"']/g;\nwhile ((match = imgTagRegex.exec(data.html || '')) !== null) {\n  images.push(match[1]);\n}\n\n// Deduplicate images\nconst uniqueImages = [...new Set(images)];\n\nreturn [{\n  json: {\n    success: true,\n    markdown: data.markdown || '',\n    links: data.links || [],\n    images: uniqueImages,\n    metadata: data.metadata || {},\n    error: null\n  }\n}];"
      },
      "id": "extract-data",
      "name": "Extract Content Data",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [660, 160]
    },
    {
      "parameters": {
        "jsCode": "const response = $input.first().json;\n\nreturn [{\n  json: {\n    success: false,\n    markdown: null,\n    links: [],\n    images: [],\n    metadata: {},\n    error: response.error || response.message || 'Scraping failed'\n  }\n}];"
      },
      "id": "handle-error",
      "name": "Handle Error",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [660, 320]
    },
    {
      "parameters": {
        "numberInputs": 2
      },
      "id": "merge-output",
      "name": "Merge Output",
      "type": "n8n-nodes-base.merge",
      "typeVersion": 3.2,
      "position": [880, 240]
    }
  ],
  "connections": {
    "Execute Workflow Trigger": {
      "main": [[{ "node": "Firecrawl Scrape", "type": "main", "index": 0 }]]
    },
    "Firecrawl Scrape": {
      "main": [[{ "node": "Scrape Successful?", "type": "main", "index": 0 }]]
    },
    "Scrape Successful?": {
      "main": [
        [{ "node": "Extract Content Data", "type": "main", "index": 0 }],
        [{ "node": "Handle Error", "type": "main", "index": 0 }]
      ]
    },
    "Extract Content Data": {
      "main": [[{ "node": "Merge Output", "type": "main", "index": 0 }]]
    },
    "Handle Error": {
      "main": [[{ "node": "Merge Output", "type": "main", "index": 1 }]]
    }
  },
  "settings": {
    "executionOrder": "v1"
  },
  "meta": {
    "notes": "Shared sub-workflow for scraping URLs using Firecrawl API. Returns markdown content, links, and images. Input: { url: string }. Output: { success, markdown, links, images, error }."
  }
}
```

**Credentials Required**:

- Create HTTP Header Auth credential named "Firecrawl API"
- Header Name: `Authorization`
- Header Value: `Bearer <YOUR_FIRECRAWL_API_KEY>`

---

### 1.3 Shared Workflow: Format Markdown to Notion

**File**: `n8n-workflows/shared-format-notion.json`

This sub-workflow converts markdown to Notion block format.

```json
{
  "name": "Shared - Format Markdown to Notion",
  "nodes": [
    {
      "parameters": {
        "inputSource": "passthrough"
      },
      "id": "workflow-trigger",
      "name": "Execute Workflow Trigger",
      "type": "n8n-nodes-base.executeWorkflowTrigger",
      "typeVersion": 1.1,
      "position": [0, 240]
    },
    {
      "parameters": {
        "mode": "runOnceForAllItems",
        "jsCode": "const item = $input.first();\nconst text = item.json.markdown || '';\nconst maxLength = item.json.maxLength || 1900;\nconst maxBlocks = item.json.maxBlocks || 98;\n\nfunction parseRichText(text) {\n  const segments = [];\n  let remaining = text;\n  \n  while (remaining.length > 0) {\n    const boldMatch = remaining.match(/^\\*\\*(.+?)\\*\\*/);\n    const italicMatch = remaining.match(/^\\*(.+?)\\*/);\n    const codeMatch = remaining.match(/^`(.+?)`/);\n    const linkMatch = remaining.match(/^\\[(.+?)\\]\\((.+?)\\)/);\n    \n    if (boldMatch) {\n      segments.push({ type: 'text', text: { content: boldMatch[1] }, annotations: { bold: true } });\n      remaining = remaining.substring(boldMatch[0].length);\n    } else if (codeMatch) {\n      segments.push({ type: 'text', text: { content: codeMatch[1] }, annotations: { code: true } });\n      remaining = remaining.substring(codeMatch[0].length);\n    } else if (linkMatch) {\n      segments.push({ type: 'text', text: { content: linkMatch[1], link: { url: linkMatch[2] } } });\n      remaining = remaining.substring(linkMatch[0].length);\n    } else if (italicMatch && !remaining.startsWith('**')) {\n      segments.push({ type: 'text', text: { content: italicMatch[1] }, annotations: { italic: true } });\n      remaining = remaining.substring(italicMatch[0].length);\n    } else {\n      const nextSpecial = remaining.search(/\\*|`|\\[/);\n      const plainEnd = nextSpecial === -1 ? remaining.length : nextSpecial;\n      if (plainEnd > 0) {\n        segments.push({ type: 'text', text: { content: remaining.substring(0, plainEnd) } });\n        remaining = remaining.substring(plainEnd);\n      } else {\n        segments.push({ type: 'text', text: { content: remaining[0] } });\n        remaining = remaining.substring(1);\n      }\n    }\n  }\n  \n  return segments.length > 0 ? segments : [{ type: 'text', text: { content: text } }];\n}\n\nfunction splitRichText(segments) {\n  const result = [];\n  let current = [];\n  let currentLen = 0;\n  \n  for (const seg of segments) {\n    const content = seg.text.content;\n    if (currentLen + content.length <= maxLength) {\n      current.push(seg);\n      currentLen += content.length;\n    } else {\n      if (current.length > 0) result.push(current);\n      current = [seg];\n      currentLen = content.length;\n    }\n  }\n  if (current.length > 0) result.push(current);\n  return result;\n}\n\nconst lines = text.split('\\n');\nconst children = [];\n\nfor (const line of lines) {\n  if (children.length >= maxBlocks) break;\n  \n  const trimmed = line.trim();\n  if (!trimmed) continue;\n  \n  if (trimmed.startsWith('# ')) {\n    children.push({ object: 'block', type: 'heading_1', heading_1: { rich_text: parseRichText(trimmed.substring(2)) } });\n  } else if (trimmed.startsWith('## ')) {\n    children.push({ object: 'block', type: 'heading_2', heading_2: { rich_text: parseRichText(trimmed.substring(3)) } });\n  } else if (trimmed.startsWith('### ')) {\n    children.push({ object: 'block', type: 'heading_3', heading_3: { rich_text: parseRichText(trimmed.substring(4)) } });\n  } else if (trimmed === '---' || trimmed === '***' || trimmed === '___') {\n    children.push({ object: 'block', type: 'divider', divider: {} });\n  } else if (trimmed.startsWith('- ')) {\n    const richText = parseRichText(trimmed.substring(2));\n    const chunks = splitRichText(richText);\n    for (const chunk of chunks) {\n      if (children.length >= maxBlocks) break;\n      children.push({ object: 'block', type: 'bulleted_list_item', bulleted_list_item: { rich_text: chunk } });\n    }\n  } else if (/^\\d+\\.\\s/.test(trimmed)) {\n    const content = trimmed.replace(/^\\d+\\.\\s/, '');\n    children.push({ object: 'block', type: 'numbered_list_item', numbered_list_item: { rich_text: parseRichText(content) } });\n  } else {\n    const richText = parseRichText(trimmed);\n    const chunks = splitRichText(richText);\n    for (const chunk of chunks) {\n      if (children.length >= maxBlocks) break;\n      children.push({ object: 'block', type: 'paragraph', paragraph: { rich_text: chunk } });\n    }\n  }\n}\n\nconst wasTruncated = children.length >= maxBlocks;\n\nreturn [{\n  json: {\n    notionChildren: children,\n    blockCount: children.length,\n    wasTruncated: wasTruncated\n  }\n}];"
      },
      "id": "parse-markdown",
      "name": "Parse Markdown to Notion Blocks",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [220, 240]
    }
  ],
  "connections": {
    "Execute Workflow Trigger": {
      "main": [[{ "node": "Parse Markdown to Notion Blocks", "type": "main", "index": 0 }]]
    }
  },
  "settings": {
    "executionOrder": "v1"
  },
  "meta": {
    "notes": "Shared sub-workflow for converting markdown to Notion block format. Input: { markdown: string, maxBlocks?: number, maxLength?: number }. Output: { notionChildren: array, blockCount: number, wasTruncated: boolean }."
  }
}
```

---

## Phase 2: Add New RSS Sources (16 feeds)

This phase adds 16 new RSS sources:
- 1 Official source (Anthropic Blog)
- 7 Medium authors (DevOps/AI focused)
- 8 Substack newsletters (DevOps/Cloud/Engineering)

### 2.1 New RSS Nodes

Add these nodes to `feed-data-ingestion.json`:

#### Official Source

```json
{
  "parameters": { "url": "https://www.anthropic.com/feed", "options": {} },
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

#### Medium Authors (7 feeds)

```json
{
  "parameters": { "url": "https://medium.com/feed/@michael-levan", "options": {} },
  "id": "rss-medium-michael-levan",
  "name": "RSS Medium - Michael Levan",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, -48],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://medium.com/feed/@amanpathakdevops", "options": {} },
  "id": "rss-medium-aman-pathak",
  "name": "RSS Medium - Aman Pathak",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 16],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://medium.com/feed/@devopslearning", "options": {} },
  "id": "rss-medium-prashant-lakhera",
  "name": "RSS Medium - Prashant Lakhera",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 80],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://medium.com/feed/@bdfinst", "options": {} },
  "id": "rss-medium-bryan-finster",
  "name": "RSS Medium - Bryan Finster",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 144],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://medium.com/feed/@kdeepak99", "options": {} },
  "id": "rss-medium-dipak-knvdl",
  "name": "RSS Medium - DiPAK KNVDL",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 208],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://medium.com/feed/@joe.njenga", "options": {} },
  "id": "rss-medium-joe-njenga",
  "name": "RSS Medium - Joe Njenga",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 272],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://medium.com/feed/@codebun", "options": {} },
  "id": "rss-medium-codebun",
  "name": "RSS Medium - CodeBun",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 336],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

#### Substack Newsletters (8 feeds)

```json
{
  "parameters": { "url": "https://devopsbulletin.substack.com/feed", "options": {} },
  "id": "rss-substack-devops-bulletin",
  "name": "RSS Substack - DevOps Bulletin",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 400],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://devopsdaily.substack.com/feed", "options": {} },
  "id": "rss-substack-devops-daily",
  "name": "RSS Substack - DevOps Daily",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 464],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://learnkubernetesweekly.substack.com/feed", "options": {} },
  "id": "rss-substack-learn-k8s",
  "name": "RSS Substack - Learn K8s Weekly",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 528],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://pragmaticengineer.substack.com/feed", "options": {} },
  "id": "rss-substack-pragmatic-eng",
  "name": "RSS Substack - Pragmatic Engineer",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 592],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://bytebytego.substack.com/feed", "options": {} },
  "id": "rss-substack-bytebytego",
  "name": "RSS Substack - ByteByteGo",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 656],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://newsletter.techworld-with-milan.com/feed", "options": {} },
  "id": "rss-substack-techworld-milan",
  "name": "RSS Substack - TechWorld Milan",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 720],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://cloudhandbook.substack.com/feed", "options": {} },
  "id": "rss-substack-cloud-handbook",
  "name": "RSS Substack - Cloud Handbook",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 784],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

```json
{
  "parameters": { "url": "https://refactoring.substack.com/feed", "options": {} },
  "id": "rss-substack-refactoring",
  "name": "RSS Substack - Refactoring",
  "type": "n8n-nodes-base.rssFeedRead",
  "typeVersion": 1.2,
  "position": [280, 848],
  "retryOnFail": true,
  "maxTries": 3,
  "waitBetweenTries": 5000,
  "onError": "continueRegularOutput"
}
```

### 2.2 Update Merge Groups

You'll need to create a new Merge Group (Merge Group 3) for the new feeds, or expand existing groups.

**Option A: Create Merge Group 3** (Recommended - keeps workflow organized)

```json
{
  "parameters": {
    "mode": "combine",
    "combineBy": "combineAll",
    "options": {}
  },
  "id": "merge-group-3",
  "name": "Merge Group 3",
  "type": "n8n-nodes-base.merge",
  "typeVersion": 3.2,
  "position": [560, 400],
  "parameters": { "numberInputs": 16 }
}
```

**Connections for Merge Group 3:**

```json
"RSS Anthropic Blog": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 0 }]] },
"RSS Medium - Michael Levan": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 1 }]] },
"RSS Medium - Aman Pathak": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 2 }]] },
"RSS Medium - Prashant Lakhera": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 3 }]] },
"RSS Medium - Bryan Finster": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 4 }]] },
"RSS Medium - DiPAK KNVDL": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 5 }]] },
"RSS Medium - Joe Njenga": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 6 }]] },
"RSS Medium - CodeBun": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 7 }]] },
"RSS Substack - DevOps Bulletin": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 8 }]] },
"RSS Substack - DevOps Daily": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 9 }]] },
"RSS Substack - Learn K8s Weekly": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 10 }]] },
"RSS Substack - Pragmatic Engineer": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 11 }]] },
"RSS Substack - ByteByteGo": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 12 }]] },
"RSS Substack - TechWorld Milan": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 13 }]] },
"RSS Substack - Cloud Handbook": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 14 }]] },
"RSS Substack - Refactoring": { "main": [[{ "node": "Merge Group 3", "type": "main", "index": 15 }]] }
```

**Update "All Feed Items" merge to include Merge Group 3:**

Add connection from Merge Group 3 to "All Feed Items" merge node.

### 2.3 Update Normalize Function

Add to `getSourceName` function in the normalize Code node:

```javascript
// Official
'anthropic.com': 'Anthropic Blog',

// Medium Authors
'medium.com/@michael-levan': 'Michael Levan (Medium)',
'medium.com/@amanpathakdevops': 'Aman Pathak (Medium)',
'medium.com/@devopslearning': 'Prashant Lakhera (Medium)',
'medium.com/@bdfinst': 'Bryan Finster (Medium)',
'medium.com/@kdeepak99': 'DiPAK KNVDL (Medium)',
'medium.com/@joe.njenga': 'Joe Njenga (Medium)',
'medium.com/@codebun': 'CodeBun (Medium)',

// Substack Newsletters
'devopsbulletin.substack.com': 'DevOps Bulletin',
'devopsdaily.substack.com': 'DevOps Daily',
'learnkubernetesweekly.substack.com': 'Learn K8s Weekly',
'pragmaticengineer.substack.com': 'Pragmatic Engineer',
'bytebytego.substack.com': 'ByteByteGo',
'newsletter.techworld-with-milan.com': 'TechWorld with Milan',
'cloudhandbook.substack.com': 'Cloud Handbook',
'refactoring.substack.com': 'Refactoring',
```

Add to `getTopicTags` function:

```javascript
// Official Anthropic content
if (lowerLink.includes('anthropic.com')) {
  tags.push('claude');
  tags.push('official');
}

// Medium - DevOps focused authors
if (lowerLink.includes('medium.com/@michael-levan') ||
    lowerLink.includes('medium.com/@amanpathakdevops') ||
    lowerLink.includes('medium.com/@devopslearning') ||
    lowerLink.includes('medium.com/@bdfinst') ||
    lowerLink.includes('medium.com/@kdeepak99')) {
  tags.push('devops');
}

// Medium - AI/Claude focused authors
if (lowerLink.includes('medium.com/@joe.njenga') ||
    lowerLink.includes('medium.com/@codebun')) {
  tags.push('claude');
  tags.push('devops');
}

// Substack - DevOps/Kubernetes focused
if (lowerLink.includes('devopsbulletin.substack.com') ||
    lowerLink.includes('devopsdaily.substack.com') ||
    lowerLink.includes('learnkubernetesweekly.substack.com') ||
    lowerLink.includes('cloudhandbook.substack.com')) {
  tags.push('devops');
}

// Substack - Software Engineering
if (lowerLink.includes('pragmaticengineer.substack.com') ||
    lowerLink.includes('bytebytego.substack.com') ||
    lowerLink.includes('newsletter.techworld-with-milan.com') ||
    lowerLink.includes('refactoring.substack.com')) {
  tags.push('devops');
}
```

Add source priority logic:

```javascript
function getSourcePriority(link) {
  const lowerLink = link.toLowerCase();

  // Priority 1: Official sources
  if (lowerLink.includes('anthropic.com') ||
      lowerLink.includes('kubernetes.io') ||
      lowerLink.includes('hashicorp.com')) {
    return 1;
  }

  // Priority 2: Top-tier newsletters (large following, high quality)
  if (lowerLink.includes('pragmaticengineer.substack.com') ||
      lowerLink.includes('bytebytego.substack.com')) {
    return 2;
  }

  // Priority 3: Major blogs/newsletters
  if (lowerLink.includes('infoq.com') ||
      lowerLink.includes('devops.com') ||
      lowerLink.includes('cncf.io') ||
      lowerLink.includes('devopsbulletin.substack.com') ||
      lowerLink.includes('learnkubernetesweekly.substack.com')) {
    return 3;
  }

  // Priority 4: Medium authors and other Substacks
  if (lowerLink.includes('medium.com') ||
      lowerLink.includes('substack.com')) {
    return 4;
  }

  // Priority 5: Community/other
  return 5;
}
```

### 2.4 RSS Source Summary Table

| Source | Type | Category | Topics | Priority |
|--------|------|----------|--------|----------|
| Anthropic Blog | Official | AI | claude, official | 1 |
| Michael Levan | Medium | DevOps/K8s | devops | 4 |
| Aman Pathak | Medium | DevOps/AWS | devops | 4 |
| Prashant Lakhera | Medium | DevOps/AWS | devops | 4 |
| Bryan Finster | Medium | DevOps/CI-CD | devops | 4 |
| DiPAK KNVDL | Medium | DevOps/n8n | devops | 4 |
| Joe Njenga | Medium | AI/Claude | claude, devops | 4 |
| CodeBun | Medium | AI/Tech | claude, devops | 4 |
| DevOps Bulletin | Substack | DevOps | devops | 3 |
| DevOps Daily | Substack | DevOps | devops | 4 |
| Learn K8s Weekly | Substack | Kubernetes | devops | 3 |
| Pragmatic Engineer | Substack | Engineering | devops | 2 |
| ByteByteGo | Substack | System Design | devops | 2 |
| TechWorld Milan | Substack | .NET/Cloud | devops | 4 |
| Cloud Handbook | Substack | Cloud/AWS | devops | 4 |
| Refactoring | Substack | Engineering | devops | 4 |

---

## Phase 3: Content Scraping Integration

### 3.1 Add Scraping After Insert

Add these nodes after "Insert Article" in `feed-data-ingestion.json`:

**Node: Execute Scrape Workflow**

```json
{
  "parameters": {
    "workflowId": {
      "__rl": true,
      "value": "SHARED_SCRAPE_URL_WORKFLOW_ID",
      "mode": "id"
    },
    "workflowInputs": {
      "mappingMode": "defineBelow",
      "value": {
        "url": "={{ $('Process One at a Time').item.json.url }}"
      }
    }
  },
  "id": "execute-scrape",
  "name": "Scrape Full Content",
  "type": "n8n-nodes-base.executeWorkflow",
  "typeVersion": 1.2,
  "position": [2016, 480],
  "onError": "continueRegularOutput"
}
```

**Node: Update with Scraped Content**

```json
{
  "parameters": {
    "operation": "executeQuery",
    "query": "UPDATE feed_articles SET full_content_markdown = $1, external_links = $2::jsonb, image_urls = $3::jsonb, scraped_at = NOW(), scrape_error = $4 WHERE url = $5;",
    "options": {
      "queryParameters": "={{ [$json.markdown || null, JSON.stringify($json.links || []), JSON.stringify($json.images || []), $json.error || null, $('Process One at a Time').item.json.url] }}"
    }
  },
  "id": "update-scraped-content",
  "name": "Update with Scraped Content",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.6,
  "position": [2224, 480],
  "credentials": {
    "postgres": {
      "id": "POSTGRES_CREDENTIAL_ID",
      "name": "Postgres - n8n"
    }
  }
}
```

**Node: Rate Limit Delay**

```json
{
  "parameters": {
    "amount": 1,
    "unit": "seconds"
  },
  "id": "rate-limit-delay",
  "name": "Rate Limit (1s)",
  "type": "n8n-nodes-base.wait",
  "typeVersion": 1.1,
  "position": [2432, 480]
}
```

### 3.2 Update Connections

```json
"Insert Article": {
  "main": [[{ "node": "Scrape Full Content", "type": "main", "index": 0 }]]
},
"Scrape Full Content": {
  "main": [[{ "node": "Update with Scraped Content", "type": "main", "index": 0 }]]
},
"Update with Scraped Content": {
  "main": [[{ "node": "Rate Limit (1s)", "type": "main", "index": 0 }]]
},
"Rate Limit (1s)": {
  "main": [[{ "node": "Process One at a Time", "type": "main", "index": 0 }]]
}
```

---

## Phase 4: AI Relevance Filtering

### 4.1 Add Relevance Evaluation

Add after "Update with Scraped Content" (before Rate Limit):

**Node: Evaluate Relevance (LLM)**

```json
{
  "parameters": {
    "model": {
      "__rl": true,
      "value": "claude-haiku-4-5-20251001",
      "mode": "list"
    },
    "options": {
      "maxTokensToSample": 512
    }
  },
  "id": "claude-haiku-relevance",
  "name": "Claude Haiku",
  "type": "@n8n/n8n-nodes-langchain.lmChatAnthropic",
  "typeVersion": 1.3,
  "position": [2432, 640],
  "credentials": {
    "anthropicApi": {
      "id": "ANTHROPIC_CREDENTIAL_ID",
      "name": "Anthropic"
    }
  }
}
```

**Node: Relevance Chain**

```json
{
  "parameters": {
    "promptType": "define",
    "text": "=You are evaluating content relevance for a news digest.\n\nTopics this article should match:\n{{ $('Process One at a Time').item.json.topic.map(t => '- ' + t).join('\\n') }}\n\nTopic definitions:\n- \"claude\": Content about Claude AI, Anthropic, Claude Code, AI assistants, or LLM development\n- \"devops\": Content about DevOps, Kubernetes, GitOps, IaC, CI/CD, platform engineering, cloud infrastructure\n\nArticle Title: {{ $('Process One at a Time').item.json.title }}\n\nArticle Content (first 2000 chars):\n{{ ($json.markdown || $('Process One at a Time').item.json.content_snippet || '').substring(0, 2000) }}\n\nEvaluate if this content is genuinely relevant to the topics listed above.\n\nRespond with ONLY valid JSON:\n{\"is_relevant\": true/false, \"score\": 0.0-1.0, \"reasoning\": \"1-2 sentence explanation\"}",
    "hasOutputParser": true,
    "options": {}
  },
  "id": "evaluate-relevance",
  "name": "Evaluate Relevance",
  "type": "@n8n/n8n-nodes-langchain.chainLlm",
  "typeVersion": 1.5,
  "position": [2432, 480]
}
```

**Node: Structured Output Parser**

```json
{
  "parameters": {
    "schemaType": "manual",
    "inputSchema": "{\n  \"type\": \"object\",\n  \"properties\": {\n    \"is_relevant\": { \"type\": \"boolean\" },\n    \"score\": { \"type\": \"number\" },\n    \"reasoning\": { \"type\": \"string\" }\n  },\n  \"required\": [\"is_relevant\", \"score\", \"reasoning\"]\n}"
  },
  "id": "relevance-parser",
  "name": "Relevance Parser",
  "type": "@n8n/n8n-nodes-langchain.outputParserStructured",
  "typeVersion": 1.2,
  "position": [2432, 720]
}
```

**Node: Update Relevance Score**

```json
{
  "parameters": {
    "operation": "executeQuery",
    "query": "UPDATE feed_articles SET is_relevant = $1, relevance_score = $2, relevance_reasoning = $3, ai_evaluated_at = NOW() WHERE url = $4;",
    "options": {
      "queryParameters": "={{ [$json.output.is_relevant, $json.output.score, $json.output.reasoning, $('Process One at a Time').item.json.url] }}"
    }
  },
  "id": "update-relevance",
  "name": "Update Relevance Score",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.6,
  "position": [2640, 480],
  "credentials": {
    "postgres": {
      "id": "POSTGRES_CREDENTIAL_ID",
      "name": "Postgres - n8n"
    }
  }
}
```

### 4.2 Update Digest Queries

Update the query in `claude-digest-generator.json`:

```sql
SELECT
    url, title,
    COALESCE(full_content_markdown, content_snippet) as content,
    source_name, category, pub_date, relevance_score,
    metadata->>'author' as author
FROM feed_articles
WHERE 'claude' = ANY(topic)
  AND pub_date >= NOW() - INTERVAL '24 hours'
  AND (is_relevant IS NULL OR is_relevant = TRUE)
  AND (relevance_score IS NULL OR relevance_score >= 0.5)
  AND (last_digest_date IS NULL OR last_digest_date < CURRENT_DATE)
ORDER BY
  COALESCE(relevance_score, 0.5) DESC,
  source_priority ASC,
  pub_date DESC
LIMIT 25;
```

---

## Phase 5: Formatter Integration

### 5.1 Refactor Digest Generators

Replace the "Format for Notion" code node with Execute Workflow:

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
  "typeVersion": 1.2,
  "position": [1072, 224]
}
```

Update the "Create Notion Child Page" node to use the new output:

```json
"jsonBody": "={{ JSON.stringify({ parent: { page_id: '2d38e013c07f80c59d93c9aeb9e271b7' }, properties: { title: [{ text: { content: 'Claude - ' + $('Prepare for LLM').first().json.date } }] }, children: $json.notionChildren }) }}"
```

---

## Implementation Checklist

### Phase 1: Foundation

- [ ] Run database migration `001-feed-articles-enhancements.sql`
- [ ] Import `shared-scrape-url.json` to n8n
- [ ] Configure Firecrawl API credential
- [ ] Test scrape workflow with sample URL
- [ ] Import `shared-format-notion.json` to n8n
- [ ] Test format workflow with sample markdown

### Phase 2: New RSS Sources (16 feeds)

- [ ] Add RSS Anthropic Blog node
- [ ] Add 7 Medium author RSS nodes
- [ ] Add 8 Substack newsletter RSS nodes
- [ ] Create Merge Group 3 with 16 inputs
- [ ] Connect Merge Group 3 to All Feed Items merge
- [ ] Update normalize function with source name mappings
- [ ] Update normalize function with topic tagging logic
- [ ] Add source priority function
- [ ] Test feed ingestion includes new sources

### Phase 3: Scraping Integration

- [ ] Add Execute Scrape workflow node
- [ ] Add Update with Scraped Content node
- [ ] Add Rate Limit delay node
- [ ] Update connections
- [ ] Test full ingestion with scraping

### Phase 4: Relevance Filtering

- [ ] Add Claude Haiku model node
- [ ] Add Evaluate Relevance chain node
- [ ] Add Relevance Parser node
- [ ] Add Update Relevance Score node
- [ ] Update digest queries
- [ ] Test relevance scoring accuracy

### Phase 5: Formatter Integration

- [ ] Update claude-digest-generator to use shared formatter
- [ ] Update devops-digest-generator to use shared formatter
- [ ] Test digest generation end-to-end

### Phase 6: Testing & Deployment

- [ ] Run full integration test
- [ ] Monitor first automated runs
- [ ] Verify Notion pages created correctly
- [ ] Check relevance filtering accuracy

---

## Rollback Plan

Each enhancement is independent. If issues arise:

1. **Scraping fails**: Articles still have `content_snippet`, digests work with existing data
2. **Relevance filter too aggressive**: Run `UPDATE feed_articles SET is_relevant = NULL` and query ignores filter
3. **New RSS feed broken**: Remove from connections, other feeds continue
4. **Shared formatter issues**: Revert to inline code in digest generators

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| RSS sources | 13 feeds | 29 feeds (+16) |
| Content per article | ~500 chars | Full article |
| Digest quality | RSS snippets | Full context |
| Irrelevant articles | ~30% | <10% |
| Official source coverage | 0 | 100% of Anthropic posts |
| Medium authors | 0 | 7 DevOps/AI authors |
| Substack newsletters | 0 | 8 engineering newsletters |
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
