"""
Instagram Analytics Query Lambda - Declarative NL-to-SQL Executor

This Lambda receives SQL queries from Bedrock Agent and executes them
against Parquet data in S3 using DuckDB. The agent generates SQL from
natural language; this Lambda only validates and executes.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Any

import duckdb

from iceberg_logger import log_query_async

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# =============================================================================
# DECLARATIVE CONFIGURATION
# =============================================================================

# Environment
ANALYTICS_BUCKET = os.environ.get('ANALYTICS_BUCKET', 'instagram-analytics-lake')
S3_REGION = os.environ.get('S3_REGION', 'us-east-1')

# Declarative Schema Definition
TABLES = {
    "posts": {
        "path": f"s3://{ANALYTICS_BUCKET}/data/posts/*/*.parquet",
        "columns": [
            ("search_term", "VARCHAR", "Search term used to find this post"),
            ("post_id", "VARCHAR", "Unique post identifier"),
            ("creator", "VARCHAR", "Instagram username of post creator"),
            ("posted_date", "VARCHAR", "Date the post was published"),
            ("likes", "INTEGER", "Number of likes on the post"),
            ("comments", "INTEGER", "Number of comments on the post"),
            ("hashtags", "VARCHAR", "Space-separated hashtags"),
            ("caption", "VARCHAR", "Post caption text"),
            ("image_description", "VARCHAR", "AI-generated image description"),
            ("post_url", "VARCHAR", "URL to the Instagram post"),
            ("status", "VARCHAR", "Scrape status"),
            ("ingested_at", "TIMESTAMP", "When data was ingested"),
        ]
    },
    "restaurants": {
        "path": f"s3://{ANALYTICS_BUCKET}/data/restaurants/*/*.parquet",
        "columns": [
            ("restaurant_name", "VARCHAR", "Name of the restaurant"),
            ("city", "VARCHAR", "City where restaurant is located"),
            ("zip_code", "VARCHAR", "ZIP code"),
            ("phone", "VARCHAR", "Phone number"),
            ("instagram_handle", "VARCHAR", "Instagram username"),
            ("followers", "INTEGER", "Number of Instagram followers"),
            ("posts_count", "INTEGER", "Number of Instagram posts"),
            ("bio", "VARCHAR", "Instagram bio text"),
            ("website", "VARCHAR", "Website URL"),
            ("status", "VARCHAR", "Scrape status: FOUND, NOT_FOUND, EMPTY_PROFILE"),
            ("ingested_at", "TIMESTAMP", "When data was ingested"),
        ]
    },
    "query_logs": {
        "path": f"s3://{ANALYTICS_BUCKET}/data/query_logs/*/*.parquet",
        "columns": [
            ("query_id", "VARCHAR", "Unique query identifier"),
            ("timestamp", "TIMESTAMP", "When query was executed (UTC)"),
            ("user_query", "VARCHAR", "Original natural language query from user"),
            ("sql_query", "VARCHAR", "The SQL query that was executed"),
            ("success", "BOOLEAN", "Whether query succeeded"),
            ("error_message", "VARCHAR", "Error details if query failed"),
            ("execution_time_ms", "INTEGER", "Query execution time in milliseconds"),
            ("row_count", "INTEGER", "Number of rows returned"),
            ("columns_returned", "VARCHAR", "JSON array of column names"),
        ]
    }
}

# Declarative SQL Validation Rules
BLOCKED_PATTERNS = [
    (r"\bDROP\b", "DROP operations not allowed"),
    (r"\bDELETE\b", "DELETE operations not allowed"),
    (r"\bINSERT\b", "INSERT operations not allowed"),
    (r"\bUPDATE\b", "UPDATE operations not allowed"),
    (r"\bCREATE\s+TABLE\b", "CREATE TABLE not allowed"),
    (r"\bALTER\b", "ALTER operations not allowed"),
    (r"\bTRUNCATE\b", "TRUNCATE operations not allowed"),
    (r"\bEXEC\b", "EXEC operations not allowed"),
    (r"\bCALL\b", "CALL operations not allowed"),
    (r";\s*\w", "Multiple statements not allowed"),
]

REQUIRED_PATTERNS = [
    (r"^\s*SELECT\b", "Query must start with SELECT"),
]

# Query limits
MAX_ROWS = 1000
DEFAULT_LIMIT = 100

# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class QueryResult:
    """Result of a SQL query execution."""
    success: bool
    data: list | None
    error: str | None
    sql: str
    row_count: int = 0
    columns: list | None = None


@dataclass
class ValidationResult:
    """Result of SQL validation."""
    valid: bool
    error: str | None = None
    sanitized_sql: str | None = None


# =============================================================================
# SQL VALIDATION (Declarative Rules)
# =============================================================================

def validate_sql(sql: str) -> ValidationResult:
    """
    Validate SQL against declarative rules.
    Returns ValidationResult with sanitized SQL if valid.
    """
    if not sql or not sql.strip():
        return ValidationResult(valid=False, error="Empty SQL query")

    sql_normalized = sql.strip()
    sql_upper = sql_normalized.upper()

    # Check blocked patterns
    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, sql_upper, re.IGNORECASE):
            logger.warning(f"Blocked SQL pattern detected: {pattern}")
            return ValidationResult(valid=False, error=message)

    # Check required patterns
    for pattern, message in REQUIRED_PATTERNS:
        if not re.search(pattern, sql_upper, re.IGNORECASE):
            return ValidationResult(valid=False, error=message)

    # Ensure LIMIT exists (add if missing)
    if not re.search(r"\bLIMIT\s+\d+", sql_upper):
        sql_normalized = f"{sql_normalized.rstrip(';')} LIMIT {DEFAULT_LIMIT}"
        logger.info(f"Added default LIMIT {DEFAULT_LIMIT}")

    # Cap LIMIT if too high
    limit_match = re.search(r"\bLIMIT\s+(\d+)", sql_normalized, re.IGNORECASE)
    if limit_match:
        limit_val = int(limit_match.group(1))
        if limit_val > MAX_ROWS:
            sql_normalized = re.sub(
                r"\bLIMIT\s+\d+",
                f"LIMIT {MAX_ROWS}",
                sql_normalized,
                flags=re.IGNORECASE
            )
            logger.info(f"Capped LIMIT from {limit_val} to {MAX_ROWS}")

    return ValidationResult(valid=True, sanitized_sql=sql_normalized)


# =============================================================================
# DUCKDB INITIALIZATION
# =============================================================================

def init_duckdb() -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB with required extensions."""
    conn = duckdb.connect()
    conn.execute("SET home_directory='/tmp';")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute(f"SET s3_region='{S3_REGION}';")
    logger.info("DuckDB initialized with httpfs extension")
    return conn


def build_table_views(conn: duckdb.DuckDBPyConnection) -> None:
    """
    Declaratively create views for each table from TABLES config.
    This allows queries to use simple table names instead of read_parquet().
    """
    for table_name, config in TABLES.items():
        try:
            view_sql = f"""
                CREATE OR REPLACE VIEW {table_name} AS
                SELECT * FROM read_parquet('{config['path']}')
            """
            conn.execute(view_sql)
            logger.info(f"Created view: {table_name}")
        except Exception as e:
            # Handle case where table has no data yet (e.g., query_logs)
            logger.warning(f"Could not create view for {table_name}: {e}")


# =============================================================================
# QUERY EXECUTION
# =============================================================================

def execute_query(sql: str, user_query: str | None = None) -> QueryResult:
    """
    Validate and execute SQL query.
    Returns QueryResult with data or error.
    """
    start_time = time.time()

    # Validate
    validation = validate_sql(sql)
    if not validation.valid:
        logger.error(f"SQL validation failed: {validation.error}")
        execution_time_ms = int((time.time() - start_time) * 1000)

        # Log failed validation
        log_query_async(
            sql_query=sql,
            success=False,
            error_message=validation.error,
            execution_time_ms=execution_time_ms,
            row_count=0,
            columns_returned=[],
            user_query=user_query
        )

        return QueryResult(
            success=False,
            data=None,
            error=validation.error,
            sql=sql
        )

    safe_sql = validation.sanitized_sql
    logger.info(f"Executing SQL: {safe_sql}")

    try:
        # Initialize DuckDB and create views
        conn = init_duckdb()
        build_table_views(conn)

        # Execute query
        result = conn.execute(safe_sql)
        rows = result.fetchall()
        columns = [desc[0] for desc in result.description]

        # Convert to list of dicts
        data = [dict(zip(columns, row)) for row in rows]

        # Handle special types (dates, etc.)
        for row in data:
            for key, value in row.items():
                if hasattr(value, 'isoformat'):
                    row[key] = value.isoformat()

        logger.info(f"Query returned {len(data)} rows")

        execution_time_ms = int((time.time() - start_time) * 1000)

        # Log successful query
        log_query_async(
            sql_query=safe_sql,
            success=True,
            error_message=None,
            execution_time_ms=execution_time_ms,
            row_count=len(data),
            columns_returned=columns,
            user_query=user_query
        )

        return QueryResult(
            success=True,
            data=data,
            error=None,
            sql=safe_sql,
            row_count=len(data),
            columns=columns
        )

    except Exception as e:
        logger.error(f"Query execution failed: {str(e)}")
        execution_time_ms = int((time.time() - start_time) * 1000)

        # Log failed query
        log_query_async(
            sql_query=safe_sql,
            success=False,
            error_message=str(e),
            execution_time_ms=execution_time_ms,
            row_count=0,
            columns_returned=[],
            user_query=user_query
        )

        return QueryResult(
            success=False,
            data=None,
            error=f"Query execution error: {str(e)}",
            sql=safe_sql
        )


# =============================================================================
# RESULT FORMATTING
# =============================================================================

def format_number(value: any) -> str:
    """Format numbers with K/M suffixes for readability."""
    if value is None:
        return ""
    if not isinstance(value, (int, float)):
        return str(value)

    if abs(value) >= 1_000_000:
        return f"{value/1_000_000:.1f}M"
    elif abs(value) >= 1_000:
        return f"{value/1_000:.1f}K"
    else:
        return f"{value:,}" if isinstance(value, int) else f"{value:.2f}"


def format_as_markdown_table(columns: list, data: list) -> str:
    """
    Format query results as a markdown table.
    Returns a properly formatted markdown table string.
    """
    if not data or not columns:
        return "No data found."

    # Create human-readable headers
    header_map = {
        'restaurant_name': 'Restaurant',
        'instagram_handle': 'Handle',
        'followers': 'Followers',
        'posts_count': 'Posts',
        'search_term': 'Hashtag',
        'post_id': 'Post ID',
        'creator': 'Creator',
        'posted_date': 'Date',
        'likes': 'Likes',
        'comments': 'Comments',
        'hashtags': 'Hashtags',
        'caption': 'Caption',
        'city': 'City',
        'zip_code': 'ZIP',
        'phone': 'Phone',
        'bio': 'Bio',
        'website': 'Website',
        'status': 'Status',
        'avg_likes': 'Avg Likes',
        'avg_comments': 'Avg Comments',
        'total_likes': 'Total Likes',
        'count': 'Count',
    }

    # Format headers
    headers = [header_map.get(col.lower(), col.replace('_', ' ').title()) for col in columns]

    # Numeric columns that should be formatted
    numeric_cols = {'followers', 'posts_count', 'likes', 'comments', 'avg_likes',
                    'avg_comments', 'total_likes', 'count'}

    # Build table
    lines = []

    # Header row
    lines.append("| " + " | ".join(headers) + " |")

    # Separator row
    lines.append("|" + "|".join(["---" for _ in headers]) + "|")

    # Data rows
    for row in data:
        cells = []
        for col in columns:
            value = row.get(col, "")
            # Format numeric columns
            if col.lower() in numeric_cols:
                cells.append(format_number(value))
            elif value is None:
                cells.append("")
            else:
                # Truncate long text
                str_val = str(value)
                if len(str_val) > 50:
                    str_val = str_val[:47] + "..."
                # Escape pipe characters
                str_val = str_val.replace("|", "\\|")
                cells.append(str_val)
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


# =============================================================================
# EVIDENCE METADATA (Anti-Hallucination Support)
# =============================================================================

def calculate_evidence_level(row_count: int) -> str:
    """
    Determine evidence level based on retrieved data.

    Returns:
        "none" - No data retrieved (0 rows)
        "partial" - Limited evidence (1-5 rows)
        "sufficient" - Strong evidence (>5 rows)
    """
    if row_count == 0:
        return "none"
    elif row_count <= 5:
        return "partial"
    else:
        return "sufficient"


def extract_post_ids(data: list, columns: list) -> list:
    """
    Extract post IDs from query results for evidence tracking.

    Args:
        data: List of row dictionaries
        columns: List of column names

    Returns:
        List of post IDs (max 20 for metadata)
    """
    if not data or not columns:
        return []

    # Check if post_id column exists
    columns_lower = [c.lower() for c in columns]
    if 'post_id' not in columns_lower:
        return []

    post_ids = [row.get('post_id') for row in data if row.get('post_id')]
    return post_ids[:20]  # Limit to first 20 for metadata


def extract_restaurant_handles(data: list, columns: list) -> list:
    """
    Extract restaurant Instagram handles from query results for evidence tracking.

    Args:
        data: List of row dictionaries
        columns: List of column names

    Returns:
        List of instagram_handle values (max 20 for metadata)
    """
    if not data or not columns:
        return []

    # Check if instagram_handle column exists
    columns_lower = [c.lower() for c in columns]
    if 'instagram_handle' not in columns_lower:
        return []

    handles = [row.get('instagram_handle') for row in data if row.get('instagram_handle')]
    return handles[:20]  # Limit to first 20 for metadata


def determine_query_scope(sql: str) -> str:
    """
    Determine the scope of the query based on tables referenced.

    Args:
        sql: The SQL query string

    Returns:
        "posts", "restaurants", "posts_and_restaurants", or "unknown"
    """
    if not sql:
        return "unknown"

    sql_upper = sql.upper()

    has_posts = 'POSTS' in sql_upper and 'POSTS_COUNT' not in sql_upper
    has_restaurants = 'RESTAURANTS' in sql_upper

    if has_posts and has_restaurants:
        return "posts_and_restaurants"
    elif has_posts:
        return "posts"
    elif has_restaurants:
        return "restaurants"
    else:
        return "unknown"


def build_evidence_metadata(result: 'QueryResult') -> dict:
    """
    Build evidence metadata for anti-hallucination support.

    This metadata helps the agent determine:
    - Whether factual claims can be made
    - What level of confidence is appropriate
    - Which posts/restaurants can be cited as evidence

    Args:
        result: QueryResult from query execution

    Returns:
        Evidence metadata dictionary with entity IDs for validation
    """
    if not result.success:
        return {
            'posts_retrieved': False,
            'post_ids': [],
            'restaurants_retrieved': False,
            'restaurant_handles': [],
            'evidence_level': 'none',
            'query_scope': 'unknown'
        }

    query_scope = determine_query_scope(result.sql)
    evidence_level = calculate_evidence_level(result.row_count)
    post_ids = extract_post_ids(result.data or [], result.columns or [])
    restaurant_handles = extract_restaurant_handles(result.data or [], result.columns or [])

    # posts_retrieved is True only if we queried posts and got results
    posts_retrieved = (
        result.row_count > 0 and
        query_scope in ['posts', 'posts_and_restaurants'] and
        len(post_ids) > 0
    )

    # restaurants_retrieved is True only if we queried restaurants and got results
    restaurants_retrieved = (
        result.row_count > 0 and
        query_scope in ['restaurants', 'posts_and_restaurants'] and
        len(restaurant_handles) > 0
    )

    return {
        'posts_retrieved': posts_retrieved,
        'post_ids': post_ids,
        'restaurants_retrieved': restaurants_retrieved,
        'restaurant_handles': restaurant_handles,
        'evidence_level': evidence_level,
        'query_scope': query_scope
    }


def build_allowed_values(result: 'QueryResult') -> dict:
    """
    Build allowed_values for simplified anti-hallucination validation.

    This provides a simple list of valid values that the validator can
    use for lookup-based verification without re-executing queries.

    Args:
        result: QueryResult from query execution

    Returns:
        Dictionary with 'handles' and 'post_ids' lists
    """
    if not result.success or not result.data:
        return {
            'handles': [],
            'post_ids': [],
            'restaurant_names': [],
            'creators': []
        }

    handles = []
    post_ids = []
    restaurant_names = []
    creators = []

    for row in result.data:
        # Extract Instagram handles
        if 'instagram_handle' in row and row['instagram_handle']:
            handle = row['instagram_handle']
            # Normalize: ensure @ prefix
            if not handle.startswith('@'):
                handle = f"@{handle}"
            handles.append(handle.lower())

        # Extract post IDs
        if 'post_id' in row and row['post_id']:
            post_ids.append(str(row['post_id']))

        # Extract restaurant names
        if 'restaurant_name' in row and row['restaurant_name']:
            restaurant_names.append(row['restaurant_name'])

        # Extract creators (from posts table)
        if 'creator' in row and row['creator']:
            creator = row['creator']
            if not creator.startswith('@'):
                creator = f"@{creator}"
            creators.append(creator.lower())

    # Deduplicate while preserving order
    return {
        'handles': list(dict.fromkeys(handles)),
        'post_ids': list(dict.fromkeys(post_ids)),
        'restaurant_names': list(dict.fromkeys(restaurant_names)),
        'creators': list(dict.fromkeys(creators))
    }


# =============================================================================
# SCHEMA INTROSPECTION (for agent)
# =============================================================================

def get_schema_description() -> str:
    """
    Generate human-readable schema description.
    This can be returned to help the agent understand available data.
    """
    lines = ["Available tables and columns:\n"]

    for table_name, config in TABLES.items():
        lines.append(f"\n## {table_name}")
        for col_name, col_type, col_desc in config["columns"]:
            lines.append(f"  - {col_name} ({col_type}): {col_desc}")

    return "\n".join(lines)


# =============================================================================
# BEDROCK AGENT HANDLER
# =============================================================================

def extract_parameters(event: dict) -> dict:
    """Extract parameters from Bedrock Agent event format."""
    params = {}

    # Handle both list format and direct format
    parameters = event.get('parameters', [])
    if isinstance(parameters, list):
        for param in parameters:
            if isinstance(param, dict):
                params[param.get('name', '')] = param.get('value', '')
    elif isinstance(parameters, dict):
        params = parameters

    # Also check requestBody for POST requests
    request_body = event.get('requestBody', {})
    if request_body:
        content = request_body.get('content', {})
        app_json = content.get('application/json', {})
        properties = app_json.get('properties', {})
        for key, value in properties.items():
            if isinstance(value, dict):
                params[key] = value.get('value', '')
            else:
                params[key] = value

    return params


def extract_user_query(event: dict) -> str | None:
    """Extract the original user query from Bedrock Agent event."""
    # Bedrock Agent includes inputText in the event
    user_query = event.get('inputText')
    if user_query:
        return user_query

    # Also check sessionAttributes for user context
    session_attrs = event.get('sessionAttributes', {})
    if 'userQuery' in session_attrs:
        return session_attrs['userQuery']

    # Check agent context
    agent = event.get('agent', {})
    if 'inputText' in agent:
        return agent['inputText']

    return None


def format_bedrock_response(event: dict, result: QueryResult) -> dict:
    """Format response for Bedrock Agent with evidence metadata and allowed_values."""
    action_group = event.get('actionGroup', '')
    function_name = event.get('function', 'execute_sql')
    api_path = event.get('apiPath', '/execute_sql')
    http_method = event.get('httpMethod', 'GET')

    # Build evidence metadata for anti-hallucination support
    evidence_metadata = build_evidence_metadata(result)

    # Build allowed_values for simplified validation (handles and post_ids only)
    allowed_values = build_allowed_values(result)

    if result.success:
        # Generate pre-formatted markdown table
        markdown_table = format_as_markdown_table(result.columns, result.data)

        response_body = {
            'success': True,
            'row_count': result.row_count,
            'columns': result.columns,
            'markdown_table': markdown_table,
            'data': result.data,
            'sql_executed': result.sql,
            'evidence_metadata': evidence_metadata,
            'allowed_values': allowed_values
        }
    else:
        response_body = {
            'success': False,
            'error': result.error,
            'sql_attempted': result.sql,
            'evidence_metadata': evidence_metadata,
            'allowed_values': allowed_values
        }

    return {
        'messageVersion': '1.0',
        'response': {
            'actionGroup': action_group,
            'apiPath': api_path,
            'httpMethod': http_method,
            'function': function_name,
            'functionResponse': {
                'responseBody': {
                    'TEXT': {
                        'body': json.dumps(response_body, default=str)
                    }
                }
            }
        }
    }


def handler(event: dict, context: Any) -> dict:
    """
    Lambda handler for Bedrock Agent action group.

    Expects event with:
    - function: 'execute_sql' or 'get_schema'
    - parameters: [{'name': 'sql', 'value': 'SELECT ...'}]
    """
    logger.info(f"Event received: {json.dumps(event)}")

    try:
        function_name = event.get('function', 'execute_sql')
        params = extract_parameters(event)
        user_query = extract_user_query(event)

        logger.info(f"Function: {function_name}, Params: {params}, UserQuery: {user_query}")

        if function_name == 'get_schema':
            # Return schema description to help agent
            schema = get_schema_description()
            result = QueryResult(
                success=True,
                data=[{'schema': schema}],
                error=None,
                sql='',
                row_count=1
            )
        elif function_name == 'execute_sql':
            # Execute SQL query
            sql = params.get('sql', '')
            result = execute_query(sql, user_query=user_query)
        else:
            result = QueryResult(
                success=False,
                data=None,
                error=f"Unknown function: {function_name}",
                sql=''
            )

        return format_bedrock_response(event, result)

    except Exception as e:
        logger.error(f"Handler error: {str(e)}")
        return {
            'messageVersion': '1.0',
            'response': {
                'actionGroup': event.get('actionGroup', ''),
                'apiPath': event.get('apiPath', '/execute_sql'),
                'httpMethod': event.get('httpMethod', 'GET'),
                'function': event.get('function', ''),
                'functionResponse': {
                    'responseBody': {
                        'TEXT': {
                            'body': json.dumps({
                                'success': False,
                                'error': f"Lambda error: {str(e)}"
                            })
                        }
                    }
                }
            }
        }
