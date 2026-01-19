"""
OpenAI Function Calling Tool Definitions

Defines the tools available to the LLM orchestrator using OpenAI's function calling schema.
Tool descriptions are pulled from prompts.py for centralized management.
"""

from prompts import get_prompt

# =============================================================================
# TOOL DEFINITIONS FOR OPENAI FUNCTION CALLING
# =============================================================================

def get_tool_definitions() -> list:
    """
    Get the list of tool definitions for OpenAI function calling.

    Returns:
        List of tool definition dicts in OpenAI format
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "query_database",
                "description": get_prompt("tool_descriptions", "query_database"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "The SQL SELECT query to execute. Must be a valid SELECT statement with appropriate LIMIT clause."
                        },
                        "explanation": {
                            "type": "string",
                            "description": "Brief explanation of what this query retrieves and why it answers the user's question."
                        }
                    },
                    "required": ["sql", "explanation"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_knowledge_base",
                "description": get_prompt("tool_descriptions", "search_knowledge_base"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language search query describing what to find. Should describe features, atmosphere, or characteristics."
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return. Default is 5, maximum is 10.",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 10
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_help_info",
                "description": get_prompt("tool_descriptions", "get_help_info"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Help topic to retrieve information about.",
                            "enum": ["general", "sql_queries", "semantic_search", "greeting"]
                        }
                    },
                    "required": ["topic"]
                }
            }
        }
    ]


# =============================================================================
# TOOL NAME CONSTANTS
# =============================================================================

TOOL_QUERY_DATABASE = "query_database"
TOOL_SEARCH_KNOWLEDGE_BASE = "search_knowledge_base"
TOOL_GET_HELP_INFO = "get_help_info"

ALL_TOOL_NAMES = [
    TOOL_QUERY_DATABASE,
    TOOL_SEARCH_KNOWLEDGE_BASE,
    TOOL_GET_HELP_INFO,
]


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def is_valid_tool_name(name: str) -> bool:
    """Check if a tool name is valid."""
    return name in ALL_TOOL_NAMES


def validate_tool_call(tool_name: str, arguments: dict) -> tuple[bool, str]:
    """
    Validate a tool call's arguments.

    Args:
        tool_name: Name of the tool being called
        arguments: Arguments passed to the tool

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not is_valid_tool_name(tool_name):
        return False, f"Unknown tool: {tool_name}"

    if tool_name == TOOL_QUERY_DATABASE:
        if "sql" not in arguments:
            return False, "Missing required parameter: sql"
        sql = arguments["sql"].strip().upper()
        if not sql.startswith("SELECT"):
            return False, "Only SELECT queries are allowed"
        # Check for dangerous keywords
        dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "CREATE"]
        for keyword in dangerous:
            if keyword in sql:
                return False, f"Query contains forbidden keyword: {keyword}"

    elif tool_name == TOOL_SEARCH_KNOWLEDGE_BASE:
        if "query" not in arguments:
            return False, "Missing required parameter: query"
        if arguments.get("top_k", 5) > 10:
            return False, "top_k cannot exceed 10"

    elif tool_name == TOOL_GET_HELP_INFO:
        if "topic" not in arguments:
            return False, "Missing required parameter: topic"
        valid_topics = ["general", "sql_queries", "semantic_search", "greeting"]
        if arguments["topic"] not in valid_topics:
            return False, f"Invalid topic. Must be one of: {valid_topics}"

    return True, ""
