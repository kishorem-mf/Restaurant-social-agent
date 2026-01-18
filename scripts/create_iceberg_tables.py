#!/usr/bin/env python3
"""
Create Iceberg Tables Script
Run once to initialize Iceberg tables in the analytics lake bucket.

Prerequisites:
- AWS credentials configured (aws configure)
- Analytics lake bucket created (instagram-analytics-lake)

Usage:
    python scripts/create_iceberg_tables.py
"""

import sys

import duckdb


ANALYTICS_BUCKET = "instagram-analytics-lake"
AWS_REGION = "us-east-1"


def create_tables():
    """Create Iceberg tables for posts and restaurants."""

    print("Initializing DuckDB with extensions...")
    conn = duckdb.connect()

    # Install and load required extensions
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")

    # Configure S3 region
    conn.execute(f"SET s3_region='{AWS_REGION}';")

    print(f"Creating tables in s3://{ANALYTICS_BUCKET}/iceberg/...")

    # Create Posts table
    print("\n1. Creating posts table...")
    posts_path = f"s3://{ANALYTICS_BUCKET}/iceberg/posts/"

    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS iceberg_scan('{posts_path}') (
                search_term VARCHAR,
                post_id VARCHAR,
                creator VARCHAR,
                posted_date DATE,
                likes INTEGER,
                comments INTEGER,
                hashtags VARCHAR,
                caption VARCHAR,
                image_description VARCHAR,
                post_url VARCHAR,
                status VARCHAR,
                ingested_at TIMESTAMP
            )
        """)
        print(f"   ✓ Posts table created at {posts_path}")
    except Exception as e:
        print(f"   ✗ Failed to create posts table: {e}")
        sys.exit(1)

    # Create Restaurants table
    print("\n2. Creating restaurants table...")
    restaurants_path = f"s3://{ANALYTICS_BUCKET}/iceberg/restaurants/"

    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS iceberg_scan('{restaurants_path}') (
                restaurant_name VARCHAR,
                city VARCHAR,
                phone VARCHAR,
                instagram_handle VARCHAR,
                followers VARCHAR,
                posts_count INTEGER,
                bio VARCHAR,
                website VARCHAR,
                status VARCHAR,
                ingested_at TIMESTAMP
            )
        """)
        print(f"   ✓ Restaurants table created at {restaurants_path}")
    except Exception as e:
        print(f"   ✗ Failed to create restaurants table: {e}")
        sys.exit(1)

    # Verify tables
    print("\n3. Verifying tables...")

    try:
        posts_count = conn.execute(
            f"SELECT COUNT(*) FROM iceberg_scan('{posts_path}')"
        ).fetchone()[0]
        print(f"   ✓ Posts table accessible (rows: {posts_count})")
    except Exception as e:
        print(f"   ✗ Posts table verification failed: {e}")

    try:
        restaurants_count = conn.execute(
            f"SELECT COUNT(*) FROM iceberg_scan('{restaurants_path}')"
        ).fetchone()[0]
        print(f"   ✓ Restaurants table accessible (rows: {restaurants_count})")
    except Exception as e:
        print(f"   ✗ Restaurants table verification failed: {e}")

    print("\n" + "=" * 50)
    print("Iceberg tables created successfully!")
    print("=" * 50)
    print(f"\nTable locations:")
    print(f"  - Posts:       {posts_path}")
    print(f"  - Restaurants: {restaurants_path}")
    print(f"\nNext steps:")
    print(f"  1. Deploy Lambda function with S3 triggers")
    print(f"  2. Upload CSV to scraper backup bucket to test ingestion")
    print(f"  3. Query tables using DuckDB or Athena")


def query_sample():
    """Query sample data from tables (utility function)."""
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    conn.execute(f"SET s3_region='{AWS_REGION}';")

    print("\n--- Posts Sample ---")
    try:
        result = conn.execute(f"""
            SELECT post_id, creator, likes, status
            FROM iceberg_scan('s3://{ANALYTICS_BUCKET}/iceberg/posts/')
            LIMIT 5
        """).fetchall()
        for row in result:
            print(row)
    except Exception as e:
        print(f"No data or error: {e}")

    print("\n--- Restaurants Sample ---")
    try:
        result = conn.execute(f"""
            SELECT restaurant_name, city, instagram_handle, status
            FROM iceberg_scan('s3://{ANALYTICS_BUCKET}/iceberg/restaurants/')
            LIMIT 5
        """).fetchall()
        for row in result:
            print(row)
    except Exception as e:
        print(f"No data or error: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--query":
        query_sample()
    else:
        create_tables()
