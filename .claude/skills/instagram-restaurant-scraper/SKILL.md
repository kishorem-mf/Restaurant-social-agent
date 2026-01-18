# Instagram Restaurant Profile Scraper Skill

## Command
`/scrape-instagram`

## Purpose
Scrape Instagram profiles for restaurants from CSV using Claude's browser automation (MCP tools).
- Find restaurant Instagram profiles
- Extract profile data (followers, posts, bio, website)
- Save results to CSV with timestamped backups
- Upload backups to S3

## Important: Do NOT Use main.py
The Python script `main.py` triggers Instagram's bot detection. Always use Claude's browser automation with an existing logged-in Chrome session.

## Prerequisites
- **Instagram Login**: User must be logged into Instagram in Chrome browser
- **Chrome Browser**: User's Chrome with active Instagram session
- **Claude-in-Chrome MCP**: Browser automation tools enabled
- **AWS CLI**: Configured for S3 uploads

## Batch Processing Configuration
- **Batch Size**: Configurable via `batch_size` field in marker file (default: 50)
- **Total Entries**: 3000+ in source dataset
- **Sessions Required**: Depends on batch size setting

## Input Files
- **Source CSV**: `/Users/nandu/Kishore/Projects/InstragramScraper/input/data-1767987167575.csv`
  - Columns: name, countrycode, city, zipcode, phonenumber, etc.

### Marker File (Progress Tracking & Configuration)
- **Path**: `/Users/nandu/Kishore/Projects/InstragramScraper/output/markers/scrape_marker.json`
- **Purpose**: Track progress AND configure batch size
- **Format**:
```json
{
  "last_processed_index": 139,
  "last_processed_name": "ERISTAVI WINERY",
  "last_session_timestamp": "2026-01-10T21:15:00",
  "total_processed": 140,
  "total_found": 35,
  "total_not_found": 46,
  "total_empty": 8,
  "total_wrong": 6,
  "batch_size": 50,
  "source_file": "data-1767987167575.csv"
}
```
- **batch_size**: Number of entries to process per session (user configurable, default: 50)

## Output Files

### Primary Results File
- **Path**: `/Users/nandu/Kishore/Projects/InstragramScraper/output/results/instagram_results.csv`
- **Format**: CSV with exact columns:
```csv
restaurant_name,city,phone,instagram_handle,followers,posts,bio,website,status
```
- **Phone Field**: Used as join key with original dataset

### Timestamped Backups
- **Path**: `/Users/nandu/Kishore/Projects/InstragramScraper/output/backups/instagram_results_YYYYMMDD_HHMMSS.csv`
- Created after every append to results file

### S3 Backup
- **Bucket**: `s3://instagram-scraper-backups-kishore/`
- **Files**: Timestamped CSV files uploaded after every update

## Status Values
| Status | Description |
|--------|-------------|
| `FOUND` | Profile found with data extracted |
| `NOT_FOUND` | No matching Instagram profile found |
| `EMPTY_PROFILE` | Profile exists but has no posts/minimal data |
| `WRONG_PROFILE` | Profile found but doesn't match restaurant |
| `NOT_CHECKED` | Not yet processed |

## Execution Steps

### Step 1: Read Marker File
```
1. Read scrape_marker.json to get last_processed_index and batch_size
2. If marker doesn't exist, start from index 0 with default batch_size of 50
3. Calculate batch range: [last_processed_index + 1] to [last_processed_index + batch_size]
```

### Step 2: Get Browser Context
```
1. Call mcp__claude-in-chrome__tabs_context_mcp
2. Verify user is logged into Instagram
3. Use existing tab or create new tab in user's Chrome
```

### Step 3: Load Batch Data
```
1. Read source CSV (input/data-1767987167575.csv)
2. Skip entries 0 to last_processed_index (already processed)
3. Load next [batch_size] entries for this session
4. Load existing results (output/results/instagram_results.csv) for appending
```

### Step 4: For Each Restaurant (Max [batch_size] per session)

#### 4.1 Search Instagram (with Retry Logic)
```
ATTEMPT 1: Direct URL
- Navigate to instagram.com/{clean_restaurant_name}
- Clean name: lowercase, remove special chars, no spaces
- Example: "LUKE S LOBSTER" → instagram.com/lukeslobster

ATTEMPT 2: If NOT_FOUND, try variations
- Remove common suffixes: restaurant, cafe, bar, grill, kitchen
- Add city abbreviation: lukeslobsternyc, lukeslobstersf
- Try abbreviations: "BOBA GUYS" → bobaguys, thebobaguys

ATTEMPT 3: If still NOT_FOUND, search Instagram
- Use search: instagram.com/explore/search/keyword/?q={name}+{city}
- Review top results for matching business
- Check bio/location for confirmation
```

**Search Modification Examples**:
| Original Name | Attempt 1 | Attempt 2 | Attempt 3 |
|--------------|-----------|-----------|-----------|
| LUKE S LOBSTER | lukeslobster | lukeslobsternyc | search "Luke's Lobster New York" |
| KRISPY KRUNCHY CHICKEN | krispykrunchychicken | krispykrunchy | search "Krispy Krunchy SF" |
| BOBA GUYS MILK TEA | bobaguys | thebobaguys | search "Boba Guys San Francisco" |

#### 4.2 Extract Profile Data
```
Using mcp__claude-in-chrome__read_page:
- Instagram handle (@username)
- Follower count
- Post count
- Bio text (first 300 chars)
- Website link
```

#### 4.3 Determine Status
```
- Profile found with posts → FOUND
- Profile found, no posts → EMPTY_PROFILE
- Profile doesn't match restaurant → WRONG_PROFILE
- No profile found → NOT_FOUND
```

### Step 5: Save Results

#### 5.1 Append to Main CSV
```
Update output/results/instagram_results.csv with new data
Maintain exact format:
restaurant_name,city,phone,instagram_handle,followers,posts,bio,website,status
```
- **Phone**: From source CSV (phonenumber column) - used as join key

#### 5.2 Create Timestamped Backup
```bash
# Format: instagram_results_YYYYMMDD_HHMMSS.csv
cp output/results/instagram_results.csv output/backups/instagram_results_$(date +%Y%m%d_%H%M%S).csv
```

#### 5.3 Upload to S3
```bash
# Upload timestamped backup
aws s3 cp output/backups/instagram_results_YYYYMMDD_HHMMSS.csv s3://instagram-scraper-backups-kishore/

# Upload main results to latest/ for KB ingestion
aws s3 cp output/results/instagram_results.csv s3://instagram-scraper-backups-kishore/latest/instagram_results.csv
```

#### 5.4 Sync Knowledge Base
```bash
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id QQJTQJ1VWU \
  --data-source-id RD885PXP2K \
  --region us-east-1
```
This triggers the Bedrock KB to ingest only from `latest/` folder (not all timestamped backups).

### Step 6: Update Marker File
```
Update scrape_marker.json with:
- last_processed_index: index of last entry processed in this batch
- last_processed_name: name of last restaurant processed
- last_session_timestamp: current timestamp
- total_processed: cumulative count of all processed entries
- total_found/not_found/empty/wrong: cumulative status counts
```

**Example marker after processing entries 31-80 (with batch_size=50):**
```json
{
  "last_processed_index": 80,
  "last_processed_name": "RESTAURANT XYZ",
  "last_session_timestamp": "2026-01-10T19:30:00",
  "total_processed": 80,
  "total_found": 25,
  "total_not_found": 45,
  "total_empty": 6,
  "total_wrong": 4,
  "batch_size": 50,
  "source_file": "data-1767987167575.csv"
}
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
| Type | `mcp__claude-in-chrome__computer` (action: type) |
| Find element | `mcp__claude-in-chrome__find` |
| Get text | `mcp__claude-in-chrome__get_page_text` |

## Instagram Selectors

```yaml
# Profile indicators
post_grid: 'article a[href*="/p/"]'
followers_count: 'meta[name="description"]' # Contains "X Followers"
bio_section: 'div[class*="biography"]'
website_link: 'a[href*="l.instagram.com"]'

# Login check
home_icon: 'svg[aria-label="Home"]'
profile_icon: 'span[class*="avatar"]'
```

## Rate Limiting
- Wait 3-5 seconds between profile checks
- Take breaks every 10-15 profiles
- If Instagram shows challenge/captcha, stop and notify user

## Error Handling
| Error | Action |
|-------|--------|
| Login required | Notify user to log in manually |
| Rate limited | Pause 5-10 minutes |
| Page not loading | Retry once, then skip |
| Profile private | Mark as NOT_FOUND |
| Challenge/captcha | Stop scraping, notify user |

## Example Data Row
```csv
"LUKE S LOBSTER","NEW YORK","+1 917-475-9191","@lukeslobster","123K","4316","Traceable + sustainable. Shipped direct from ME","linkin.bio/lukeslobster","FOUND"
```

## Usage
```
User: /scrape-instagram
Claude: I'll scrape Instagram profiles from your CSV.
        First, checking your browser tabs...
        [Uses existing Chrome session where user is logged in]
        [Scrapes profiles one by one]
        [Saves to CSV after each batch]
        [Creates timestamped backup]
        [Uploads to S3]
```

## Workflow Summary
```
1. User invokes /scrape-instagram
2. Read output/markers/scrape_marker.json to get last_processed_index and batch_size
3. Calculate batch: entries [last_index + 1] to [last_index + batch_size]
4. Claude uses MCP tools with user's logged-in Chrome
5. For each restaurant in batch (max [batch_size]):
   - Navigate to potential Instagram profile (3 attempts with variations)
   - Extract data using read_page
   - Determine status
6. Append results to output/results/instagram_results.csv
7. Create timestamped backup to output/backups/
8. Upload backup to S3: s3://instagram-scraper-backups-kishore/
9. Sync Bedrock Knowledge Base (data source: RD885PXP2K)
10. Update output/markers/scrape_marker.json with new last_processed_index
11. Report: "Processed entries X to Y. Next session will start at Y+1"
```

## Session Progress Example
```
With batch_size=50:
Session 1: Entries 0-49   → marker: last_processed_index = 49
Session 2: Entries 50-99  → marker: last_processed_index = 99
Session 3: Entries 100-149 → marker: last_processed_index = 149
...

With batch_size=30:
Session 1: Entries 0-29   → marker: last_processed_index = 29
Session 2: Entries 30-59  → marker: last_processed_index = 59
...
```

## Notes
- Never use main.py - it triggers Instagram's bot detection
- Always use Claude's browser automation with existing logged-in session
- User must be logged into Instagram in Chrome before starting
- Results are saved incrementally to prevent data loss
- S3 backups ensure data durability
- **Batch limit**: Configurable via `batch_size` in marker file (default: 50, adjust based on rate limiting)
- **Resume capability**: Marker file enables seamless resume across sessions
- **Join key**: Phone number field allows joining results with original dataset
- **To change batch size**: Edit `batch_size` value in `scrape_marker.json` before running
