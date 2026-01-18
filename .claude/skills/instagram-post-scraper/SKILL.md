# Instagram Post Scraper Skill

## Command
`/scrape-posts`

## Purpose
Scrape top Instagram posts for food/restaurant related hashtags using Claude's browser automation (MCP tools).
- Search hashtag explore pages
- Extract post data (id, date, engagement, hashtags, caption, image description)
- Save results to CSV with timestamped backups
- Upload backups to S3
- Round-robin through 15 search terms

## Prerequisites
- **Instagram Login**: User must be logged into Instagram in Chrome browser
- **Chrome Browser**: User's Chrome with active Instagram session
- **Claude-in-Chrome MCP**: Browser automation tools enabled
- **AWS CLI**: Configured for S3 uploads

## Configuration
- **Posts Per Session**: 10 posts extracted per run
- **Search Terms**: 15 hashtags, used in round-robin rotation
- **Rate Limiting**: 2-3 seconds between post clicks

## Search Terms

The following 15 hashtags are used in round-robin order:

1. `#foodporn`
2. `#foodie`
3. `#restaurantfood`
4. `#foodphotography`
5. `#instafood`
6. `#foodblogger`
7. `#foodstagram`
8. `#eeeeeats`
9. `#foodlover`
10. `#yummy`
11. `#delicious`
12. `#dinner`
13. `#brunch`
14. `#finedining`
15. `#streetfood`

## Input Files

### Marker File (Progress Tracking)
- **Path**: `/Users/nandu/Kishore/Projects/InstragramScraper/output/markers/post_scrape_marker.json`
- **Purpose**: Track current search term index and session progress
- **Format**:
```json
{
  "current_search_term_index": 0,
  "last_session_timestamp": "2026-01-11T10:00:00",
  "total_posts_scraped": 0,
  "posts_per_term": 10,
  "search_terms": [
    "#foodporn", "#foodie", "#restaurantfood", "#foodphotography",
    "#instafood", "#foodblogger", "#foodstagram", "#eeeeeats",
    "#foodlover", "#yummy", "#delicious", "#dinner",
    "#brunch", "#finedining", "#streetfood"
  ]
}
```

## Output Files

### Primary Results File
- **Path**: `/Users/nandu/Kishore/Projects/InstragramScraper/output/results/post_results.csv`
- **Format**: CSV with exact columns:
```csv
search_term,post_id,creator,posted_date,likes,comments,hashtags,caption,image_description,post_url,status
```

### Timestamped Backups
- **Path**: `/Users/nandu/Kishore/Projects/InstragramScraper/output/backups/post_results_YYYYMMDD_HHMMSS.csv`
- Created after every append to results file

### S3 Backup
- **Bucket**: `s3://instagram-post-scraper-backups-kishore-us/`
- **Files**: Timestamped CSV files uploaded after every update

## Status Values
| Status | Description |
|--------|-------------|
| `SCRAPED` | Post data extracted successfully |
| `PRIVATE` | Post is from private account |
| `ERROR` | Failed to extract post data |

## Execution Steps

### Step 1: Read Marker File
```
1. Read post_scrape_marker.json to get current_search_term_index
2. If marker doesn't exist, create with defaults (index 0)
3. Get current search term: search_terms[current_search_term_index]
```

### Step 2: Get Browser Context
```
1. Call mcp__claude-in-chrome__tabs_context_mcp
2. Verify user is logged into Instagram
3. Use existing tab or create new tab in user's Chrome
```

### Step 3: Navigate to Hashtag Explore Page
```
1. Remove # from search term (e.g., #foodporn → foodporn)
2. Navigate to: instagram.com/explore/tags/{term}/
3. Wait for page to load and show top posts grid
```

### Step 4: Extract Top Posts (10 per session)

#### 4.1 Click on Post
```
1. Find post thumbnail in the grid
2. Click to open post modal
3. Wait for modal to fully load
```

#### 4.2 Extract Post Data
```
Using mcp__claude-in-chrome__read_page:
- post_id: Extract from URL (format: /p/{post_id}/)
- creator: Username of the post author (without @)
- posted_date: From timestamp element
- likes: Heart/like count
- comments: Comment count
- hashtags: All #tags from caption text
- caption: Full caption (truncate to 500 chars)
- image_description: Alt text or describe the image
- post_url: Full Instagram URL
```

#### 4.3 Close Modal and Continue
```
1. Press Escape or click outside to close modal
2. Wait 2-3 seconds (rate limiting)
3. Move to next post in grid
4. Repeat for 10 posts total
```

### Step 5: Save Results

#### 5.1 Append to Main CSV
```
Append 10 new rows to output/results/post_results.csv
Maintain exact format:
search_term,post_id,creator,posted_date,likes,comments,hashtags,caption,image_description,post_url,status
```

#### 5.2 Create Timestamped Backup
```bash
# Format: post_results_YYYYMMDD_HHMMSS.csv
cp output/results/post_results.csv output/backups/post_results_$(date +%Y%m%d_%H%M%S).csv
```

#### 5.3 Upload to S3
```bash
# Upload timestamped backup
aws s3 cp output/backups/post_results_YYYYMMDD_HHMMSS.csv s3://instagram-post-scraper-backups-kishore-us/

# Upload main results to latest/ for KB ingestion
aws s3 cp output/results/post_results.csv s3://instagram-post-scraper-backups-kishore-us/latest/post_results.csv
```

#### 5.4 Sync Knowledge Base
```bash
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id QQJTQJ1VWU \
  --data-source-id Q1SKFUYBH8 \
  --region us-east-1
```
This triggers the Bedrock KB to ingest only from `latest/` folder (not all timestamped backups).

### Step 6: Update Marker File
```
Update post_scrape_marker.json with:
- current_search_term_index: (index + 1) % 15
- last_session_timestamp: current timestamp
- total_posts_scraped: previous + 10
```

## Claude MCP Tools Reference

| Action | MCP Tool |
|--------|----------|
| Get tab context | `mcp__claude-in-chrome__tabs_context_mcp` |
| Create tab | `mcp__claude-in-chrome__tabs_create_mcp` |
| Navigate | `mcp__claude-in-chrome__navigate` |
| Read page | `mcp__claude-in-chrome__read_page` |
| Screenshot | `mcp__claude-in-chrome__computer` (action: screenshot) |
| Click | `mcp__claude-in-chrome__computer` (action: left_click) |
| Press key | `mcp__claude-in-chrome__computer` (action: key) |
| Find element | `mcp__claude-in-chrome__find` |
| Get text | `mcp__claude-in-chrome__get_page_text` |

## Instagram Selectors

```yaml
# Explore page
post_grid: 'article a[href*="/p/"]'
top_posts: 'div[class*="x9f619"] article'

# Post modal
post_modal: 'div[role="dialog"]'
like_count: 'section span'
comment_count: 'ul li span'
caption: 'div[class*="caption"] span'
timestamp: 'time[datetime]'
image: 'img[class*="x5yr21d"]'
```

## Rate Limiting
- Wait 2-3 seconds between post clicks
- 10 posts maximum per session
- One search term per session
- If Instagram shows challenge/captcha, stop and notify user

## Error Handling
| Error | Action |
|-------|--------|
| Login required | Notify user to log in manually |
| Rate limited | Stop session, try next term in new session |
| Post not loading | Skip post, continue to next |
| Private account | Mark as PRIVATE, continue |
| Challenge/captcha | Stop scraping, notify user |

## Example Data Row
```csv
"#foodporn","CzXy123abc","pasta_lover_nyc","2026-01-10","15234","342","#foodporn #pasta #italianfood #nyceats","Amazing homemade pasta at this hidden gem in NYC. The sauce was perfection!","A plate of spaghetti with rich tomato sauce, fresh basil, and parmesan cheese on a rustic wooden table","https://instagram.com/p/CzXy123abc/","SCRAPED"
```

## Usage
```
User: /scrape-posts
Claude: I'll scrape Instagram posts from the current search term.
        First, checking your browser tabs...
        [Uses existing Chrome session where user is logged in]
        [Navigates to hashtag explore page]
        [Clicks and extracts 10 top posts]
        [Saves to CSV after completion]
        [Creates timestamped backup]
        [Uploads to S3]
        [Updates marker for next search term]
```

## Workflow Summary
```
1. User invokes /scrape-posts
2. Read output/markers/post_scrape_marker.json to get current search term
3. Claude uses MCP tools with user's logged-in Chrome
4. Navigate to instagram.com/explore/tags/{term}/
5. For each of top 10 posts:
   - Click to open post modal
   - Extract all data fields
   - Close modal, wait 2-3 seconds
6. Append 10 results to output/results/post_results.csv
7. Create timestamped backup to output/backups/
8. Upload backup to S3: s3://instagram-post-scraper-backups-kishore-us/
9. Sync Bedrock Knowledge Base (data source: Q1SKFUYBH8)
10. Update output/markers/post_scrape_marker.json: increment search term index
11. Report: "Scraped 10 posts for #{term}. Next session will use #{next_term}"
```

## Session Progress Example
```
Session 1: #foodporn     → marker: current_search_term_index = 1
Session 2: #foodie       → marker: current_search_term_index = 2
Session 3: #restaurantfood → marker: current_search_term_index = 3
...
Session 15: #streetfood   → marker: current_search_term_index = 0 (wraps around)
Session 16: #foodporn     → marker: current_search_term_index = 1
```

## Notes
- Always use Claude's browser automation with existing logged-in session
- User must be logged into Instagram in Chrome before starting
- Results are saved after each session (10 posts)
- S3 backups ensure data durability
- Round-robin ensures variety across all 15 search terms
- Each full cycle through all terms = 150 posts
