"""
Orchestrator Configuration

Environment variables and constants for the orchestrator.
"""

import json
import logging
import os
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# =============================================================================
# ENVIRONMENT CONFIGURATION
# =============================================================================

# AWS Configuration
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
S3_REGION = os.environ.get('S3_REGION', 'us-east-1')

# Lambda Function Names (for direct invocation)
QUERY_DATA_LAMBDA = os.environ.get('QUERY_DATA_LAMBDA', 'instagram-query-data')
RESPONSE_VALIDATOR_LAMBDA = os.environ.get('RESPONSE_VALIDATOR_LAMBDA', 'instagram-response-validator')

# Bedrock Knowledge Base
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID', 'QQJTQJ1VWU')

# Secrets Manager Configuration
AZURE_OPENAI_SECRET_NAME = os.environ.get('AZURE_OPENAI_SECRET_NAME', 'azure-openai-credentials')


# =============================================================================
# SECRETS MANAGER HELPER
# =============================================================================

@lru_cache(maxsize=1)
def get_azure_openai_credentials() -> dict:
    """
    Retrieve Azure OpenAI credentials from AWS Secrets Manager.
    Results are cached for the Lambda execution lifetime.

    Returns:
        dict with keys: AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT,
                       AZURE_API_VERSION, AZURE_OPENAI_DEPLOYMENT_NAME
    """
    # Allow environment variable override for local development
    if os.environ.get('AZURE_OPENAI_API_KEY'):
        logger.info("Using Azure OpenAI credentials from environment variables")
        return {
            'AZURE_OPENAI_API_KEY': os.environ.get('AZURE_OPENAI_API_KEY', ''),
            'AZURE_OPENAI_ENDPOINT': os.environ.get('AZURE_OPENAI_ENDPOINT', ''),
            'AZURE_API_VERSION': os.environ.get('AZURE_API_VERSION', '2024-12-01-preview'),
            'AZURE_OPENAI_DEPLOYMENT_NAME': os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4o-mini'),
            'AZURE_OPENAI_API_VERSION': os.environ.get('AZURE_OPENAI_API_VERSION', '2024-12-01-preview'),
        }

    try:
        client = boto3.client('secretsmanager', region_name=AWS_REGION)
        response = client.get_secret_value(SecretId=AZURE_OPENAI_SECRET_NAME)
        secret = json.loads(response['SecretString'])
        logger.info("Successfully retrieved Azure OpenAI credentials from Secrets Manager")
        return secret
    except ClientError as e:
        logger.error(f"Failed to retrieve secret {AZURE_OPENAI_SECRET_NAME}: {e}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse secret JSON: {e}")
        raise


# Azure OpenAI Configuration (lazy-loaded from Secrets Manager)
def get_azure_openai_config():
    """Get Azure OpenAI configuration. Call this when needed, not at module load."""
    creds = get_azure_openai_credentials()
    return {
        'endpoint': creds.get('AZURE_OPENAI_ENDPOINT', ''),
        'api_key': creds.get('AZURE_OPENAI_API_KEY', ''),
        'deployment': creds.get('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4o-mini'),
        'api_version': creds.get('AZURE_OPENAI_API_VERSION', '2024-12-01-preview'),
    }


# Legacy compatibility - these will be populated on first access
# For Phase 2 LLM integration
AZURE_OPENAI_ENDPOINT = None  # Use get_azure_openai_config() instead
AZURE_OPENAI_API_KEY = None   # Use get_azure_openai_config() instead
AZURE_OPENAI_DEPLOYMENT = None
AZURE_OPENAI_API_VERSION = None

# =============================================================================
# TOOL CONFIGURATION
# =============================================================================

# Query limits
MAX_SQL_ROWS = 1000
DEFAULT_SQL_LIMIT = 100
MAX_VECTOR_RESULTS = 10
DEFAULT_VECTOR_RESULTS = 5

# =============================================================================
# SCHEMA DEFINITIONS
# =============================================================================

# Table schemas for SQL generation context
TABLE_SCHEMAS = {
    "posts": {
        "description": "Instagram posts data with engagement metrics",
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
        "description": "Restaurant Instagram profiles and metrics",
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
    }
}

# =============================================================================
# INTENT PATTERNS
# =============================================================================

# Keywords for intent detection
INTENT_PATTERNS = {
    "sql_query": [
        "how many", "show", "list", "top", "best", "most", "least",
        "count", "average", "total", "sum", "find", "get", "select",
        "restaurants", "posts", "followers", "likes", "comments",
        "order by", "sort", "rank", "compare"
    ],
    "vector_search": [
        "describe", "similar", "like", "about", "recommend",
        "outdoor", "seating", "vegan", "atmosphere", "ambiance",
        "style", "type of", "kind of", "cuisine", "feature"
    ],
    "general_question": [
        "what is", "explain", "help", "how do", "can you"
    ]
}

# =============================================================================
# PROMPT TEMPLATES
# =============================================================================

SYSTEM_PROMPT = """You are an Instagram Analytics assistant that helps users analyze restaurant and social media data.

You have access to the following data tables:

## posts table
Contains Instagram posts with engagement metrics:
- post_id: Unique identifier
- creator: Instagram handle of the creator
- posted_date: When the post was published
- likes: Number of likes
- comments: Number of comments
- hashtags: Hashtags used in the post
- caption: Post caption text
- image_description: AI-generated description of the image
- search_term: The hashtag used to find this post

## restaurants table
Contains restaurant Instagram profiles:
- restaurant_name: Name of the restaurant
- instagram_handle: Instagram username
- followers: Number of Instagram followers
- posts_count: Number of posts on their profile
- bio: Instagram bio text
- city: City location
- phone: Contact phone number
- website: Website URL
- status: FOUND, NOT_FOUND, or EMPTY_PROFILE

Guidelines:
1. When answering questions that require specific data, ALWAYS query the database first
2. For questions about restaurant descriptions/features, use vector search on bios
3. Present data in clear markdown tables when appropriate
4. Be concise but helpful
5. NEVER make up data - only report what is in the query results
6. If data is not available, clearly say so
"""

SQL_GENERATION_PROMPT = """Based on the user's question, generate a SQL query to retrieve the relevant data.

Available tables and columns:
{schema}

User question: {question}

Requirements:
- Generate ONLY a SELECT query
- Use appropriate JOINs if needed
- Include LIMIT (default 100, max 1000)
- Use ORDER BY when ranking is implied
- Handle NULL values appropriately

Return ONLY the SQL query, no explanation."""

RESPONSE_FORMAT_PROMPT = """Format the following data into a clear, helpful response for the user.

User question: {question}

Query results:
{data}

Guidelines:
- Use markdown tables for structured data
- Summarize key insights
- Be concise but complete
- If no data was returned, explain what was searched
- Include relevant context about the data
"""
