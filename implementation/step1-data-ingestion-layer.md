# Step 1: Data Ingestion Layer - Implementation Plan

## Overview

Event-driven pipeline that automatically ingests CSV files into Iceberg tables when uploaded to S3 by scrapers.

```
Scraper → S3 (Existing Backup Buckets) → S3 Event → Lambda (ingest_data) → Iceberg Tables (Analytics Lake)
```

---

## S3 Bucket Architecture

### Source Buckets (Already Exist - Used by Scrapers)
| Bucket | Purpose | Scraper Command |
|--------|---------|-----------------|
| `instagram-scraper-backups-kishore` | Restaurant CSV backups | `/scrape-instagram` |
| `instagram-post-scraper-backups-kishore` | Post CSV backups | `/scrape-posts` |

### Destination Bucket (To Be Created)
| Bucket | Purpose |
|--------|---------|
| `instagram-analytics-lake` | Iceberg tables for analytics |

---

## Pre-requisites

| Requirement | Status |
|-------------|--------|
| AWS Account with permissions | ☐ |
| AWS CLI configured locally | ☐ |
| Terraform installed (v1.5+) | ☐ |
| Python 3.11 installed | ☐ |
| Docker installed (for Lambda layer) | ☐ |
| Existing scraper S3 buckets | ✅ |

---

## Implementation Tasks

### Task 1.1: Create Analytics Lake S3 Bucket

**Files to create:** `infrastructure/modules/s3/main.tf`

```hcl
# Analytics lake bucket (Iceberg destination) - NEW
resource "aws_s3_bucket" "analytics_lake" {
  bucket = "instagram-analytics-lake"
}

# Enable versioning for Iceberg time-travel
resource "aws_s3_bucket_versioning" "analytics_lake" {
  bucket = aws_s3_bucket.analytics_lake.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Reference existing scraper buckets (data sources)
data "aws_s3_bucket" "restaurant_backups" {
  bucket = "instagram-scraper-backups-kishore"
}

data "aws_s3_bucket" "post_backups" {
  bucket = "instagram-post-scraper-backups-kishore"
}
```

**Bucket Structure:**
```
# SOURCE (Existing - Scrapers write here)
instagram-scraper-backups-kishore/
└── instagram_results_*.csv         # Restaurant profile data

instagram-post-scraper-backups-kishore/
└── post_results_*.csv              # Post engagement data

# DESTINATION (New - Lambda writes Iceberg here)
instagram-analytics-lake/
├── iceberg/
│   ├── posts/                      # Iceberg table
│   └── restaurants/                # Iceberg table
└── dlq/                            # Dead letter queue for failed files
```

---

### Task 1.2: Create Glue Database & Catalog

**Files to create:** `infrastructure/modules/glue/main.tf`

```hcl
resource "aws_glue_catalog_database" "instagram_db" {
  name        = "instagram_db"
  description = "Instagram analytics database"
}
```

---

### Task 1.3: Create IAM Role for Lambda

**Files to create:** `infrastructure/modules/iam/lambda_ingest_role.tf`

```hcl
resource "aws_iam_role" "lambda_ingest" {
  name = "instagram-ingest-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_ingest_policy" {
  name = "instagram-ingest-lambda-policy"
  role = aws_iam_role.lambda_ingest.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          # Source buckets (existing scraper backups)
          "arn:aws:s3:::instagram-scraper-backups-kishore",
          "arn:aws:s3:::instagram-scraper-backups-kishore/*",
          "arn:aws:s3:::instagram-post-scraper-backups-kishore",
          "arn:aws:s3:::instagram-post-scraper-backups-kishore/*",
          # Destination bucket (analytics lake)
          "arn:aws:s3:::instagram-analytics-lake",
          "arn:aws:s3:::instagram-analytics-lake/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["glue:GetDatabase", "glue:GetTable", "glue:CreateTable", "glue:UpdateTable"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}
```

---

### Task 1.4: Build DuckDB Lambda Layer

**Files to create:** `lambdas/layers/duckdb/build.sh`

```bash
#!/bin/bash
set -e

LAYER_DIR="python"
mkdir -p $LAYER_DIR

pip install duckdb==0.10.0 -t $LAYER_DIR

zip -r duckdb-layer.zip $LAYER_DIR
echo "Layer built: duckdb-layer.zip"
```

---

### Task 1.5: Create Ingest Lambda Function

**Files to create:** `lambdas/ingest_data/handler.py`

```python
import json
import logging
import duckdb
import boto3
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

# Destination bucket for Iceberg tables
ANALYTICS_BUCKET = "instagram-analytics-lake"
DLQ_PREFIX = "dlq/"
AWS_REGION = "us-east-1"

POSTS_COLUMNS = ["search_term", "post_id", "creator", "posted_date", "likes",
                 "comments", "hashtags", "caption", "image_description", "post_url", "status"]

RESTAURANTS_COLUMNS = ["restaurant_name", "city", "phone", "instagram_handle",
                       "followers", "posts", "bio", "website", "status"]


def validate_csv_schema(conn, s3_path: str, expected_columns: list) -> bool:
    """Validate CSV has expected columns."""
    try:
        result = conn.execute(f"SELECT * FROM read_csv_auto('{s3_path}') LIMIT 0")
        actual = [desc[0].lower() for desc in result.description]
        missing = set([c.lower() for c in expected_columns]) - set(actual)
        if missing:
            logger.error(f"Missing columns: {missing}")
            return False
        return True
    except Exception as e:
        logger.error(f"Schema validation failed: {e}")
        return False


def move_to_dlq(source_bucket: str, key: str, error_msg: str):
    """Move failed file to dead letter queue in analytics bucket."""
    dlq_key = f"{DLQ_PREFIX}{datetime.now().strftime('%Y%m%d_%H%M%S')}_{source_bucket}_{key.replace('/', '_')}"

    # Copy to analytics bucket DLQ
    s3_client.copy_object(
        Bucket=ANALYTICS_BUCKET,
        CopySource=f"{source_bucket}/{key}",
        Key=dlq_key,
        Metadata={"error": error_msg[:256], "source_bucket": source_bucket},
        MetadataDirective="REPLACE"
    )
    logger.info(f"Moved to DLQ: s3://{ANALYTICS_BUCKET}/{dlq_key}")


def init_duckdb():
    """Initialize DuckDB with extensions."""
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    conn.execute(f"SET s3_region='{AWS_REGION}';")
    return conn


def ingest_posts(conn, s3_path: str) -> dict:
    """Ingest posts CSV with deduplication on (post_id, creator)."""
    if not validate_csv_schema(conn, s3_path, POSTS_COLUMNS):
        raise ValueError("Invalid posts CSV schema")

    conn.execute(f"""
        MERGE INTO iceberg_scan('s3://{ANALYTICS_BUCKET}/iceberg/posts/') AS target
        USING (
            SELECT *, current_timestamp as ingested_at
            FROM read_csv_auto('{s3_path}')
        ) AS source
        ON target.post_id = source.post_id AND target.creator = source.creator
        WHEN NOT MATCHED THEN INSERT *
    """)
    return {"status": "merged"}


def ingest_restaurants(conn, s3_path: str) -> dict:
    """Ingest restaurants CSV with deduplication on (restaurant_name, city, phone)."""
    if not validate_csv_schema(conn, s3_path, RESTAURANTS_COLUMNS):
        raise ValueError("Invalid restaurants CSV schema")

    conn.execute(f"""
        MERGE INTO iceberg_scan('s3://{ANALYTICS_BUCKET}/iceberg/restaurants/') AS target
        USING (
            SELECT *, current_timestamp as ingested_at
            FROM read_csv_auto('{s3_path}')
        ) AS source
        ON target.restaurant_name = source.restaurant_name
           AND target.city = source.city
           AND target.phone = source.phone
        WHEN NOT MATCHED THEN INSERT *
    """)
    return {"status": "merged"}


def handler(event, context):
    """Lambda handler for S3 event trigger."""
    logger.info(f"Event: {json.dumps(event)}")

    record = event['Records'][0]
    bucket = record['s3']['bucket']['name']
    key = record['s3']['object']['key']
    s3_path = f"s3://{bucket}/{key}"

    try:
        conn = init_duckdb()

        # Determine file type based on filename pattern
        if 'post_results' in key:
            result = ingest_posts(conn, s3_path)
            table_type = "posts"
        elif 'instagram_results' in key:
            result = ingest_restaurants(conn, s3_path)
            table_type = "restaurants"
        else:
            logger.warning(f"Unknown file type: {key}")
            return {"statusCode": 400, "body": json.dumps({"error": "Unknown file type"})}

        logger.info(f"Ingested {table_type} from {bucket}: {result}")
        return {"statusCode": 200, "body": json.dumps({"status": "success", "table": table_type, "source": bucket})}

    except Exception as e:
        logger.error(f"Error: {e}")
        move_to_dlq(bucket, key, str(e))
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
```

**Files to create:** `lambdas/ingest_data/requirements.txt`

```
duckdb==0.10.0
boto3>=1.28.0
```

---

### Task 1.6: Configure S3 Event Triggers

**Files to create:** `infrastructure/modules/lambda/ingest_trigger.tf`

```hcl
resource "aws_lambda_function" "ingest_data" {
  function_name = "instagram-ingest-data"
  role          = var.lambda_role_arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 1024
  layers        = [var.duckdb_layer_arn]

  filename         = data.archive_file.ingest_lambda.output_path
  source_code_hash = data.archive_file.ingest_lambda.output_base64sha256
}

# Permission for restaurant backups bucket
resource "aws_lambda_permission" "allow_s3_restaurants" {
  statement_id  = "AllowS3InvokeRestaurants"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_data.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::instagram-scraper-backups-kishore"
}

# Permission for post backups bucket
resource "aws_lambda_permission" "allow_s3_posts" {
  statement_id  = "AllowS3InvokePosts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_data.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = "arn:aws:s3:::instagram-post-scraper-backups-kishore"
}

# Trigger on restaurant backups bucket
resource "aws_s3_bucket_notification" "restaurant_trigger" {
  bucket = "instagram-scraper-backups-kishore"

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest_data.arn
    events              = ["s3:ObjectCreated:*"]
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.allow_s3_restaurants]
}

# Trigger on post backups bucket
resource "aws_s3_bucket_notification" "post_trigger" {
  bucket = "instagram-post-scraper-backups-kishore"

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest_data.arn
    events              = ["s3:ObjectCreated:*"]
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.allow_s3_posts]
}
```

---

### Task 1.7: Create Initial Iceberg Tables

**Files to create:** `scripts/create_iceberg_tables.py`

```python
"""Run once to create Iceberg tables."""
import duckdb

ANALYTICS_BUCKET = "instagram-analytics-lake"

def create_tables():
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs; INSTALL iceberg; LOAD iceberg;")
    conn.execute("SET s3_region='us-east-1';")

    # Posts table
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS iceberg_scan('s3://{ANALYTICS_BUCKET}/iceberg/posts/') (
            post_id VARCHAR, search_term VARCHAR, creator VARCHAR,
            posted_date DATE, likes INTEGER, comments INTEGER,
            hashtags VARCHAR, caption VARCHAR, image_description VARCHAR,
            post_url VARCHAR, status VARCHAR, ingested_at TIMESTAMP
        )
    """)

    # Restaurants table
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS iceberg_scan('s3://{ANALYTICS_BUCKET}/iceberg/restaurants/') (
            restaurant_name VARCHAR, city VARCHAR, phone VARCHAR,
            instagram_handle VARCHAR, followers VARCHAR, posts_count INTEGER,
            bio VARCHAR, website VARCHAR, status VARCHAR, ingested_at TIMESTAMP
        )
    """)

    print("Tables created!")

if __name__ == "__main__":
    create_tables()
```

---

## Folder Structure

```
InstragramScraper/
├── implementation/
│   └── step1-data-ingestion-layer.md
├── infrastructure/
│   └── modules/
│       ├── s3/main.tf
│       ├── glue/main.tf
│       ├── iam/lambda_ingest_role.tf
│       └── lambda/ingest_trigger.tf
├── lambdas/
│   ├── layers/duckdb/build.sh
│   └── ingest_data/
│       ├── handler.py
│       └── requirements.txt
└── scripts/
    └── create_iceberg_tables.py
```

---

## Deployment Steps

```bash
# 1. Build Lambda layer
cd lambdas/layers/duckdb && ./build.sh

# 2. Deploy infrastructure (creates analytics bucket + triggers on existing buckets)
cd infrastructure && terraform init && terraform apply

# 3. Create Iceberg tables
python scripts/create_iceberg_tables.py

# 4. Test - Scraper backup will auto-trigger ingestion
# OR manually copy a test file:
aws s3 cp output/backups/post_results_20260113_175319.csv s3://instagram-post-scraper-backups-kishore/
```

---

## Verification Checklist

| Test | Expected |
|------|----------|
| Existing scraper buckets accessible | `aws s3 ls instagram-scraper-backups-kishore` works |
| Analytics lake bucket created | `aws s3 ls instagram-analytics-lake` works |
| Lambda deployed | Function visible in console |
| Restaurant backup triggers Lambda | Upload to `instagram-scraper-backups-kishore` → CloudWatch logs |
| Post backup triggers Lambda | Upload to `instagram-post-scraper-backups-kishore` → CloudWatch logs |
| Data in Iceberg | Query returns rows |
| Dedup works | Same file twice = no duplicates |
| DLQ works | Bad file moved to `instagram-analytics-lake/dlq/` |

---

## Data Flow Summary

```
                    ┌─────────────────────────────────────┐
                    │      EXISTING SCRAPER BUCKETS       │
                    └─────────────────────────────────────┘
                                      │
        ┌─────────────────────────────┴─────────────────────────────┐
        │                                                           │
        ▼                                                           ▼
┌───────────────────────────┐                         ┌───────────────────────────┐
│ instagram-scraper-        │                         │ instagram-post-scraper-   │
│ backups-kishore           │                         │ backups-kishore           │
│ (restaurants)             │                         │ (posts)                   │
└───────────────────────────┘                         └───────────────────────────┘
        │                                                           │
        │  S3 Event: ObjectCreated                                  │  S3 Event: ObjectCreated
        │  Filter: *.csv                                            │  Filter: *.csv
        │                                                           │
        └─────────────────────────────┬─────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │   Lambda: instagram-ingest-data     │
                    │   (DuckDB + Iceberg Extension)      │
                    └─────────────────────────────────────┘
                                      │
                                      │  MERGE INTO (dedup)
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │    instagram-analytics-lake         │
                    │    (NEW - Iceberg Tables)           │
                    ├─────────────────────────────────────┤
                    │  /iceberg/posts/                    │
                    │  /iceberg/restaurants/              │
                    │  /dlq/                              │
                    └─────────────────────────────────────┘
```
