"""
Tool Wrappers for Orchestrator

Provides unified interface to query_data, vector_search, and response_validator.
Handles Bedrock Agent response format extraction.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import boto3

from config import (
    AWS_REGION,
    QUERY_DATA_LAMBDA,
    RESPONSE_VALIDATOR_LAMBDA,
    KNOWLEDGE_BASE_ID,
    DEFAULT_VECTOR_RESULTS,
)

logger = logging.getLogger(__name__)

# AWS Clients (lazy initialization)
_lambda_client = None
_bedrock_agent_client = None


def get_lambda_client():
    """Get or create Lambda client."""
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client('lambda', region_name=AWS_REGION)
    return _lambda_client


def get_bedrock_agent_client():
    """Get or create Bedrock Agent Runtime client."""
    global _bedrock_agent_client
    if _bedrock_agent_client is None:
        _bedrock_agent_client = boto3.client('bedrock-agent-runtime', region_name=AWS_REGION)
    return _bedrock_agent_client


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class QueryDataResult:
    """Result from query_data tool."""
    success: bool
    data: list
    columns: list
    row_count: int
    markdown_table: str
    sql_executed: str
    error: Optional[str] = None
    evidence_metadata: Optional[dict] = None
    allowed_values: Optional[dict] = None  # For simplified anti-hallucination


@dataclass
class VectorSearchResult:
    """Result from vector_search tool."""
    success: bool
    results: list
    query: str
    error: Optional[str] = None


@dataclass
class ValidationResult:
    """Result from response_validator tool."""
    action: str  # PASS, SANITIZE, BLOCK
    validated_response: str
    confidence_score: float
    violations_count: int
    violations: list
    details: dict


# =============================================================================
# BEDROCK RESPONSE EXTRACTION
# =============================================================================

def extract_from_bedrock_response(response_payload: dict) -> dict:
    """
    Extract the actual response body from Bedrock Agent nested format.

    Bedrock Agent format:
    {
        "messageVersion": "1.0",
        "response": {
            "functionResponse": {
                "responseBody": {
                    "TEXT": {
                        "body": "{...actual JSON...}"
                    }
                }
            }
        }
    }

    Returns the parsed body dict.
    """
    try:
        # Navigate the nested structure
        response = response_payload.get('response', {})
        function_response = response.get('functionResponse', {})
        response_body = function_response.get('responseBody', {})
        text_body = response_body.get('TEXT', {})
        body_str = text_body.get('body', '{}')

        # Parse the JSON string
        if isinstance(body_str, str):
            return json.loads(body_str)
        return body_str

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"Failed to extract from Bedrock response: {e}")
        logger.debug(f"Response payload: {response_payload}")
        return {}


# =============================================================================
# TOOL: QUERY DATA
# =============================================================================

def invoke_query_data(sql: str, user_query: Optional[str] = None) -> QueryDataResult:
    """
    Execute SQL query via query_data Lambda.

    Args:
        sql: SQL SELECT query to execute
        user_query: Original natural language query (for logging)

    Returns:
        QueryDataResult with data or error
    """
    logger.info(f"Invoking query_data with SQL: {sql[:100]}...")

    try:
        client = get_lambda_client()

        # Build Bedrock Agent format request
        payload = {
            'function': 'execute_sql',
            'parameters': [
                {'name': 'sql', 'value': sql}
            ]
        }

        if user_query:
            payload['inputText'] = user_query

        # Invoke Lambda
        response = client.invoke(
            FunctionName=QUERY_DATA_LAMBDA,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )

        # Parse response
        response_payload = json.loads(response['Payload'].read())

        # Extract from Bedrock format
        body = extract_from_bedrock_response(response_payload)

        if body.get('success'):
            return QueryDataResult(
                success=True,
                data=body.get('data', []),
                columns=body.get('columns', []),
                row_count=body.get('row_count', 0),
                markdown_table=body.get('markdown_table', ''),
                sql_executed=body.get('sql_executed', sql),
                evidence_metadata=body.get('evidence_metadata'),
                allowed_values=body.get('allowed_values')
            )
        else:
            return QueryDataResult(
                success=False,
                data=[],
                columns=[],
                row_count=0,
                markdown_table='',
                sql_executed=sql,
                error=body.get('error', 'Unknown error'),
                allowed_values=body.get('allowed_values')
            )

    except Exception as e:
        logger.error(f"query_data invocation failed: {e}")
        return QueryDataResult(
            success=False,
            data=[],
            columns=[],
            row_count=0,
            markdown_table='',
            sql_executed=sql,
            error=str(e)
        )


# =============================================================================
# TOOL: VECTOR SEARCH
# =============================================================================

def invoke_vector_search(query: str, top_k: int = DEFAULT_VECTOR_RESULTS) -> VectorSearchResult:
    """
    Perform semantic search via Bedrock Knowledge Base.

    Args:
        query: Natural language search query
        top_k: Number of results to return

    Returns:
        VectorSearchResult with matched documents
    """
    logger.info(f"Invoking vector_search with query: {query[:50]}...")

    try:
        client = get_bedrock_agent_client()

        # Call Bedrock Knowledge Base retrieve API
        response = client.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={'text': query},
            retrievalConfiguration={
                'vectorSearchConfiguration': {
                    'numberOfResults': top_k
                }
            }
        )

        # Extract results
        retrieval_results = response.get('retrievalResults', [])

        results = []
        for r in retrieval_results:
            content = r.get('content', {})
            results.append({
                'content': content.get('text', ''),
                'score': r.get('score', 0.0),
                'metadata': r.get('metadata', {}),
                'location': r.get('location', {})
            })

        logger.info(f"Vector search returned {len(results)} results")

        return VectorSearchResult(
            success=True,
            results=results,
            query=query
        )

    except Exception as e:
        logger.error(f"vector_search invocation failed: {e}")
        return VectorSearchResult(
            success=False,
            results=[],
            query=query,
            error=str(e)
        )


# =============================================================================
# TOOL: RESPONSE VALIDATOR
# =============================================================================

def invoke_response_validator(
    response_text: str,
    sql_executed: str,
    allowed_values: Optional[dict] = None
) -> ValidationResult:
    """
    Validate LLM response against query evidence using allowed_values.

    Args:
        response_text: The LLM-generated response to validate
        sql_executed: The SQL query that was executed
        allowed_values: Dict with 'handles', 'post_ids', 'restaurant_names', 'creators'
                       for simplified lookup-based validation

    Returns:
        ValidationResult with action and validated response
    """
    logger.info("Invoking response_validator...")

    try:
        client = get_lambda_client()

        # Build Bedrock Agent format request
        parameters = [
            {'name': 'response_text', 'value': response_text},
            {'name': 'sql_executed', 'value': sql_executed}
        ]

        # Pass allowed_values as JSON string for simplified validation
        if allowed_values:
            parameters.append({
                'name': 'allowed_values',
                'value': json.dumps(allowed_values)
            })

        payload = {
            'function': 'validate_response',
            'parameters': parameters
        }

        # Invoke Lambda
        response = client.invoke(
            FunctionName=RESPONSE_VALIDATOR_LAMBDA,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )

        # Parse response
        response_payload = json.loads(response['Payload'].read())

        # Extract from Bedrock format
        body = extract_from_bedrock_response(response_payload)

        return ValidationResult(
            action=body.get('action', 'PASS'),
            validated_response=body.get('validated_response', response_text),
            confidence_score=body.get('confidence_score', 1.0),
            violations_count=body.get('violations_count', 0),
            violations=body.get('violations', []),
            details=body.get('details', {})
        )

    except Exception as e:
        logger.error(f"response_validator invocation failed: {e}")
        # On error, default to passing the original response
        # (fail-open for availability, but log the error)
        return ValidationResult(
            action='PASS',
            validated_response=response_text,
            confidence_score=0.5,
            violations_count=0,
            violations=[],
            details={'error': str(e), 'fallback': True}
        )


# =============================================================================
# DIRECT FORMAT TOOLS (for local testing without Lambda)
# =============================================================================

def query_data_direct(sql: str) -> QueryDataResult:
    """
    Execute SQL query directly using DuckDB (for local testing).
    Bypasses Lambda invocation.
    """
    import duckdb
    from .config import S3_REGION, TABLE_SCHEMAS

    # This is a simplified version for local testing
    # In production, use invoke_query_data

    try:
        conn = duckdb.connect()
        conn.execute("SET home_directory='/tmp';")
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute(f"SET s3_region='{S3_REGION}';")

        # For local testing, you would need to set up views
        # This is just a placeholder
        result = conn.execute(sql)
        rows = result.fetchall()
        columns = [desc[0] for desc in result.description]

        data = [dict(zip(columns, row)) for row in rows]

        return QueryDataResult(
            success=True,
            data=data,
            columns=columns,
            row_count=len(data),
            markdown_table='',  # Would need to generate
            sql_executed=sql
        )

    except Exception as e:
        return QueryDataResult(
            success=False,
            data=[],
            columns=[],
            row_count=0,
            markdown_table='',
            sql_executed=sql,
            error=str(e)
        )


# =============================================================================
# TOOL REGISTRY
# =============================================================================

TOOLS = {
    'query_data': {
        'function': invoke_query_data,
        'description': 'Execute SQL queries against posts and restaurants tables',
        'parameters': {
            'sql': 'SQL SELECT query to execute',
            'user_query': 'Original natural language query (optional)'
        }
    },
    'vector_search': {
        'function': invoke_vector_search,
        'description': 'Semantic search for restaurant descriptions and features',
        'parameters': {
            'query': 'Natural language search query',
            'top_k': 'Number of results to return (default: 5)'
        }
    },
    'response_validator': {
        'function': invoke_response_validator,
        'description': 'Validate LLM response against query evidence',
        'parameters': {
            'response_text': 'The response to validate',
            'sql_executed': 'The SQL query that generated the data'
        }
    }
}
