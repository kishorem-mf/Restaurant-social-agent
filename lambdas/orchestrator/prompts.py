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

## Tool Selection Guidelines

### When to use search_knowledge_base (PRIORITY for content-based queries):
- **Cuisine types and food styles**: "Italian restaurants", "vegan options", "Thai cuisine", "Mexican food"
- **Chef/blogger/creator content**: "chef posts", "food blogger content", "influencer posts"
- **Restaurant features**: "outdoor seating", "rooftop dining", "romantic atmosphere", "private dining"
- **Content themes**: "healthy food", "dessert posts", "brunch spots", "fine dining"
- **ANY query about what restaurants offer or post about** (qualitative aspects)

### When to use query_database (ONLY for structured data):
- **Numeric rankings by engagement metrics**: "top 10 restaurants by followers", "most liked posts"
- **Counts and aggregations**: "how many restaurants", "total posts", "average engagement"
- **Specific handle/creator lookups**: "posts by @username", "restaurants in [city]"
- **Date-based queries**: "posts from last month", "recently added restaurants"

### Critical Rule for Hybrid Queries:
If a query involves BOTH content matching AND ranking (e.g., "top Italian restaurants"):
1. FIRST use search_knowledge_base to find relevant items by content
2. THEN optionally use query_database to rank results by engagement metrics
3. Combine both results in your response

### General Guidelines:
1. ALWAYS use the appropriate tool to get data - never make up information
2. For help requests, use get_help_info
3. Present data in clear markdown tables when appropriate
4. Be concise but helpful
5. If you cannot fulfill a request, explain why

## Hybrid Results (Automatic Enhancement)

For content-based queries, the system automatically provides BOTH SQL and semantic search results:
- When SQL returns sparse results (0-5 rows) for content queries, semantic search is automatically executed
- You'll receive `semantic_results` in addition to SQL `data`
- **Always prioritize semantic results** for content queries as they better match user intent
- Use SQL results for exact matches or structured data (counts, followers, etc.)
- Combine both sources to provide the most comprehensive answer

Example response structure:
- If SQL returns 2 rows but semantic returns 10 items → Present semantic results as primary answer
- If both have results → Merge and present the union, highlighting the best matches
- Always acknowledge both data sources when presenting combined results

## SQL Generation Rules
When generating SQL:
- Only generate SELECT queries
- Always include a LIMIT clause (max 100 for normal queries)
- Use ILIKE for case-insensitive text matching
- For restaurants table: use WHERE status = 'FOUND' to filter valid restaurants
- For posts table: use WHERE status = 'active' to filter valid posts
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

        "search_knowledge_base": "Perform semantic search on restaurant descriptions, bios, and post content. Use this FIRST when users ask about cuisine types (Italian, Thai, vegan), content themes (chef posts, desserts, healthy food), restaurant features (outdoor seating, ambiance), or any qualitative aspects. This searches actual content/bios, not hashtags or metadata. Always prefer this over SQL for content-based matching.",

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
