# Instagram Analytics Platform - Requirements Document

## Architecture Overview

### Two Separate Flows

```
═══════════════════════════════════════════════════════════════════════════════
FLOW 1: QUERY (Bedrock Agent)
═══════════════════════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────────┐
│                        API Gateway                               │
│                      POST /api/query                             │
└─────────────────────────────┬───────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Amazon Bedrock Agent                          │
│                    (Claude 3 Sonnet)                             │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  • Understands natural language queries                  │    │
│  │  • Orchestrates tool calls + Knowledge Base              │    │
│  │  • Pre/Post processing in Lambda tool                    │    │
│  │  • Generates actionable insights                         │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────┬───────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ↓                               ↓
       ┌─────────────┐                 ┌─────────────┐
       │   Lambda    │                 │   Bedrock   │
       │   query_    │                 │  Knowledge  │
       │   iceberg   │                 │    Base     │
       │   (DuckDB)  │                 │  (Native)   │
       └──────┬──────┘                 └──────┬──────┘
              ↓                               ↓
       ┌─────────────┐                 ┌─────────────┐
       │    Glue     │                 │  S3 Vector  │
       │   Catalog   │                 │    Store    │
       │   Iceberg   │                 │ (Auto-sync) │
       └─────────────┘                 └─────────────┘

═══════════════════════════════════════════════════════════════════════════════
FLOW 2: INGESTION (Event-Driven, Separate from Agent)
═══════════════════════════════════════════════════════════════════════════════

┌─────────────┐      ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│   Scraper   │ ──→  │  S3 Bucket  │ ──→  │   Lambda    │ ──→  │   Iceberg   │
│  (Claude    │      │  (Raw CSV)  │      │  ingest_    │      │   Tables    │
│   Chrome)   │      │             │      │    data     │      │  (Glue)     │
└─────────────┘      └─────────────┘      └─────────────┘      └─────────────┘
                           │
                      S3 Event
                      Trigger
```

---

## 1. Data Ingestion Layer (Flow 2 - Event-Driven)

> **Note**: This flow is SEPARATE from the Bedrock Agent. It runs automatically when scrapers upload files to S3.

### S3 & Lambda Requirements
1. **Ingest Lambda**: Python Lambda to load CSV files to Iceberg tables via DuckDB
2. **S3 Event Trigger**: Configure S3 event notifications to trigger Lambda on new file uploads to `s3://instagram-raw-data/`
3. **File Validation**: Lambda must validate CSV schema before loading (reject malformed files)
4. **Idempotent Processing**: Implement deduplication logic per file type:
   - `post_results.csv` → Unique key: `(post_id, creator)`
   - `instagram_restaurant_results.csv` → Unique key: `(restaurant_name, city, phone)`
5. **Error Handling**: Failed records written to `s3://instagram-raw-data/dlq/` for retry

### Iceberg Table Requirements
6. **Table Schema - Posts**:
```sql
CREATE TABLE instagram_db.posts (
  post_id STRING,
  search_term STRING,
  creator STRING,
  posted_date DATE,
  likes INT,
  comments INT,
  hashtags STRING,
  caption STRING,
  image_description STRING,
  post_url STRING,
  status STRING,
  ingested_at TIMESTAMP
) USING iceberg
PARTITIONED BY (months(posted_date))
```

7. **Table Schema - Restaurants**:
```sql
CREATE TABLE instagram_db.restaurants (
  restaurant_name STRING,
  city STRING,
  phone STRING,
  instagram_handle STRING,
  followers STRING,
  posts_count INT,
  bio STRING,
  website STRING,
  status STRING,
  ingested_at TIMESTAMP
) USING iceberg
PARTITIONED BY (city)
```

8. **Glue Catalog**: Register tables in AWS Glue Data Catalog under database `instagram_db`
9. **Table Location**: `s3://instagram-analytics-lake/iceberg/posts/` and `s3://instagram-analytics-lake/iceberg/restaurants/`
10. **DuckDB Access**: Lambda uses DuckDB with `iceberg` and `httpfs` extensions to read/write Iceberg tables

---

## 2. Infrastructure as Code (Terraform)

### Deployment Requirements
11. **Module Structure**: Organize Terraform into modules: `s3`, `lambda`, `glue`, `iam`, `bedrock`, `api_gateway`
12. **S3 Buckets**:
    - `instagram-raw-data` - CSV uploads (triggers ingest Lambda)
    - `instagram-analytics-lake` - Iceberg warehouse
    - `instagram-knowledge-base` - KB source documents
    - `instagram-terraform-state` - TF state
13. **IAM Roles**:
    - Ingest Lambda role: S3, Glue, CloudWatch
    - Query Lambda role: S3, Glue, CloudWatch
    - Bedrock agent role: Lambda invoke, S3 read, Knowledge Base access
14. **Environment Separation**: Support `dev`, `prod` workspaces via tfvars
15. **State Management**: S3 backend with DynamoDB locking

### Folder Structure
16. **Project Layout**:
```
infrastructure/
├── modules/
│   ├── s3/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── lambda/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── glue/
│   ├── bedrock/
│   ├── api_gateway/
│   └── iam/
├── environments/
│   ├── dev.tfvars
│   └── prod.tfvars
├── main.tf
├── variables.tf
├── outputs.tf
└── backend.tf

lambdas/
├── query_iceberg/          # Agent tool (Flow 1)
│   ├── handler.py
│   ├── requirements.txt
│   └── Dockerfile
└── ingest_data/            # Event-driven (Flow 2)
    ├── handler.py
    └── requirements.txt
```

---

## 3. Bedrock Agent Architecture (Flow 1 - Query)

### Agent Configuration
17. **Foundation Model**: `anthropic.claude-3-sonnet-20240229-v1:0`
18. **Agent Instructions**:
```
You are an Instagram Analytics Agent for restaurant and chef clients.
You help users understand their social media performance and discover trends.

CAPABILITIES:
- Query post engagement metrics (likes, comments, hashtags) via SQL
- Search for similar successful posts via Knowledge Base
- Compare performance against competitors
- Identify trending content patterns

DATA SOURCES:
- Use query_iceberg for structured data (metrics, counts, aggregations)
- Use Knowledge Base for semantic search (similar posts, content patterns)

RESPONSE FORMAT:
- Always include specific numbers and examples
- Provide 3-5 actionable recommendations
- Cite which data source provided the information
- Rate confidence: HIGH (>100 data points), MEDIUM (10-100), LOW (<10)
```

19. **Action Groups**: query_iceberg Lambda only (NOT ingest_data)
20. **Knowledge Base**: Connect to S3 vector store for semantic search (RAG)

### Knowledge Base Configuration
21. **KB Settings**:
```yaml
Name: instagram-posts-kb
Data Source: s3://instagram-knowledge-base/
Embedding Model: amazon.titan-embed-text-v1
Vector Store: Amazon OpenSearch Serverless (managed)
Chunking: Fixed size (300 tokens, 20% overlap)
Sync Schedule: On-demand or daily
```

22. **KB Data Format**: Upload post captions and image descriptions as documents
```json
{
  "post_id": "abc123",
  "content": "Caption: Fresh pasta made daily... | Image: Rustic wooden table with handmade fettuccine...",
  "metadata": {
    "creator": "@italian_kitchen",
    "likes": 5420,
    "city": "NYC",
    "hashtags": "#pasta #italian #homemade"
  }
}
```

---

## 4. Lambda Functions

### 4A. Agent Tool: query_iceberg (Flow 1)
23. **Purpose**: Execute SQL queries against Iceberg tables via DuckDB
24. **Invoked By**: Bedrock Agent
25. **Lambda Config**:
```yaml
Runtime: python3.11
Memory: 1024 MB
Timeout: 60 seconds
Architecture: arm64
Layers:
  - duckdb-layer (custom)
```
26. **Handler Logic** (with pre/post processing):
```python
import duckdb
import json
import re

def handler(event, context):
    query = event['query']

    # ═══ PRE-PROCESSING ═══
    # Validate: block destructive queries
    if re.search(r'\b(DELETE|DROP|INSERT|UPDATE|TRUNCATE)\b', query, re.IGNORECASE):
        return {"error": "Only SELECT queries allowed"}

    # Sanitize: add row limit if missing
    if 'LIMIT' not in query.upper():
        query = query.rstrip(';') + ' LIMIT 100'

    # ═══ EXECUTE ═══
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs; INSTALL iceberg; LOAD iceberg;")
    conn.execute("SET s3_region='us-east-1';")

    result = conn.execute(query).fetchall()
    columns = [desc[0] for desc in conn.description]

    # ═══ POST-PROCESSING ═══
    # Format response
    data = [dict(zip(columns, row)) for row in result]

    # Mask sensitive data if needed
    for row in data:
        if 'phone' in row:
            row['phone'] = row['phone'][:3] + '****' + row['phone'][-2:]

    return {
        "columns": columns,
        "data": data,
        "row_count": len(result),
        "source": "iceberg"
    }
```

### 4B. Ingestion Pipeline: ingest_data (Flow 2)
27. **Purpose**: Load CSV files to Iceberg tables
28. **Invoked By**: S3 Event Trigger (NOT Bedrock Agent)
29. **Trigger Config**:
```yaml
Event: s3:ObjectCreated:*
Bucket: instagram-raw-data
Prefix: uploads/
Suffix: .csv
```
30. **Handler Logic** (with deduplication):
```python
import duckdb
import boto3

def handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']

    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs; INSTALL iceberg; LOAD iceberg;")
    conn.execute("SET s3_region='us-east-1';")

    s3_path = f"s3://{bucket}/{key}"

    if 'post_results' in key:
        # Dedup key: (post_id, creator)
        conn.execute(f"""
            MERGE INTO iceberg_scan('s3://instagram-analytics-lake/iceberg/posts/') AS target
            USING (
                SELECT *, current_timestamp as ingested_at
                FROM read_csv_auto('{s3_path}')
            ) AS source
            ON target.post_id = source.post_id AND target.creator = source.creator
            WHEN NOT MATCHED THEN INSERT *
        """)

    elif 'instagram_restaurant_results' in key:
        # Dedup key: (restaurant_name, city, phone)
        conn.execute(f"""
            MERGE INTO iceberg_scan('s3://instagram-analytics-lake/iceberg/restaurants/') AS target
            USING (
                SELECT *, current_timestamp as ingested_at
                FROM read_csv_auto('{s3_path}')
            ) AS source
            ON target.restaurant_name = source.restaurant_name
               AND target.city = source.city
               AND target.phone = source.phone
            WHEN NOT MATCHED THEN INSERT *
        """)

    return {"status": "success", "file": key}
```

---

## 5. Bedrock Action Group Definition (Flow 1 Only)

31. **OpenAPI Schema for Agent Tools**:
```yaml
openapi: 3.0.0
info:
  title: Instagram Analytics Tools
  version: 1.0.0

paths:
  /query_posts:
    post:
      operationId: queryPosts
      summary: Query Instagram post metrics from Iceberg
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                sql:
                  type: string
                  description: SQL query to execute (SELECT only)
              required: [sql]
      responses:
        200:
          description: Query results

  /query_restaurants:
    post:
      operationId: queryRestaurants
      summary: Query restaurant profiles from Iceberg
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                sql:
                  type: string
                  description: SQL query to execute (SELECT only)
              required: [sql]

  /get_trends:
    post:
      operationId: getTrends
      summary: Get trending hashtags and content patterns
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                city:
                  type: string
                days:
                  type: integer
                  default: 7
```

> **Note**: No ingestion endpoints. Data ingestion is handled by Flow 2 (S3 event trigger).

---

## 6. Session Management (Flow 1)

Bedrock Agents have **built-in session management** for multi-turn conversations.

### How It Works
32. **Session ID**: Client generates unique `sessionId` per conversation
33. **History Storage**: Bedrock stores conversation turns internally (managed)
34. **Context Window**: Agent sees all previous turns within same session
35. **Session TTL**: Configurable idle timeout (default: 1 hour)

### Architecture
```
┌──────────────┐                              ┌─────────────────┐
│   Client     │   POST /api/query            │   API Gateway   │
│   (Web App)  │ ────────────────────────────→│                 │
│              │   {                          └────────┬────────┘
│  Stores:     │     "sessionId": "user123",           │
│  sessionId   │     "query": "top posts"              ↓
└──────────────┘   }                          ┌─────────────────┐
                                              │  Bedrock Agent  │
       ┌──────────────────────────────────────│                 │
       │  Bedrock manages conversation        │  Session Store  │
       │  history per sessionId               │  (Managed)      │
       └──────────────────────────────────────└─────────────────┘
```

### API Gateway Integration
36. **Request Schema**:
```json
{
  "sessionId": "user123-conv-abc",
  "query": "What were the top posts we discussed earlier?"
}
```

37. **Lambda Proxy to Bedrock Agent**:
```python
import boto3

bedrock_agent = boto3.client('bedrock-agent-runtime')

def handler(event, context):
    body = json.loads(event['body'])

    response = bedrock_agent.invoke_agent(
        agentId="AGENT_ID",
        agentAliasId="ALIAS_ID",
        sessionId=body['sessionId'],      # ← Session tracking
        inputText=body['query']
    )

    # Stream response
    result = ""
    for event in response['completion']:
        if 'chunk' in event:
            result += event['chunk']['bytes'].decode()

    return {
        "statusCode": 200,
        "body": json.dumps({"response": result})
    }
```

### Client Responsibility
| Component | Responsibility |
|-----------|---------------|
| **Frontend** | Generate UUID for new conversations, store in localStorage/state |
| **API Gateway** | Pass sessionId to Bedrock Agent |
| **Bedrock Agent** | Manage history automatically |
| **Your Lambdas** | No session logic needed |

### Session Example Flow
```
Turn 1: sessionId="abc123", query="Top 10 posts in NYC"
        → Agent queries Iceberg, returns results

Turn 2: sessionId="abc123", query="Show me similar ones"
        → Agent remembers "NYC posts" context, queries KB

Turn 3: sessionId="abc123", query="Compare to last week"
        → Agent uses full conversation context
```

---

## 7. Cost Estimates (Monthly)

| Component | Flow | Usage | Cost |
|-----------|------|-------|------|
| Bedrock Claude Sonnet | Query | 100K input + 50K output tokens | ~$5 |
| Bedrock Knowledge Base | Query | 5K queries | ~$2.50 |
| Lambda (query_iceberg) | Query | 10K invocations @ 1s | ~$0.20 |
| Lambda (ingest_data) | Ingestion | 100 invocations @ 5s | ~$0.05 |
| S3 Storage | Both | 5GB | ~$0.12 |
| Glue Catalog | Both | 10K objects | ~$0.10 |
| API Gateway | Query | 10K requests | ~$0.04 |
| **Total** | | | **~$8/month** |

---

## 8. Deployment Sequence

### Flow 2 (Ingestion) - Deploy First
1. **Terraform Init**: Create S3 buckets, IAM roles, Glue database
2. **Ingest Lambda**: Deploy ingest_data Lambda with S3 trigger
3. **Glue Tables**: Create Iceberg tables via DuckDB

### Flow 1 (Query) - Deploy Second
4. **Query Lambda**: Deploy query_iceberg Lambda
5. **Knowledge Base**: Create Bedrock KB, configure S3 data source, sync
6. **Bedrock Agent**: Create agent, attach query_iceberg + KB
7. **API Gateway**: Create REST API, connect to Bedrock Agent
8. **Test**: Send sample queries through API

---

## 9. Example Queries (Flow 1)

| User Query | Agent Action | Data Source |
|------------|--------------|-------------|
| "Top 10 posts by likes in NYC" | SQL query | query_iceberg |
| "Find posts similar to 'rustic pasta plating'" | Semantic search | Knowledge Base |
| "Compare @joes_pizza to competitors" | SQL + aggregation | query_iceberg × 2 |
| "Trending hashtags this week in SF" | SQL GROUP BY | query_iceberg |
| "What makes brunch posts successful?" | Semantic + SQL | KB → query_iceberg |
| "Show me viral dessert content" | Semantic search | Knowledge Base |
