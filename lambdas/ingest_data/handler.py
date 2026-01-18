"""
Instagram Data Ingest Lambda
Triggered by S3 events when scrapers upload CSV files to backup buckets.
Ingests data into Parquet format with deduplication for analytics.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any

import boto3
import duckdb

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
s3_client = boto3.client('s3')

# Configuration
ANALYTICS_BUCKET = os.environ.get('ANALYTICS_BUCKET', 'instagram-analytics-lake')
S3_REGION = os.environ.get('S3_REGION', 'us-east-1')
DLQ_PREFIX = 'dlq/'

# Expected CSV columns
POSTS_COLUMNS = [
    'search_term', 'post_id', 'creator', 'posted_date', 'likes',
    'comments', 'hashtags', 'caption', 'image_description', 'post_url', 'status'
]

# Required columns (must exist in CSV)
RESTAURANTS_COLUMNS = [
    'restaurant_name', 'city', 'zip_code', 'phone', 'instagram_handle',
    'followers', 'posts', 'bio', 'website', 'status'
]


def init_duckdb() -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB with required extensions."""
    conn = duckdb.connect()

    # Set home directory for Lambda (required for extension caching)
    conn.execute("SET home_directory='/tmp';")

    # Install and load httpfs extension for S3 access
    conn.execute("INSTALL httpfs; LOAD httpfs;")

    # Configure S3 access (uses Lambda IAM role)
    conn.execute(f"SET s3_region='{S3_REGION}';")

    logger.info("DuckDB initialized with httpfs extension")
    return conn


def validate_csv_schema(conn: duckdb.DuckDBPyConnection, s3_path: str, expected_columns: list) -> bool:
    """Validate that CSV has all expected columns."""
    try:
        # Explicitly set header=true to ensure first row is treated as column names
        result = conn.execute(f"SELECT * FROM read_csv_auto('{s3_path}', header=true) LIMIT 0")
        actual_columns = [desc[0].lower() for desc in result.description]
        expected_lower = [c.lower() for c in expected_columns]

        missing = set(expected_lower) - set(actual_columns)
        if missing:
            logger.error(f"Missing columns: {missing}")
            logger.info(f"Expected: {expected_lower}")
            logger.info(f"Actual: {actual_columns}")
            return False

        logger.info(f"Schema validation passed. Columns: {actual_columns}")
        return True

    except Exception as e:
        logger.error(f"Schema validation failed: {e}")
        return False


def move_to_dlq(source_bucket: str, key: str, error_msg: str) -> None:
    """Move failed file to dead letter queue in analytics bucket."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_key = key.replace('/', '_')
    dlq_key = f"{DLQ_PREFIX}{timestamp}_{source_bucket}_{safe_key}"

    try:
        # Copy to analytics bucket DLQ with error metadata
        s3_client.copy_object(
            Bucket=ANALYTICS_BUCKET,
            CopySource={'Bucket': source_bucket, 'Key': key},
            Key=dlq_key,
            Metadata={
                'error': error_msg[:256],  # S3 metadata has size limits
                'source_bucket': source_bucket,
                'original_key': key,
                'failed_at': timestamp
            },
            MetadataDirective='REPLACE'
        )
        logger.info(f"Moved to DLQ: s3://{ANALYTICS_BUCKET}/{dlq_key}")

    except Exception as e:
        logger.error(f"Failed to move to DLQ: {e}")


def parse_k_m_value(column: str) -> str:
    """
    Generate DuckDB SQL to convert K/M suffixed values to integers.

    Handles decimal values: "43.8K" → 43800, "1.2M" → 1200000
    """
    return f"""
        CASE
            WHEN CAST({column} AS VARCHAR) LIKE '%M'
                THEN TRY_CAST(CAST(REPLACE(CAST({column} AS VARCHAR), 'M', '') AS DOUBLE) * 1000000 AS INTEGER)
            WHEN CAST({column} AS VARCHAR) LIKE '%K'
                THEN TRY_CAST(CAST(REPLACE(CAST({column} AS VARCHAR), 'K', '') AS DOUBLE) * 1000 AS INTEGER)
            ELSE TRY_CAST({column} AS INTEGER)
        END"""


def ingest_posts(conn: duckdb.DuckDBPyConnection, s3_path: str) -> dict:
    """
    Ingest posts CSV with deduplication on (post_id, creator).
    Writes to Parquet format in S3 analytics bucket.
    """
    if not validate_csv_schema(conn, s3_path, POSTS_COLUMNS):
        raise ValueError("Invalid posts CSV schema")

    parquet_base = f"s3://{ANALYTICS_BUCKET}/data/posts"
    ingestion_date = datetime.now().strftime('%Y-%m-%d')
    ingestion_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = f"{parquet_base}/ingestion_date={ingestion_date}/batch_{ingestion_ts}.parquet"

    # Read new data with transformations
    # IMPORTANT: Explicitly cast all columns to ensure consistent Parquet schema
    new_data_query = f"""
        SELECT
            CAST(search_term AS VARCHAR) as search_term,
            CAST(post_id AS VARCHAR) as post_id,
            CAST(creator AS VARCHAR) as creator,
            CAST(posted_date AS VARCHAR) as posted_date,
            {parse_k_m_value('likes')} as likes,
            {parse_k_m_value('comments')} as comments,
            CAST(hashtags AS VARCHAR) as hashtags,
            CAST(caption AS VARCHAR) as caption,
            CAST(image_description AS VARCHAR) as image_description,
            CAST(post_url AS VARCHAR) as post_url,
            CAST(status AS VARCHAR) as status,
            current_timestamp as ingested_at
        FROM read_csv_auto('{s3_path}', header=true)
    """

    # Try to deduplicate against existing data
    try:
        existing_count = conn.execute(f"""
            SELECT COUNT(DISTINCT (post_id, creator))
            FROM read_parquet('{parquet_base}/*/*.parquet')
        """).fetchone()[0]

        # Deduplicate: only insert rows not already in existing data
        dedup_query = f"""
            SELECT new.* FROM ({new_data_query}) new
            WHERE NOT EXISTS (
                SELECT 1 FROM read_parquet('{parquet_base}/*/*.parquet') existing
                WHERE existing.post_id = new.post_id AND existing.creator = new.creator
            )
        """
        rows_added = conn.execute(f"SELECT COUNT(*) FROM ({dedup_query})").fetchone()[0]

        if rows_added > 0:
            conn.execute(f"COPY ({dedup_query}) TO '{output_path}' (FORMAT PARQUET)")
            logger.info(f"Posts ingestion complete. Added {rows_added} new rows (deduplicated)")
        else:
            logger.info("No new unique posts to add (all duplicates)")

    except Exception as e:
        # No existing data - insert all
        logger.info(f"No existing posts data found, inserting all rows: {e}")
        rows_added = conn.execute(f"SELECT COUNT(*) FROM ({new_data_query})").fetchone()[0]
        conn.execute(f"COPY ({new_data_query}) TO '{output_path}' (FORMAT PARQUET)")
        existing_count = 0

    return {
        "status": "ingested",
        "rows_added": rows_added,
        "output_path": output_path
    }


def ingest_restaurants(conn: duckdb.DuckDBPyConnection, s3_path: str) -> dict:
    """
    Ingest restaurants CSV with deduplication on (restaurant_name, city, phone).
    Writes to Parquet format in S3 analytics bucket.
    """
    if not validate_csv_schema(conn, s3_path, RESTAURANTS_COLUMNS):
        raise ValueError("Invalid restaurants CSV schema")

    parquet_base = f"s3://{ANALYTICS_BUCKET}/data/restaurants"
    ingestion_date = datetime.now().strftime('%Y-%m-%d')
    ingestion_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = f"{parquet_base}/ingestion_date={ingestion_date}/batch_{ingestion_ts}.parquet"

    # Read new data with transformations
    # IMPORTANT: Explicitly cast all columns to ensure consistent Parquet schema
    new_data_query = f"""
        SELECT
            CAST(restaurant_name AS VARCHAR) as restaurant_name,
            CAST(city AS VARCHAR) as city,
            CAST(zip_code AS VARCHAR) as zip_code,
            CAST(phone AS VARCHAR) as phone,
            CAST(instagram_handle AS VARCHAR) as instagram_handle,
            {parse_k_m_value('followers')} as followers,
            {parse_k_m_value('posts')} as posts_count,
            CAST(bio AS VARCHAR) as bio,
            CAST(website AS VARCHAR) as website,
            CAST(status AS VARCHAR) as status,
            current_timestamp as ingested_at
        FROM read_csv_auto('{s3_path}', header=true)
    """

    # Try to deduplicate against existing data
    try:
        existing_count = conn.execute(f"""
            SELECT COUNT(DISTINCT (restaurant_name, city, phone))
            FROM read_parquet('{parquet_base}/*/*.parquet')
        """).fetchone()[0]

        # Deduplicate: only insert rows not already in existing data
        dedup_query = f"""
            SELECT new.* FROM ({new_data_query}) new
            WHERE NOT EXISTS (
                SELECT 1 FROM read_parquet('{parquet_base}/*/*.parquet') existing
                WHERE existing.restaurant_name = new.restaurant_name
                  AND existing.city = new.city
                  AND existing.phone = new.phone
            )
        """
        rows_added = conn.execute(f"SELECT COUNT(*) FROM ({dedup_query})").fetchone()[0]

        if rows_added > 0:
            conn.execute(f"COPY ({dedup_query}) TO '{output_path}' (FORMAT PARQUET)")
            logger.info(f"Restaurants ingestion complete. Added {rows_added} new rows (deduplicated)")
        else:
            logger.info("No new unique restaurants to add (all duplicates)")

    except Exception as e:
        # No existing data - insert all
        logger.info(f"No existing restaurant data found, inserting all rows: {e}")
        rows_added = conn.execute(f"SELECT COUNT(*) FROM ({new_data_query})").fetchone()[0]
        conn.execute(f"COPY ({new_data_query}) TO '{output_path}' (FORMAT PARQUET)")
        existing_count = 0

    return {
        "status": "ingested",
        "rows_added": rows_added,
        "output_path": output_path
    }


def handler(event: dict, context: Any) -> dict:
    """
    Lambda handler for S3 event trigger.

    Triggered when CSV files are uploaded to:
    - instagram-scraper-backups-kishore (restaurants)
    - instagram-post-scraper-backups-kishore-us (posts)
    """
    logger.info(f"Event received: {json.dumps(event)}")

    # Extract S3 event details
    try:
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']

        # URL decode the key (S3 events URL-encode special characters)
        import urllib.parse
        key = urllib.parse.unquote_plus(key)

    except (KeyError, IndexError) as e:
        logger.error(f"Invalid event structure: {e}")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid S3 event structure"})
        }

    s3_path = f"s3://{bucket}/{key}"
    logger.info(f"Processing file: {s3_path}")

    # Skip non-CSV files
    if not key.lower().endswith('.csv'):
        logger.info(f"Skipping non-CSV file: {key}")
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "skipped", "reason": "Not a CSV file"})
        }

    # Skip files in latest/ folder (used only for KB ingestion, same data as timestamped backups)
    if key.startswith('latest/'):
        logger.info(f"Skipping KB-only file: {key}")
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "skipped", "reason": "KB-only file in latest/"})
        }

    try:
        conn = init_duckdb()

        # Determine file type based on filename pattern
        if 'post_results' in key.lower():
            result = ingest_posts(conn, s3_path)
            table_type = "posts"

        elif 'instagram_results' in key.lower():
            result = ingest_restaurants(conn, s3_path)
            table_type = "restaurants"

        else:
            logger.warning(f"Unknown file type: {key}")
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": "Unknown file type",
                    "file": key,
                    "hint": "File name must contain 'post_results' or 'instagram_results'"
                })
            }

        logger.info(f"Successfully ingested {table_type} from {bucket}/{key}")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "success",
                "table": table_type,
                "source_bucket": bucket,
                "source_key": key,
                **result
            })
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ingestion failed: {error_msg}")

        # Move failed file to DLQ
        move_to_dlq(bucket, key, error_msg)

        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": error_msg,
                "source_bucket": bucket,
                "source_key": key,
                "dlq": f"s3://{ANALYTICS_BUCKET}/{DLQ_PREFIX}"
            })
        }
