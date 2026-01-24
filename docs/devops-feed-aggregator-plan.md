# DevOps/Infrastructure Feed Aggregator - n8n Workflow Implementation Plan

## Overview
Daily automated workflow that aggregates DevOps, Kubernetes, and infrastructure news from blogs, tech sites, and community discussions, uses AI to summarize the content, and creates a daily digest in Notion.

## Architecture

```
┌─────────────────┐
│ Schedule Trigger│ (Daily at configured time)
└────────┬────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                        Parallel Data Collection                            │
├─────────────┬─────────────┬─────────────┬─────────────┬──────────────────┤
│  DevOps     │  InfoQ      │  HashiCorp  │  CNCF       │  Hacker News     │
│  Blogs      │  DevOps     │  Blog       │  Blog       │  (k8s/devops)    │
└──────┬──────┴──────┬──────┴──────┬──────┴──────┬──────┴────────┬─────────┘
       │             │             │             │               │
       └─────────────┴─────────────┴─────────────┴───────────────┘
                                   │
                                   ▼
                         ┌─────────────────┐
                         │  Merge Results  │
                         └────────┬────────┘
                                  ▼
                         ┌─────────────────┐
                         │ Filter & Sort   │ (recency, deduplication)
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

### Primary DevOps Blogs (User Selected)
| Source | RSS URL | Content |
|--------|---------|---------|
| DevOps.com | `https://devops.com/feed/` | Industry news, AI/DevOps trends, daily |
| The Agile Admin | `https://theagileadmin.com/feed/` | DevOps practices, leadership |
| InfoQ DevOps | `https://feed.infoq.com/Devops/articles/` | Platform engineering, architecture |
| DevOps Daily | `https://devops-daily.com/feed.xml` | Weekly curated digests |

### Infrastructure/Cloud Native (Recommended - aligns with your stack)
| Source | RSS URL | Content |
|--------|---------|---------|
| HashiCorp Blog | `https://www.hashicorp.com/blog/feed.xml` | Terraform, Vault, Consul (your IaC stack) |
| CNCF Blog | `https://www.cncf.io/blog/feed/` | Kubernetes ecosystem, cloud native |
| Kubernetes Official | `https://kubernetes.io/feed.xml` | K8s releases, best practices |

### Community Discussions
| Source | RSS URL | Content |
|--------|---------|---------|
| Hacker News (DevOps/K8s) | `https://hnrss.org/newest?q=kubernetes+OR+devops+OR+terraform` | Tech community discussions |
| Reddit r/devops | `https://www.reddit.com/r/devops/.rss` | DevOps community |
| Reddit r/kubernetes | `https://www.reddit.com/r/kubernetes/.rss` | K8s community |

## Workflow Nodes

### 1. Schedule Trigger
- **Node**: `n8n-nodes-base.scheduleTrigger`
- **Config**: Run daily at 8:00 AM EST (configurable)
- **Purpose**: Initiates the daily aggregation

### 2. Primary Blog RSS Feeds (Parallel - 5 feeds)
- **Node**: `n8n-nodes-base.rssFeedRead` (x5)
- **URLs**:
  - `https://devops.com/feed/`
  - `https://theagileadmin.com/feed/`
  - `https://feed.infoq.com/Devops/articles/`
  - `https://www.urolime.com/blogs/category/devops/feed/`
  - `https://devops-daily.com/feed.xml`
- **Purpose**: Fetch latest DevOps blog posts

### 3. Infrastructure Blog RSS Feeds (Parallel - 3 feeds)
- **Node**: `n8n-nodes-base.rssFeedRead` (x3)
- **URLs**:
  - `https://www.hashicorp.com/blog/feed.xml`
  - `https://www.cncf.io/blog/feed/`
  - `https://kubernetes.io/feed.xml`
- **Purpose**: Fetch infrastructure/cloud native content

### 4. Community RSS Feeds (Parallel - 3 feeds)
- **Node**: `n8n-nodes-base.rssFeedRead` (x3)
- **URLs**:
  - `https://hnrss.org/newest?q=kubernetes+OR+devops+OR+terraform`
  - `https://www.reddit.com/r/devops/.rss`
  - `https://www.reddit.com/r/kubernetes/.rss`
- **Purpose**: Fetch community discussions

### 5. Merge Node
- **Node**: `n8n-nodes-base.merge`
- **Mode**: Append all items (11 inputs)
- **Purpose**: Combine all RSS results into single stream

### 6. Filter & Sort (Code Node)
- **Node**: `n8n-nodes-base.code`
- **Logic**:
  - Filter posts from last 7 days
  - Sort by date (newest first)
  - Remove duplicates by URL
  - Categorize by source type (Blog, Community, Official)
  - Limit to top 30 items
- **Purpose**: Quality filtering and deduplication

### 7. Prepare for LLM (Code Node)
- **Node**: `n8n-nodes-base.code`
- **Logic**: Consolidate filtered items into structured text for AI
- **Purpose**: Format data for Claude consumption

### 8. AI Summarization
- **Node**: `@n8n/n8n-nodes-langchain.lmChatAnthropic`
- **Model**: `claude-haiku-4-5-20251001`
- **Prompt**: Generate a structured summary with:
  - Key announcements/releases (Kubernetes, Terraform, tools)
  - Infrastructure trends (IaC, GitOps, platform engineering)
  - Community highlights (hot discussions, tips)
  - Tools & tutorials (new tools, guides)
  - Security updates (CVEs, best practices)
- **Purpose**: Create human-readable digest

### 9. Format for Notion (Code Node)
- **Node**: `n8n-nodes-base.code`
- **Logic**: Convert markdown to Notion block format
- **Purpose**: Proper rendering in Notion

### 10. Notion Page Creation
- **Node**: `n8n-nodes-base.httpRequest`
- **Operation**: POST to Notion API
- **Parent**: Shared "Daily News" page (ID: `2d38e013c07f80c59d93c9aeb9e271b7`)
- **Title Format**: `DevOps - YYYY-MM-DD` (e.g., "DevOps - 2024-12-24")
- **Content**: Formatted summary with sections and source links
- **Purpose**: Store daily digest as child page alongside Claude digests

## Credentials Required

| Service | Credential Type | Notes |
|---------|----------------|-------|
| Anthropic | API Key | For Claude Haiku 4.5 summarization |
| Notion | Header Auth | Authorization: Bearer {token} |

## Notion Setup Required

Uses the shared "Daily News" parent page (same as Claude aggregator):
- **Page ID**: `2d38e013c07f80c59d93c9aeb9e271b7`
- **Child Page Naming Convention**:
  - Claude workflow: `Claude - YYYY-MM-DD`
  - DevOps workflow: `DevOps - YYYY-MM-DD`
- No additional Notion setup required (reuses existing integration)

## Sample Output Structure (Notion Page)

```markdown
# DevOps - 2024-12-24

## Key Releases & Announcements
- [Terraform 1.10 released with new provider features]
- [Kubernetes 1.32 enters beta]

## Infrastructure Trends
- [GitOps adoption continues to rise]
- [Platform engineering best practices]

## Community Highlights
- [Hot discussion: "Best practices for multi-cluster management"]
- [Popular tip: "Optimizing Terraform state management"]

## Tools & Tutorials
- [New tool: CRD Wizard for Kubernetes]
- [Tutorial: ArgoCD multi-tenancy setup]

## Security Updates
- [CVE-2024-XXXX: Kubernetes privilege escalation]

## All Sources
| Title | Source | Category | Link |
|-------|--------|----------|------|
| ... | DevOps.com | Blog | [link] |
| ... | r/kubernetes | Community | [link] |
```

## Implementation Steps

1. **Create Notion page** for DevOps Digests (if not exists)
2. **Create workflow skeleton** with Schedule Trigger
3. **Add RSS feed nodes** (11 total in 3 groups)
4. **Add Merge node** to combine all feeds
5. **Add Filter & Sort code node** for deduplication
6. **Add Prepare for LLM code node** for data formatting
7. **Add Anthropic node** for AI summarization
8. **Add Format for Notion code node** for block conversion
9. **Add HTTP Request node** for Notion API
10. **Connect all nodes** and configure connections
11. **Validate workflow** structure
12. **Test with manual trigger**
13. **Activate for daily runs**

## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| Run Time | 08:00 EST | Daily trigger time |
| Max Posts | 30 | Maximum posts to include |
| Lookback | 7 days | How far back to fetch |
| Summary Length | ~800 words | AI summary target length |

## Error Handling

- RSS feed failures: Continue with available sources (continueOnFail: true)
- AI API errors: Retry once, then create page with raw data
- Notion API errors: Log error for review

## Comparison with Claude News Aggregator

| Aspect | Claude Aggregator | DevOps Aggregator |
|--------|-------------------|-------------------|
| Feed Count | 4 | 10 |
| Sources | Reddit, HN | Blogs, Reddit, HN, Official |
| Categories | Claude/Anthropic | DevOps, K8s, IaC, Security |
| Focus | Product updates | Industry trends, tools |
| Summary Sections | 4 | 5 |

## Future Enhancements (Optional)

- Add Slack/Discord notifications for critical updates
- Filter by specific technologies (ArgoCD, Vault, etc.)
- Add sentiment analysis for community discussions
- Weekly/monthly trend reports
- Integration with your GitOps workflow notifications
