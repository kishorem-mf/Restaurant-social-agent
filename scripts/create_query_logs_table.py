#!/usr/bin/env python3
"""
Create Query Logs Iceberg Table Script

Initializes the Iceberg table for storing query logs using DuckDB.

Prerequisites:
- AWS credentials configured (aws configure)
- Analytics lake bucket created (instagram-analytics-lake)
- pip install duckdb

Usage:
    python scripts/create_query_logs_table.py
    python scripts/create_query_logs_table.py --query  # View sample data
    python scripts/create_query_logs_table.py --test   # Insert test record
"""

import sys
from datetime import datetime, timezone
import uuid
import json

import duckdb


# Configuration
AWS_REGION = "us-east-1"
ANALYTICS_BUCKET = "instagram-analytics-lake"
ICEBERG_PATH = f"s3://{ANALYTICS_BUCKET}/iceberg/query_logs"


def get_connection() -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB with required extensions."""
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("INSTALL iceberg; LOAD iceberg;")
    conn.execute(f"SET s3_region='{AWS_REGION}';")
    return conn


def create_table():
    """Create the query_logs Iceberg table."""
    print(f"Connecting to DuckDB...")
    conn = get_connection()

    print(f"Creating Iceberg table at: {ICEBERG_PATH}")

    try:
        # Check if table already exists
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM iceberg_scan('{ICEBERG_PATH}')").fetchone()[0]
            print(f"\n⚠️  Table already exists with {count} records!")
            return
        except Exception:
            pass  # Table doesn't exist, create it

        # Create the table
        conn.execute(f"""
            CREATE TABLE '{ICEBERG_PATH}' (
                query_id VARCHAR,
                timestamp TIMESTAMPTZ,
                sql_query VARCHAR,
                success BOOLEAN,
                error_message VARCHAR,
                execution_time_ms INTEGER,
                row_count INTEGER,
                columns_returned VARCHAR
            )
        """)

        print(f"\n✓ Table created successfully!")
        print(f"  Location: {ICEBERG_PATH}")

    except Exception as e:
        print(f"\n✗ Failed to create table: {e}")
        sys.exit(1)


def insert_test_record():
    """Insert a test record to verify the table works."""
    print("\nInserting test record...")
    conn = get_connection()

    try:
        query_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        conn.execute(f"""
            INSERT INTO '{ICEBERG_PATH}'
            VALUES (
                '{query_id}',
                '{timestamp}'::TIMESTAMPTZ,
                'SELECT 1 as test',
                true,
                NULL,
                5,
                1,
                '["test"]'
            )
        """)

        print("✓ Test record inserted successfully!")

    except Exception as e:
        print(f"✗ Failed to insert test record: {e}")


def query_sample():
    """Query sample data from the table."""
    print(f"\nQuerying {ICEBERG_PATH}...")
    conn = get_connection()

    try:
        result = conn.execute(f"""
            SELECT query_id, timestamp, success, execution_time_ms, row_count
            FROM iceberg_scan('{ICEBERG_PATH}')
            ORDER BY timestamp DESC
            LIMIT 10
        """).fetchall()

        if not result:
            print("  No records found.")
        else:
            print(f"  Found {len(result)} records:")
            print(f"  {'Query ID':<40} {'Timestamp':<25} {'Success':<8} {'Time(ms)':<10} {'Rows':<6}")
            print(f"  {'-'*40} {'-'*25} {'-'*8} {'-'*10} {'-'*6}")
            for row in result:
                print(f"  {str(row[0])[:38]:<40} {str(row[1])[:23]:<25} {str(row[2]):<8} {row[3]:<10} {row[4]:<6}")

    except Exception as e:
        print(f"  Error querying table: {e}")


def show_stats():
    """Show table statistics."""
    print(f"\n--- Table Statistics ---")
    conn = get_connection()

    try:
        # Get counts
        total = conn.execute(f"SELECT COUNT(*) FROM iceberg_scan('{ICEBERG_PATH}')").fetchone()[0]
        success = conn.execute(f"SELECT COUNT(*) FROM iceberg_scan('{ICEBERG_PATH}') WHERE success = true").fetchone()[0]
        failed = conn.execute(f"SELECT COUNT(*) FROM iceberg_scan('{ICEBERG_PATH}') WHERE success = false").fetchone()[0]

        # Get avg execution time
        avg_time = conn.execute(f"SELECT AVG(execution_time_ms) FROM iceberg_scan('{ICEBERG_PATH}')").fetchone()[0]

        print(f"Location: {ICEBERG_PATH}")
        print(f"Total queries: {total}")
        print(f"Successful: {success}")
        print(f"Failed: {failed}")
        print(f"Avg execution time: {avg_time:.1f}ms" if avg_time else "Avg execution time: N/A")

    except Exception as e:
        print(f"Error getting stats: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--query":
            query_sample()
        elif sys.argv[1] == "--stats":
            show_stats()
        elif sys.argv[1] == "--test":
            insert_test_record()
            query_sample()
        else:
            print("Usage:")
            print("  python create_query_logs_table.py          # Create table")
            print("  python create_query_logs_table.py --query  # Query sample data")
            print("  python create_query_logs_table.py --stats  # Show table stats")
            print("  python create_query_logs_table.py --test   # Insert test record")
    else:
        create_table()
        print("\n" + "=" * 50)
        print("Query logs table ready!")
        print("=" * 50)
        print(f"\nNext steps:")
        print(f"  1. Deploy Lambda with updated code:")
        print(f"     cd lambdas/query_data && zip -r ../query_data.zip .")
        print(f"     aws lambda update-function-code --function-name instagram-query-data --zip-file fileb://lambdas/query_data.zip")
        print(f"  2. Test queries and verify logging:")
        print(f"     python scripts/create_query_logs_table.py --query")
