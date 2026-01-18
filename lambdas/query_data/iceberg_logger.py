"""
Query Logger

Logs all SQL query executions to Parquet files in S3 for analytics,
auditing, and debugging. Uses DuckDB for efficient writes.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

import duckdb

logger = logging.getLogger()

# Configuration
S3_REGION = os.environ.get('S3_REGION', 'us-east-1')
ANALYTICS_BUCKET = os.environ.get('ANALYTICS_BUCKET', 'instagram-analytics-lake')
QUERY_LOGS_PATH = f"s3://{ANALYTICS_BUCKET}/data/query_logs"
ENABLE_LOGGING = os.environ.get('ENABLE_QUERY_LOGGING', 'true').lower() == 'true'

# Lazy-loaded connection
_conn = None


def _get_connection() -> duckdb.DuckDBPyConnection:
    """
    Get or create DuckDB connection.
    Caches connection for reuse within Lambda execution context.
    """
    global _conn

    if _conn is not None:
        return _conn

    _conn = duckdb.connect()
    _conn.execute("SET home_directory='/tmp';")
    _conn.execute("INSTALL httpfs; LOAD httpfs;")
    _conn.execute(f"SET s3_region='{S3_REGION}';")

    logger.info("DuckDB initialized for query logging")
    return _conn


def log_query(
    sql_query: str,
    success: bool,
    error_message: Optional[str],
    execution_time_ms: int,
    row_count: int,
    columns_returned: list[str],
    user_query: Optional[str] = None
) -> bool:
    """
    Log a query execution to Parquet file in S3.

    Args:
        sql_query: The SQL query that was executed
        success: Whether the query succeeded
        error_message: Error details if the query failed
        execution_time_ms: Query execution time in milliseconds
        row_count: Number of rows returned
        columns_returned: List of column names in the result
        user_query: The original natural language query from the user

    Returns:
        True if logging succeeded, False otherwise
    """
    if not ENABLE_LOGGING:
        logger.debug("Query logging disabled")
        return False

    try:
        conn = _get_connection()

        # Prepare values
        query_id = str(uuid.uuid4())
        now = datetime.utcnow()
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        date_partition = now.strftime('%Y-%m-%d')
        columns_json = json.dumps(columns_returned)

        # Escape single quotes in SQL query for safe insertion
        safe_sql_query = sql_query.replace("'", "''")
        safe_error = error_message.replace("'", "''") if error_message else None
        safe_user_query = user_query.replace("'", "''") if user_query else None

        # Output path with date partitioning
        output_path = f"{QUERY_LOGS_PATH}/date={date_partition}/{query_id}.parquet"

        # Create a single-row table and write to Parquet
        insert_sql = f"""
            COPY (
                SELECT
                    '{query_id}' as query_id,
                    '{timestamp}'::TIMESTAMP as timestamp,
                    {f"'{safe_user_query}'" if safe_user_query else 'NULL'} as user_query,
                    '{safe_sql_query}' as sql_query,
                    {str(success).lower()} as success,
                    {f"'{safe_error}'" if safe_error else 'NULL'} as error_message,
                    {execution_time_ms} as execution_time_ms,
                    {row_count} as row_count,
                    '{columns_json}' as columns_returned
            )
            TO '{output_path}' (FORMAT PARQUET)
        """

        conn.execute(insert_sql)

        logger.info(f"Query logged to {output_path}: success={success}, time={execution_time_ms}ms, rows={row_count}")
        return True

    except Exception as e:
        logger.warning(f"Failed to log query: {e}")
        return False


def log_query_async(
    sql_query: str,
    success: bool,
    error_message: Optional[str],
    execution_time_ms: int,
    row_count: int,
    columns_returned: list[str],
    user_query: Optional[str] = None
) -> None:
    """
    Fire-and-forget query logging.
    Catches all exceptions to ensure logging never blocks the response.
    """
    try:
        log_query(
            sql_query=sql_query,
            success=success,
            error_message=error_message,
            execution_time_ms=execution_time_ms,
            row_count=row_count,
            columns_returned=columns_returned,
            user_query=user_query
        )
    except Exception as e:
        # Never let logging failures propagate
        logger.warning(f"Async query logging failed: {e}")
