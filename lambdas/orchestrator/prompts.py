"""
Centralized Prompt Management for LLM Orchestrator

Single source of truth for all LLM prompts - NO prompts in other code files.
"""

from typing import Any

# =============================================================================
# PROMPT TEMPLATES
# =============================================================================

PROMPTS = {
    # -------------------------------------------------------------------------
    # SYSTEM PROMPTS
    # -------------------------------------------------------------------------
    "system": {
        "orchestrator": """You are an Instagram Analytics assistant that helps users analyze restaurant and social media data.

## Your Capabilities
You have access to three tools:
1. **query_database** - Execute SQL queries against the database
2. **search_knowledge_base** - Semantic search for restaurant descriptions and features
3. **get_help_info** - Provide help information about available features

## Database Schema
{schema}

## Knowledge Base
The knowledge base contains restaurant bios and descriptions that can be searched semantically. Use this for queries about:
- Restaurant features (outdoor seating, vegan options, ambiance)
- Restaurant styles and cuisines
- Atmosphere descriptions

## Guidelines
1. ALWAYS use the appropriate tool to get data - never make up information
2. For data queries (counts, rankings, lists), use query_database with SQL
3. For feature/description searches, use search_knowledge_base
4. For help requests, use get_help_info
5. Present data in clear markdown tables when appropriate
6. Be concise but helpful
7. If you cannot fulfill a request, explain why

## SQL Generation Rules
When generating SQL:
- Only generate SELECT queries
- Always include a LIMIT clause (max 100 for normal queries)
- Use ILIKE for case-insensitive text matching
- For status filtering, use WHERE status = 'FOUND' on restaurants table
- Handle NULL values appropriately
- For hashtag analysis, use: unnest(string_split(hashtags, ' '))

## Anti-Hallucination Rules
- NEVER invent data - only report what tools return
- If a query returns no results, say so clearly
- If you're uncertain, ask for clarification
- Always base your response on actual tool results""",
    },

    # -------------------------------------------------------------------------
    # TOOL DESCRIPTIONS
    # -------------------------------------------------------------------------
    "tool_descriptions": {
        "query_database": "Execute SQL queries against the Instagram analytics database. Use this for data retrieval, counts, rankings, aggregations, and any structured data queries. The database contains 'posts' (Instagram posts with engagement metrics) and 'restaurants' (restaurant Instagram profiles) tables.",

        "search_knowledge_base": "Perform semantic search on restaurant descriptions and bios. Use this when the user asks about restaurant features, atmosphere, cuisine types, or any qualitative aspects that require understanding context rather than exact matching.",

        "get_help_info": "Provide help information about available features and example queries. Use this when users ask for help, want to know capabilities, or need guidance on how to phrase queries.",
    },

    # -------------------------------------------------------------------------
    # ERROR MESSAGES
    # -------------------------------------------------------------------------
    "error_messages": {
        "rate_limit": "I'm experiencing high demand right now. Please try again in a moment.",

        "invalid_sql": "I couldn't generate a valid query for that request. Could you please rephrase your question?",

        "no_results": "I couldn't find any data matching your query. Try adjusting your search criteria.",

        "tool_error": "I encountered an error while processing your request: {error}",

        "max_iterations": "I wasn't able to complete your request within the allowed steps. Please try simplifying your query.",

        "unknown_error": "I apologize, but I encountered an unexpected error. Please try again.",
    },

    # -------------------------------------------------------------------------
    # HELP TOPICS
    # -------------------------------------------------------------------------
    "help_topics": {
        "general": """I can help you analyze Instagram data for restaurants. Here's what I can do:

**Query Restaurant Data:**
- "Show top 10 restaurants by followers"
- "How many restaurants are in the database?"
- "List restaurants in [city]"

**Analyze Posts:**
- "Show top 5 posts by likes"
- "What's the average engagement?"
- "Posts from @username"
- "Top hashtags by engagement"

**Semantic Search:**
- "Find restaurants with outdoor seating"
- "Restaurants with vegan options"
- "Places with rooftop dining"

Just ask me a question and I'll help you find the data!""",

        "sql_queries": """You can ask about structured data like:
- Rankings: "Top N restaurants/posts by X"
- Counts: "How many restaurants/posts"
- Filtering: "Restaurants in [city]", "Posts by @user"
- Aggregations: "Average likes", "Total comments"
- Hashtag analysis: "Top hashtags by likes"

The database contains posts (with likes, comments, hashtags) and restaurants (with followers, posts_count, city).""",

        "semantic_search": """For feature-based queries, I can search restaurant descriptions:
- Ambiance: "cozy atmosphere", "romantic setting"
- Features: "outdoor seating", "private dining"
- Cuisine: "Italian food", "vegan options"
- Style: "fine dining", "casual brunch spot"

This uses AI-powered semantic matching on restaurant bios.""",

        "greeting": "Hello! I'm your Instagram Analytics assistant. How can I help you today? Try asking about top restaurants, popular posts, or search for specific features. Type 'help' to see all my capabilities.",
    },
}


# =============================================================================
# SCHEMA FORMATTER
# =============================================================================

def format_schema_for_prompt(table_schemas: dict) -> str:
    """
    Format table schemas into a readable string for the system prompt.

    Args:
        table_schemas: Dict with table definitions from config.py

    Returns:
        Formatted schema string
    """
    lines = []
    for table_name, schema in table_schemas.items():
        lines.append(f"### {table_name} table")
        lines.append(f"{schema.get('description', '')}")
        lines.append("")
        lines.append("| Column | Type | Description |")
        lines.append("|--------|------|-------------|")
        for col_name, col_type, col_desc in schema.get('columns', []):
            lines.append(f"| {col_name} | {col_type} | {col_desc} |")
        lines.append("")
    return "\n".join(lines)


# =============================================================================
# PROMPT ACCESSOR
# =============================================================================

def get_prompt(category: str, key: str, **kwargs: Any) -> str:
    """
    Get a prompt template with optional variable substitution.

    Args:
        category: Top-level category (system, tool_descriptions, error_messages, help_topics)
        key: Specific prompt key within the category
        **kwargs: Variables to substitute in the template

    Returns:
        Formatted prompt string

    Raises:
        KeyError: If category or key not found

    Example:
        >>> get_prompt("system", "orchestrator", schema=schema_str)
        >>> get_prompt("error_messages", "tool_error", error="Connection failed")
    """
    if category not in PROMPTS:
        raise KeyError(f"Unknown prompt category: {category}")

    if key not in PROMPTS[category]:
        raise KeyError(f"Unknown prompt key: {key} in category {category}")

    template = PROMPTS[category][key]

    if kwargs:
        return template.format(**kwargs)
    return template


def get_system_prompt(table_schemas: dict) -> str:
    """
    Build the complete system prompt with schema information.

    Args:
        table_schemas: Table schema definitions from config.py

    Returns:
        Complete system prompt ready for LLM
    """
    schema_str = format_schema_for_prompt(table_schemas)
    return get_prompt("system", "orchestrator", schema=schema_str)


def get_help_response(topic: str = "general") -> str:
    """
    Get a help response for the given topic.

    Args:
        topic: Help topic key (general, sql_queries, semantic_search, greeting)

    Returns:
        Help text for the topic
    """
    return get_prompt("help_topics", topic)


def get_error_message(error_type: str, **kwargs: Any) -> str:
    """
    Get an error message with optional formatting.

    Args:
        error_type: Error type key
        **kwargs: Variables to substitute

    Returns:
        Formatted error message
    """
    return get_prompt("error_messages", error_type, **kwargs)
