# Claude News Aggregator - n8n Workflow Implementation Plan

## Overview
Daily automated workflow that aggregates Claude/Anthropic news from Reddit and tech blogs, uses AI to summarize the content, and creates a daily digest in Notion.

## Architecture

```
┌─────────────────┐
│ Schedule Trigger│ (Daily at configured time)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│              Parallel Data Collection                    │
├─────────────────┬─────────────────┬─────────────────────┤
│  Reddit RSS     │  Reddit RSS     │  Blog RSS Feeds     │
│  r/ClaudeAI     │  r/anthropic    │  (HN, Anthropic)    │
└────────┬────────┴────────┬────────┴──────────┬──────────┘
         │                 │                   │
         └────────────────┬┴───────────────────┘
                          ▼
                ┌─────────────────┐
                │  Merge Results  │
                └────────┬────────┘
                         ▼
                ┌─────────────────┐
                │ Filter & Sort   │ (recency, engagement)
                └────────┬────────┘
                         ▼
                ┌─────────────────┐
                │ Claude Haiku    │ (AI Summarization)
                │ 4.5             │
                └────────┬────────┘
                         ▼
                ┌─────────────────┐
                │ Create Notion   │ (Daily digest page)
                │ Page            │
                └─────────────────┘
```

## Data Sources

### Reddit (via RSS - no API key required)
| Subreddit | RSS URL | Content |
|-----------|---------|---------|
| r/ClaudeAI | `https://www.reddit.com/r/ClaudeAI/.rss` | Claude discussions, tips, issues |
| r/anthropic | `https://www.reddit.com/r/anthropic/.rss` | Anthropic company news, releases |

### Tech Blogs (via RSS)
| Source | RSS URL | Content |
|--------|---------|---------|
| Hacker News (Claude) | `https://hnrss.org/newest?q=claude` | Tech community discussions |
| Hacker News (Anthropic) | `https://hnrss.org/newest?q=anthropic` | Company mentions |

> **Note**: Anthropic's official blog doesn't have a public RSS feed. We'll use Hacker News which aggregates Anthropic blog posts when they're shared.

## Workflow Nodes

### 1. Schedule Trigger
- **Node**: `n8n-nodes-base.scheduleTrigger`
- **Config**: Run daily at 8:00 AM (configurable)
- **Purpose**: Initiates the daily aggregation

### 2. Reddit RSS Feeds (Parallel)
- **Node**: `n8n-nodes-base.rssFeedRead` (x2)
- **URLs**:
  - `https://www.reddit.com/r/ClaudeAI/.rss`
  - `https://www.reddit.com/r/anthropic/.rss`
- **Purpose**: Fetch latest Reddit posts

### 3. Hacker News RSS Feeds (Parallel)
- **Node**: `n8n-nodes-base.rssFeedRead` (x2)
- **URLs**:
  - `https://hnrss.org/newest?q=claude`
  - `https://hnrss.org/newest?q=anthropic`
- **Purpose**: Fetch HN posts mentioning Claude/Anthropic

### 4. Merge Node
- **Node**: `n8n-nodes-base.merge`
- **Mode**: Append all items
- **Purpose**: Combine all RSS results into single stream

### 5. Filter & Sort (Code Node)
- **Node**: `n8n-nodes-base.code`
- **Logic**:
  - Filter posts from last 24 hours
  - Sort by engagement (comments/points where available)
  - Remove duplicates
  - Limit to top 20 items
- **Purpose**: Quality filtering and deduplication

### 6. AI Summarization
- **Node**: `@n8n/n8n-nodes-langchain.lmChatAnthropic`
- **Model**: `claude-3-5-haiku-20241022`
- **Prompt**: Generate a structured summary with:
  - Key announcements/updates
  - Interesting discussions
  - Tips and tricks shared
  - Notable issues/bugs reported
- **Purpose**: Create human-readable digest

### 7. Notion Page Creation
- **Node**: `n8n-nodes-base.notion`
- **Operation**: Create page
- **Parent**: Your specified database/page
- **Content**: Formatted markdown summary with:
  - Date header
  - Summary sections
  - Links to original posts
- **Purpose**: Store daily digest

## Credentials Required

| Service | Credential Type | Notes |
|---------|----------------|-------|
| Anthropic | API Key | For Claude Haiku 4.5 summarization |
| Notion | API Key (Integration Token) | For creating pages |

## Notion Setup Required

1. Create a Notion integration at https://www.notion.so/my-integrations
2. Create a database or page for the digests
3. Share the database/page with your integration
4. Provide the database/page ID

## Sample Output Structure (Notion Page)

```markdown
# Claude & Anthropic Daily Digest - 2024-12-24

## Key Updates
- [Summary of major announcements]

## Hot Discussions
- [Top Reddit threads with high engagement]

## Tips & Tricks
- [Useful tips shared by the community]

## Issues & Bugs
- [Notable issues reported]

## All Sources
| Title | Source | Link | Engagement |
|-------|--------|------|------------|
| ... | r/ClaudeAI | [link] | 45 upvotes |
```

## Implementation Steps

1. **Create workflow skeleton** with Schedule Trigger
2. **Add RSS feed nodes** for Reddit and Hacker News
3. **Add Merge node** to combine all feeds
4. **Add Code node** for filtering/sorting logic
5. **Add Anthropic node** for AI summarization
6. **Add Notion node** for page creation
7. **Connect all nodes** and configure connections
8. **Validate workflow** structure
9. **Test with manual trigger**
10. **Activate for daily runs**

## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| Run Time | 08:00 | Daily trigger time |
| Max Posts | 20 | Maximum posts to include |
| Lookback | 24 hours | How far back to fetch |
| Summary Length | ~500 words | AI summary target length |

## Error Handling

- RSS feed failures: Continue with available sources
- AI API errors: Retry once, then create page with raw data
- Notion API errors: Log error, send notification (optional)

## Future Enhancements (Optional)

- Add more RSS sources (dev.to, Medium tags)
- Email notification option
- Slack/Discord integration
- Sentiment analysis
- Trend detection across days
