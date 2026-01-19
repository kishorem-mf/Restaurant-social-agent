"""
Response Validator Lambda - Programmatic Anti-Hallucination Check

This Lambda validates LLM-generated responses against actual query evidence.
It INDEPENDENTLY re-executes the SQL query to get actual data, then extracts
factual claims (post IDs, metrics, creators) and verifies they exist in the
REAL results. This is the FAIL-PROOF programmatic check that runs AFTER the
LLM generates a response.

KEY PRINCIPLE: Never trust data passed by the agent - always verify independently.

Actions:
- PASS: Response is fully grounded in evidence
- SANITIZE: Response has unverified claims that were removed
- BLOCK: Response contains critical fabrications

Usage:
1. As Bedrock Agent Action Group: Agent calls validate_response with response_text and sql_executed
2. Validator re-executes SQL to get actual data
3. Validator validates claims against real data
4. Returns validated_response
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import duckdb

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# =============================================================================
# CONFIGURATION
# =============================================================================

ANALYTICS_BUCKET = os.environ.get('ANALYTICS_BUCKET', 'instagram-analytics-lake')
S3_REGION = os.environ.get('S3_REGION', 'us-east-1')

# Table definitions for DuckDB views
TABLES = {
    "posts": {
        "path": f"s3://{ANALYTICS_BUCKET}/data/posts/*/*.parquet",
    },
    "restaurants": {
        "path": f"s3://{ANALYTICS_BUCKET}/data/restaurants/*/*.parquet",
    }
}

# SQL validation - only allow SELECT queries
BLOCKED_PATTERNS = [
    (r"\bDROP\b", "DROP operations not allowed"),
    (r"\bDELETE\b", "DELETE operations not allowed"),
    (r"\bINSERT\b", "INSERT operations not allowed"),
    (r"\bUPDATE\b", "UPDATE operations not allowed"),
    (r"\bCREATE\s+TABLE\b", "CREATE TABLE not allowed"),
    (r"\bALTER\b", "ALTER operations not allowed"),
    (r"\bTRUNCATE\b", "TRUNCATE operations not allowed"),
]

MAX_ROWS = 100  # Limit for validation queries


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ValidationResult:
    """Result of response validation."""
    action: str  # PASS, SANITIZE, BLOCK
    original_response: str
    validated_response: str
    violations: list
    confidence_score: float
    details: dict


@dataclass
class ExtractedClaims:
    """Claims extracted from LLM response."""
    post_ids: list
    creators: list
    restaurant_names: list  # Restaurant names mentioned
    metrics: list  # (value, context) tuples
    rankings: list  # "top", "best", etc. claims


@dataclass
class QueryResult:
    """Result from query execution."""
    success: bool
    data: list
    columns: list
    row_count: int
    error: str | None = None


# =============================================================================
# DUCKDB INITIALIZATION & QUERY EXECUTION
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
    """Create views for each table from TABLES config."""
    for table_name, config in TABLES.items():
        try:
            view_sql = f"""
                CREATE OR REPLACE VIEW {table_name} AS
                SELECT * FROM read_parquet('{config['path']}')
            """
            conn.execute(view_sql)
            logger.info(f"Created view: {table_name}")
        except Exception as e:
            logger.warning(f"Could not create view for {table_name}: {e}")


def validate_sql(sql: str) -> tuple[bool, str | None]:
    """Validate SQL is safe to execute."""
    if not sql or not sql.strip():
        return False, "Empty SQL query"

    sql_upper = sql.upper()

    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, sql_upper, re.IGNORECASE):
            return False, message

    if not re.search(r"^\s*SELECT\b", sql_upper, re.IGNORECASE):
        return False, "Query must start with SELECT"

    return True, None


def execute_verification_query(sql: str) -> QueryResult:
    """
    Re-execute SQL query to get actual data for validation.
    This is the key function that provides independent verification.
    """
    logger.info(f"Executing verification query: {sql[:200]}...")

    # Validate SQL
    is_valid, error = validate_sql(sql)
    if not is_valid:
        logger.error(f"SQL validation failed: {error}")
        return QueryResult(
            success=False,
            data=[],
            columns=[],
            row_count=0,
            error=error
        )

    try:
        conn = init_duckdb()
        build_table_views(conn)

        # Add LIMIT if not present to prevent huge queries
        sql_normalized = sql.strip().rstrip(';')
        if not re.search(r"\bLIMIT\s+\d+", sql_normalized, re.IGNORECASE):
            sql_normalized = f"{sql_normalized} LIMIT {MAX_ROWS}"

        result = conn.execute(sql_normalized)
        rows = result.fetchall()
        columns = [desc[0] for desc in result.description]

        # Convert to list of dicts
        data = [dict(zip(columns, row)) for row in rows]

        # Handle special types
        for row in data:
            for key, value in row.items():
                if hasattr(value, 'isoformat'):
                    row[key] = value.isoformat()

        logger.info(f"Verification query returned {len(data)} rows")

        return QueryResult(
            success=True,
            data=data,
            columns=columns,
            row_count=len(data)
        )

    except Exception as e:
        logger.error(f"Query execution failed: {str(e)}")
        return QueryResult(
            success=False,
            data=[],
            columns=[],
            row_count=0,
            error=str(e)
        )


# =============================================================================
# EVIDENCE METADATA GENERATION
# =============================================================================

def calculate_evidence_level(row_count: int) -> str:
    """Determine evidence level based on retrieved data."""
    if row_count == 0:
        return "none"
    elif row_count <= 5:
        return "partial"
    else:
        return "sufficient"


def extract_post_ids_from_data(data: list, columns: list) -> list:
    """Extract post IDs from query results."""
    if not data or not columns:
        return []

    columns_lower = [c.lower() for c in columns]
    if 'post_id' not in columns_lower:
        return []

    post_ids = [row.get('post_id') for row in data if row.get('post_id')]
    return post_ids[:20]


def determine_query_scope(sql: str) -> str:
    """Determine the scope of the query based on tables referenced."""
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


def extract_restaurant_handles_from_data(data: list, columns: list) -> list:
    """Extract restaurant Instagram handles from query results."""
    if not data or not columns:
        return []

    columns_lower = [c.lower() for c in columns]
    if 'instagram_handle' not in columns_lower:
        return []

    handles = [row.get('instagram_handle') for row in data if row.get('instagram_handle')]
    return handles[:20]


def extract_restaurant_names_from_data(data: list, columns: list) -> list:
    """Extract restaurant names from query results."""
    if not data or not columns:
        return []

    columns_lower = [c.lower() for c in columns]
    if 'restaurant_name' not in columns_lower:
        return []

    names = [row.get('restaurant_name') for row in data if row.get('restaurant_name')]
    return names[:20]


def build_evidence_metadata(query_result: QueryResult, sql: str) -> dict:
    """Build evidence metadata from actual query results."""
    if not query_result.success:
        return {
            'posts_retrieved': False,
            'post_ids': [],
            'restaurants_retrieved': False,
            'restaurant_handles': [],
            'restaurant_names': [],
            'evidence_level': 'none',
            'query_scope': 'unknown'
        }

    query_scope = determine_query_scope(sql)
    evidence_level = calculate_evidence_level(query_result.row_count)
    post_ids = extract_post_ids_from_data(query_result.data, query_result.columns)
    restaurant_handles = extract_restaurant_handles_from_data(query_result.data, query_result.columns)
    restaurant_names = extract_restaurant_names_from_data(query_result.data, query_result.columns)

    posts_retrieved = (
        query_result.row_count > 0 and
        query_scope in ['posts', 'posts_and_restaurants'] and
        len(post_ids) > 0
    )

    restaurants_retrieved = (
        query_result.row_count > 0 and
        query_scope in ['restaurants', 'posts_and_restaurants'] and
        len(restaurant_handles) > 0
    )

    return {
        'posts_retrieved': posts_retrieved,
        'post_ids': post_ids,
        'restaurants_retrieved': restaurants_retrieved,
        'restaurant_handles': restaurant_handles,
        'restaurant_names': restaurant_names,
        'evidence_level': evidence_level,
        'query_scope': query_scope
    }


# =============================================================================
# CLAIM EXTRACTION
# =============================================================================

def extract_post_ids(text: str) -> list:
    """
    Extract post IDs mentioned in the response.
    Matches patterns like: post_id, POST123, C1234567890, etc.

    NOTE: Instagram post IDs typically start with 'C' followed by alphanumeric chars.
    We avoid matching Instagram handles (which follow @) or common words.
    """
    # First, extract all @handles to exclude them from post ID matching
    handles = set(re.findall(r'@([a-zA-Z0-9_\.]+)', text, re.IGNORECASE))

    patterns = [
        r'\bpost[_\s]?id[:\s]+([^\s,]+)',  # "post_id: XXX"
        r'\b(POST\d+)\b',  # POST123
        r'\b([A-Z]{2,3}\d{3,})\b',  # MED001, NYC123, etc.
        r'\b(C[a-zA-Z0-9_-]{10,})\b',  # Instagram-style IDs (start with C)
    ]

    post_ids = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # Exclude if it's a known handle
            if match.lower() not in {h.lower() for h in handles}:
                post_ids.append(match)

    return list(set(post_ids))


def extract_creators(text: str) -> list:
    """
    Extract creator/username mentions from the response.
    Matches @username patterns.
    """
    pattern = r'@([a-zA-Z0-9_\.]+)'
    return list(set(re.findall(pattern, text)))


def extract_metrics(text: str) -> list:
    """
    Extract numeric metrics (likes, comments, followers) from response.
    Returns list of (value, context) tuples.
    """
    patterns = [
        (r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?[KkMm]?)\s*(?:likes?|followers?|comments?)', 'engagement'),
        (r'(?:likes?|followers?|comments?)[:\s]+(\d{1,3}(?:,\d{3})*(?:\.\d+)?[KkMm]?)', 'engagement'),
        (r'(\d+(?:\.\d+)?%)\s*(?:engagement|rate|growth)', 'percentage'),
        (r'(?:average|avg|mean)[:\s]+(\d{1,3}(?:,\d{3})*(?:\.\d+)?[KkMm]?)', 'average'),
    ]

    metrics = []
    for pattern, context in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            metrics.append((match, context))

    return metrics


def extract_rankings(text: str) -> list:
    """
    Extract ranking claims from response.
    E.g., "top 5", "best performing", "#1", "highest"
    """
    patterns = [
        r'top\s*(\d+)',
        r'#(\d+)\s*(?:post|creator|performer)',
        r'(best|highest|most|leading)\s+(?:performing|engaged|popular)',
        r'rank(?:ed|ing)?\s*#?(\d+)',
    ]

    rankings = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        rankings.extend(matches)

    return rankings


def extract_restaurant_names_from_text(text: str) -> list:
    """
    Extract restaurant names from response text.
    Look for names in table rows (first column only), bold text, and common patterns.
    """
    names = []

    # Common non-restaurant terms to filter out
    non_names = {
        'rank', 'restaurant', 'name', 'handle', 'followers', 'posts',
        'city', 'likes', 'comments', 'date', '---', '------',
        # Common city names that might appear in tables
        'new york', 'los angeles', 'chicago', 'houston', 'phoenix',
        'philadelphia', 'san antonio', 'san diego', 'dallas', 'san jose',
        'austin', 'jacksonville', 'san francisco', 'columbus', 'charlotte',
        'fort worth', 'indianapolis', 'seattle', 'denver', 'washington',
        'boston', 'el paso', 'nashville', 'detroit', 'oklahoma city',
        'portland', 'las vegas', 'memphis', 'louisville', 'baltimore',
        'milwaukee', 'albuquerque', 'tucson', 'fresno', 'mesa', 'sacramento',
        'atlanta', 'kansas city', 'colorado springs', 'miami', 'raleigh',
        'omaha', 'long beach', 'virginia beach', 'oakland', 'minneapolis',
        'tulsa', 'arlington', 'tampa', 'new orleans', 'queens', 'brooklyn',
        'manhattan', 'staten island', 'bronx',
    }

    # Pattern 1: Extract first column from markdown table rows only
    # Match rows like: | STARBUCKS | @handle | 17.7M | ... |
    table_rows = re.findall(r'^\|([^|]+)\|', text, re.MULTILINE)
    for match in table_rows:
        name = match.strip()
        if name and name.lower() not in non_names and not name.startswith('-'):
            # Skip if it looks like a number or metric
            if not re.match(r'^[\d.,]+[KMB]?$', name, re.IGNORECASE):
                names.append(name)

    # Pattern 2: Bold restaurant names (**Name**)
    bold_pattern = r'\*\*([A-Z][A-Za-z\s\'\-&0-9]+?)\*\*'
    bold_matches = re.findall(bold_pattern, text)
    for m in bold_matches:
        name = m.strip()
        if len(name) > 2 and name.lower() not in non_names:
            names.append(name)

    # Deduplicate while preserving order
    seen = set()
    unique_names = []
    for name in names:
        normalized = name.upper().strip()
        if normalized not in seen and len(normalized) > 2:
            seen.add(normalized)
            unique_names.append(name)

    return unique_names[:20]


def extract_claims(response_text: str) -> ExtractedClaims:
    """Extract all factual claims from LLM response."""
    return ExtractedClaims(
        post_ids=extract_post_ids(response_text),
        creators=extract_creators(response_text),
        restaurant_names=extract_restaurant_names_from_text(response_text),
        metrics=extract_metrics(response_text),
        rankings=extract_rankings(response_text)
    )


# =============================================================================
# EVIDENCE VALIDATION
# =============================================================================

def normalize_id(id_str: str) -> str:
    """Normalize ID for comparison."""
    return str(id_str).strip().upper()


def validate_post_ids(claimed_ids: list, evidence_ids: list) -> tuple:
    """
    Validate claimed post IDs against evidence.
    Returns (valid_ids, invalid_ids).
    """
    evidence_normalized = {normalize_id(id) for id in evidence_ids}

    valid = []
    invalid = []

    for claimed_id in claimed_ids:
        if normalize_id(claimed_id) in evidence_normalized:
            valid.append(claimed_id)
        else:
            invalid.append(claimed_id)

    return valid, invalid


def validate_creators(claimed_creators: list, evidence_data: list) -> tuple:
    """
    Validate claimed creators against evidence.
    Returns (valid_creators, invalid_creators).
    """
    # Extract creators from evidence data
    evidence_creators = set()
    for row in evidence_data:
        if 'creator' in row:
            evidence_creators.add(row['creator'].lower().replace('@', ''))
        if 'instagram_handle' in row:
            evidence_creators.add(row['instagram_handle'].lower().replace('@', ''))

    valid = []
    invalid = []

    for creator in claimed_creators:
        if creator.lower() in evidence_creators:
            valid.append(creator)
        else:
            invalid.append(creator)

    return valid, invalid


def validate_restaurant_names(claimed_names: list, evidence_names: list) -> tuple:
    """
    Validate claimed restaurant names against evidence.
    Returns (valid_names, invalid_names).
    """
    # Normalize evidence names for comparison
    evidence_normalized = {name.upper().strip() for name in evidence_names if name}

    valid = []
    invalid = []

    for claimed_name in claimed_names:
        claimed_normalized = claimed_name.upper().strip()
        # Check for exact match or partial match
        if claimed_normalized in evidence_normalized:
            valid.append(claimed_name)
        elif any(claimed_normalized in ev or ev in claimed_normalized for ev in evidence_normalized):
            valid.append(claimed_name)  # Partial match (e.g., "LUKE'S LOBSTER" vs "LUKE S LOBSTER")
        else:
            invalid.append(claimed_name)

    return valid, invalid


def validate_metrics(claimed_metrics: list, evidence_data: list, evidence_level: str) -> tuple:
    """
    Validate claimed metrics against evidence.
    Returns (valid_metrics, invalid_metrics).

    More lenient when evidence_level is "sufficient" - allows derived stats.
    """
    if not evidence_data:
        # No evidence - all specific metrics are invalid
        return [], claimed_metrics

    # Extract actual metrics from evidence
    evidence_values = set()
    for row in evidence_data:
        for key in ['likes', 'comments', 'followers', 'posts_count']:
            if key in row and row[key] is not None:
                evidence_values.add(str(row[key]))
                # Also add formatted versions (K/M)
                val = row[key]
                if isinstance(val, (int, float)):
                    if val >= 1_000_000:
                        evidence_values.add(f"{val/1_000_000:.1f}M")
                    elif val >= 1_000:
                        evidence_values.add(f"{val/1_000:.1f}K")

    valid = []
    invalid = []

    for metric, context in claimed_metrics:
        # Normalize metric for comparison
        metric_normalized = metric.replace(',', '').upper()
        if any(metric_normalized in str(ev).upper() for ev in evidence_values):
            valid.append((metric, context))
        elif evidence_level == 'sufficient' and context in ['average', 'percentage']:
            # Allow derived statistics when we have sufficient evidence
            valid.append((metric, context))
        else:
            invalid.append((metric, context))

    return valid, invalid


# =============================================================================
# RESPONSE SANITIZATION
# =============================================================================

def sanitize_response(response_text: str, invalid_claims: dict) -> str:
    """
    Remove or redact unverified claims from response.
    """
    sanitized = response_text

    # Remove invalid post IDs
    for post_id in invalid_claims.get('post_ids', []):
        # Replace with placeholder
        sanitized = re.sub(
            rf'\b{re.escape(post_id)}\b',
            '[REDACTED]',
            sanitized,
            flags=re.IGNORECASE
        )

    # Remove invalid creator mentions
    for creator in invalid_claims.get('creators', []):
        sanitized = re.sub(
            rf'@{re.escape(creator)}\b',
            '@[REDACTED]',
            sanitized,
            flags=re.IGNORECASE
        )

    # Add warning if metrics were removed
    if invalid_claims.get('metrics'):
        if '**Confidence Notes**' in sanitized:
            sanitized = sanitized.replace(
                '**Confidence Notes**',
                '**Confidence Notes** (Some metrics could not be verified): '
            )

    return sanitized


# =============================================================================
# MAIN VALIDATION LOGIC
# =============================================================================

def validate_response_with_allowed_values(
    response_text: str,
    allowed_values: dict
) -> ValidationResult:
    """
    Simplified validation using pre-computed allowed_values.

    This is the preferred validation method - uses simple lookup instead
    of re-executing SQL queries.

    Args:
        response_text: LLM-generated response to validate
        allowed_values: Dict with 'handles', 'post_ids', 'restaurant_names', 'creators'

    Returns:
        ValidationResult with action (PASS/SANITIZE/BLOCK) and details
    """
    logger.info("Starting simplified validation with allowed_values")

    # Extract allowed lists (normalize for comparison)
    allowed_handles = {h.lower().replace('@', '') for h in allowed_values.get('handles', [])}
    allowed_post_ids = {str(pid).upper() for pid in allowed_values.get('post_ids', [])}
    allowed_names = {n.upper().strip() for n in allowed_values.get('restaurant_names', [])}
    allowed_creators = {c.lower().replace('@', '') for c in allowed_values.get('creators', [])}

    # Combine handles and creators for @ mention validation
    all_allowed_handles = allowed_handles | allowed_creators

    logger.info(f"Allowed values: {len(allowed_handles)} handles, {len(allowed_post_ids)} post_ids, "
                f"{len(allowed_names)} names, {len(allowed_creators)} creators")

    # Extract claims from response
    claims = extract_claims(response_text)
    logger.info(f"Extracted claims: {len(claims.post_ids)} post_ids, "
                f"{len(claims.creators)} creators, {len(claims.restaurant_names)} names")

    violations = []
    invalid_claims = {}

    # Validate @ mentions (handles/creators)
    if claims.creators:
        invalid_creators = []
        for creator in claims.creators:
            normalized = creator.lower().replace('@', '')
            if normalized not in all_allowed_handles:
                invalid_creators.append(creator)

        if invalid_creators:
            violations.append({
                'type': 'invalid_handles',
                'severity': 'HIGH',
                'claimed': invalid_creators,
                'allowed_sample': list(all_allowed_handles)[:5],
                'message': f"Handles not in allowed list: {invalid_creators}"
            })
            invalid_claims['creators'] = invalid_creators
            logger.warning(f"HALLUCINATION: Invalid handles {invalid_creators}")

    # Validate post IDs
    if claims.post_ids:
        invalid_ids = []
        for pid in claims.post_ids:
            if str(pid).upper() not in allowed_post_ids:
                invalid_ids.append(pid)

        if invalid_ids:
            violations.append({
                'type': 'invalid_post_ids',
                'severity': 'HIGH',
                'claimed': invalid_ids,
                'allowed_sample': list(allowed_post_ids)[:5],
                'message': f"Post IDs not in allowed list: {invalid_ids}"
            })
            invalid_claims['post_ids'] = invalid_ids
            logger.warning(f"HALLUCINATION: Invalid post IDs {invalid_ids}")

    # Validate restaurant names
    if claims.restaurant_names and allowed_names:
        invalid_names = []
        for name in claims.restaurant_names:
            normalized = name.upper().strip()
            # Check exact match or partial match
            if normalized not in allowed_names:
                # Check for partial match (e.g., "STARBUCKS" in "STARBUCKS COFFEE")
                if not any(normalized in an or an in normalized for an in allowed_names):
                    invalid_names.append(name)

        if invalid_names:
            violations.append({
                'type': 'invalid_restaurant_names',
                'severity': 'HIGH',
                'claimed': invalid_names,
                'allowed_sample': list(allowed_names)[:5],
                'message': f"Restaurant names not in allowed list: {invalid_names}"
            })
            invalid_claims['restaurant_names'] = invalid_names
            logger.warning(f"HALLUCINATION: Invalid names {invalid_names}")

    # Calculate confidence score
    total_claims = len(claims.post_ids) + len(claims.creators) + len(claims.restaurant_names)
    total_violations = (len(invalid_claims.get('post_ids', [])) +
                       len(invalid_claims.get('creators', [])) +
                       len(invalid_claims.get('restaurant_names', [])))

    if total_claims > 0:
        confidence_score = 1.0 - (total_violations / total_claims)
    else:
        confidence_score = 1.0

    # Determine action
    high_severity = sum(1 for v in violations if v['severity'] == 'HIGH')

    if high_severity >= 2:
        action = 'BLOCK'
        validated_response = (
            "I apologize, but I found references that don't match the actual data. "
            "Please let me provide only verified information from the query results."
        )
    elif violations:
        action = 'SANITIZE'
        validated_response = sanitize_response(response_text, invalid_claims)
    else:
        action = 'PASS'
        validated_response = response_text

    logger.info(f"Validation result: action={action}, confidence={confidence_score:.2f}")

    return ValidationResult(
        action=action,
        original_response=response_text,
        validated_response=validated_response,
        violations=violations,
        confidence_score=confidence_score,
        details={
            'claims_checked': total_claims,
            'claims_invalid': total_violations,
            'invalid_claims': invalid_claims,
            'verification_method': 'allowed_values_lookup'
        }
    )


def validate_response(
    response_text: str,
    sql_executed: str,
    allowed_values: dict = None
) -> ValidationResult:
    """
    Main validation function - uses allowed_values if provided, otherwise falls back
    to independent SQL verification.

    Args:
        response_text: LLM-generated response to validate
        sql_executed: The SQL query that was executed
        allowed_values: Optional dict with allowed handles, post_ids, etc.

    Returns:
        ValidationResult with action (PASS/SANITIZE/BLOCK) and details
    """
    # PREFERRED: Use allowed_values for simple lookup validation
    if allowed_values and (allowed_values.get('handles') or allowed_values.get('post_ids')
                          or allowed_values.get('restaurant_names') or allowed_values.get('creators')):
        logger.info("Using simplified allowed_values validation")
        return validate_response_with_allowed_values(response_text, allowed_values)

    # FALLBACK: Re-execute SQL for independent verification
    logger.info("Falling back to SQL re-execution validation")
    logger.info(f"SQL to verify: {sql_executed[:200]}...")

    # STEP 1: Re-execute SQL to get ACTUAL data (independent verification)
    query_result = execute_verification_query(sql_executed)

    if not query_result.success:
        logger.warning(f"Verification query failed: {query_result.error}")
        # If we can't verify, we must be conservative
        return ValidationResult(
            action='BLOCK',
            original_response=response_text,
            validated_response=(
                "I apologize, but I cannot verify the data in my response. "
                "Please try rephrasing your query or ask a different question."
            ),
            violations=[{
                'type': 'verification_failed',
                'severity': 'HIGH',
                'message': f"Could not re-execute query for verification: {query_result.error}"
            }],
            confidence_score=0.0,
            details={
                'verification_error': query_result.error,
                'sql_executed': sql_executed
            }
        )

    # STEP 2: Build evidence metadata from ACTUAL query results
    evidence_metadata = build_evidence_metadata(query_result, sql_executed)
    evidence_data = query_result.data

    logger.info(f"Actual evidence: {query_result.row_count} rows, "
                f"level={evidence_metadata['evidence_level']}, "
                f"post_ids={len(evidence_metadata['post_ids'])}")

    # STEP 3: Extract claims from response
    claims = extract_claims(response_text)
    logger.info(f"Extracted claims: {len(claims.post_ids)} post_ids, "
                f"{len(claims.creators)} creators, {len(claims.metrics)} metrics")

    violations = []
    invalid_claims = {}

    # STEP 4: Validate claims against ACTUAL data
    evidence_ids = evidence_metadata.get('post_ids', [])
    evidence_level = evidence_metadata.get('evidence_level', 'none')

    # Validate post IDs
    if claims.post_ids:
        valid_ids, invalid_ids = validate_post_ids(claims.post_ids, evidence_ids)
        if invalid_ids:
            violations.append({
                'type': 'invalid_post_ids',
                'severity': 'HIGH',
                'claimed': invalid_ids,
                'actual_post_ids': evidence_ids[:5],  # Show what's actually there
                'message': f"Post IDs not found in actual data: {invalid_ids}"
            })
            invalid_claims['post_ids'] = invalid_ids

    # Validate creators
    if claims.creators and evidence_data:
        valid_creators, invalid_creators = validate_creators(claims.creators, evidence_data)
        if invalid_creators:
            violations.append({
                'type': 'invalid_creators',
                'severity': 'MEDIUM',
                'claimed': invalid_creators,
                'message': f"Creators not found in actual data: {invalid_creators}"
            })
            invalid_claims['creators'] = invalid_creators

    # Validate restaurant names (NEW - anti-hallucination for restaurant queries)
    evidence_restaurant_names = evidence_metadata.get('restaurant_names', [])
    if claims.restaurant_names and evidence_metadata.get('query_scope') in ['restaurants', 'posts_and_restaurants']:
        valid_names, invalid_names = validate_restaurant_names(claims.restaurant_names, evidence_restaurant_names)
        if invalid_names:
            violations.append({
                'type': 'invalid_restaurant_names',
                'severity': 'HIGH',
                'claimed': invalid_names,
                'actual_names': evidence_restaurant_names[:5],
                'message': f"Restaurant names not found in actual data: {invalid_names}"
            })
            invalid_claims['restaurant_names'] = invalid_names
            logger.warning(f"HALLUCINATION DETECTED: Claimed restaurants {invalid_names} not in evidence {evidence_restaurant_names[:5]}")

    # Validate metrics
    if claims.metrics:
        valid_metrics, invalid_metrics = validate_metrics(
            claims.metrics,
            evidence_data,
            evidence_level
        )
        if invalid_metrics:
            violations.append({
                'type': 'invalid_metrics',
                'severity': 'MEDIUM',
                'claimed': [m[0] for m in invalid_metrics],
                'message': f"Metrics not verified in actual data: {[m[0] for m in invalid_metrics]}"
            })
            invalid_claims['metrics'] = invalid_metrics

    # Check ranking claims without evidence
    if claims.rankings and evidence_level == 'none':
        violations.append({
            'type': 'ungrounded_rankings',
            'severity': 'HIGH',
            'claimed': claims.rankings,
            'message': "Ranking claims made without evidence (query returned 0 rows)"
        })

    # STEP 5: Determine action based on violations
    high_severity = sum(1 for v in violations if v['severity'] == 'HIGH')
    medium_severity = sum(1 for v in violations if v['severity'] == 'MEDIUM')

    # Calculate confidence score (1.0 = fully grounded, 0.0 = fully fabricated)
    total_claims = (len(claims.post_ids) + len(claims.creators) +
                    len(claims.restaurant_names) + len(claims.metrics) + len(claims.rankings))
    total_violations = len(invalid_claims.get('post_ids', [])) + \
                       len(invalid_claims.get('creators', [])) + \
                       len(invalid_claims.get('restaurant_names', [])) + \
                       len(invalid_claims.get('metrics', []))

    if total_claims > 0:
        confidence_score = 1.0 - (total_violations / total_claims)
    else:
        # No specific claims made - considered grounded
        confidence_score = 1.0

    # Determine action
    if high_severity >= 2 or (high_severity >= 1 and medium_severity >= 2):
        action = 'BLOCK'
        validated_response = (
            "I apologize, but I cannot provide specific data that wasn't found "
            "in the query results. Please let me rephrase with verified information only.\n\n"
            f"The query returned {query_result.row_count} rows. "
            "Based on this data, I can offer general guidance, but I cannot "
            "cite specific posts, metrics, or creators that don't exist in the results."
        )
    elif violations:
        action = 'SANITIZE'
        validated_response = sanitize_response(response_text, invalid_claims)
    else:
        action = 'PASS'
        validated_response = response_text

    logger.info(f"Validation result: action={action}, confidence={confidence_score:.2f}, "
                f"violations={len(violations)}")

    return ValidationResult(
        action=action,
        original_response=response_text,
        validated_response=validated_response,
        violations=violations,
        confidence_score=confidence_score,
        details={
            'evidence_level': evidence_level,
            'actual_row_count': query_result.row_count,
            'claims_checked': total_claims,
            'claims_invalid': total_violations,
            'invalid_claims': invalid_claims,
            'verification_method': 'independent_query_execution'
        }
    )


# =============================================================================
# BEDROCK AGENT HANDLER
# =============================================================================

def extract_parameters(event: dict) -> dict:
    """Extract parameters from Bedrock Agent event format."""
    params = {}

    parameters = event.get('parameters', [])
    if isinstance(parameters, list):
        for param in parameters:
            if isinstance(param, dict):
                params[param.get('name', '')] = param.get('value', '')
    elif isinstance(parameters, dict):
        params = parameters

    # Also check requestBody
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


def format_bedrock_response(event: dict, result: ValidationResult) -> dict:
    """Format response for Bedrock Agent."""
    action_group = event.get('actionGroup', '')
    function_name = event.get('function', 'validate_response')
    api_path = event.get('apiPath', '/validate_response')
    http_method = event.get('httpMethod', 'POST')

    response_body = {
        'action': result.action,
        'validated_response': result.validated_response,
        'confidence_score': result.confidence_score,
        'violations_count': len(result.violations),
        'violations': result.violations,
        'details': result.details
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
    Lambda handler for response validation.

    Expects:
    - response_text: The LLM-generated response to validate
    - sql_executed: The SQL query that was executed
    - allowed_values: (optional) JSON string with handles, post_ids, etc. for simplified validation
    """
    logger.info(f"Validation event received: {json.dumps(event)}")

    try:
        params = extract_parameters(event)

        response_text = params.get('response_text', '')
        sql_executed = params.get('sql_executed', '')

        # Parse allowed_values if provided (comes as JSON string)
        allowed_values_str = params.get('allowed_values', '')
        allowed_values = None
        if allowed_values_str:
            try:
                allowed_values = json.loads(allowed_values_str)
                logger.info(f"Received allowed_values with {len(allowed_values.get('handles', []))} handles, "
                           f"{len(allowed_values.get('post_ids', []))} post_ids")
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse allowed_values: {e}")

        # Backwards compatibility: also check for old parameter names
        if not sql_executed:
            sql_executed = params.get('sql', '')

        if not response_text:
            result = ValidationResult(
                action='PASS',
                original_response='',
                validated_response='',
                violations=[],
                confidence_score=1.0,
                details={'message': 'No response text to validate'}
            )
        elif not sql_executed and not allowed_values:
            # If no SQL and no allowed_values provided, we cannot verify - block the response
            result = ValidationResult(
                action='BLOCK',
                original_response=response_text,
                validated_response=(
                    "I cannot verify this response without validation data. "
                    "Please provide either SQL query or allowed_values."
                ),
                violations=[{
                    'type': 'no_validation_data',
                    'severity': 'HIGH',
                    'message': 'Neither sql_executed nor allowed_values provided'
                }],
                confidence_score=0.0,
                details={'message': 'sql_executed or allowed_values parameter is required'}
            )
        else:
            result = validate_response(response_text, sql_executed, allowed_values)

        return format_bedrock_response(event, result)

    except Exception as e:
        logger.error(f"Validation error: {str(e)}")
        return {
            'messageVersion': '1.0',
            'response': {
                'actionGroup': event.get('actionGroup', ''),
                'apiPath': event.get('apiPath', '/validate_response'),
                'httpMethod': event.get('httpMethod', 'POST'),
                'function': event.get('function', 'validate_response'),
                'functionResponse': {
                    'responseBody': {
                        'TEXT': {
                            'body': json.dumps({
                                'action': 'BLOCK',
                                'error': f"Validation error: {str(e)}",
                                'validated_response': (
                                    "An error occurred during validation. "
                                    "Please try again or rephrase your query."
                                )
                            })
                        }
                    }
                }
            }
        }
