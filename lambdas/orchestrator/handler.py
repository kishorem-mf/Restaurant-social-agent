"""
Orchestrator Lambda Handler

Main entry point for the custom agent orchestrator.
Handles chat requests and coordinates tool execution.
"""

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

from config import (
    INTENT_PATTERNS,
    SYSTEM_PROMPT,
    SQL_GENERATION_PROMPT,
    RESPONSE_FORMAT_PROMPT,
    TABLE_SCHEMAS,
)
from tools import (
    invoke_query_data,
    invoke_vector_search,
    invoke_response_validator,
)

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ChatRequest:
    """Incoming chat request."""
    message: str
    conversation_history: list
    session_id: Optional[str] = None


@dataclass
class ChatResponse:
    """Outgoing chat response."""
    response: str
    metadata: dict
    error: Optional[str] = None


# =============================================================================
# INTENT DETECTION
# =============================================================================

def detect_intent(message: str) -> str:
    """
    Detect the user's intent from their message.

    Returns:
        'sql_query' - Needs database query
        'vector_search' - Needs semantic search
        'general_question' - General chat/help
        'hybrid' - Needs both SQL and vector search
    """
    message_lower = message.lower()

    # Score each intent
    scores = {intent: 0 for intent in INTENT_PATTERNS.keys()}

    for intent, keywords in INTENT_PATTERNS.items():
        for keyword in keywords:
            if keyword in message_lower:
                scores[intent] += 1

    # Determine primary intent
    max_score = max(scores.values())
    if max_score == 0:
        return 'general_question'

    # Check for hybrid (both SQL and vector search signals)
    if scores['sql_query'] > 0 and scores['vector_search'] > 0:
        return 'hybrid'

    # Return highest scoring intent
    return max(scores, key=scores.get)


# =============================================================================
# SQL GENERATION (Simple Pattern-Based)
# =============================================================================

def generate_sql_from_message(message: str) -> Optional[str]:
    """
    Generate SQL query from natural language message.
    This is a simple pattern-based approach for Phase 1.
    In Phase 2, this will use Azure OpenAI.

    Returns:
        SQL query string or None if cannot generate
    """
    message_lower = message.lower()

    # Pattern: "top N restaurants by X"
    top_match = re.search(r'top\s+(\d+)\s+restaurants?\s+(?:by|with)\s+(?:most\s+)?(\w+)', message_lower)
    if top_match:
        n = int(top_match.group(1))
        metric = top_match.group(2)

        # Map natural language to column names
        column_map = {
            'followers': 'followers',
            'following': 'followers',
            'posts': 'posts_count',
            'post': 'posts_count',
        }
        order_col = column_map.get(metric, 'followers')

        return f"""SELECT restaurant_name, instagram_handle, followers, posts_count, city
FROM restaurants
WHERE status = 'FOUND'
ORDER BY {order_col} DESC
LIMIT {min(n, 100)}"""

    # Pattern: "show restaurants" or "list restaurants"
    if re.search(r'(show|list|get)\s+(all\s+)?restaurants?', message_lower):
        return """SELECT restaurant_name, instagram_handle, followers, posts_count, city
FROM restaurants
WHERE status = 'FOUND'
ORDER BY followers DESC
LIMIT 20"""

    # Pattern: "top N posts by likes/comments"
    posts_match = re.search(r'top\s+(\d+)\s+posts?\s+(?:by|with|based\s+on)\s+(?:most\s+)?(\w+)', message_lower)
    if posts_match:
        n = int(posts_match.group(1))
        metric = posts_match.group(2)

        column_map = {
            'likes': 'likes',
            'like': 'likes',
            'comments': 'comments',
            'comment': 'comments',
            'engagement': 'likes + comments',
        }
        order_col = column_map.get(metric, 'likes')

        return f"""SELECT creator, caption, likes, comments, posted_date, hashtags
FROM posts
ORDER BY {order_col} DESC
LIMIT {min(n, 100)}"""

    # Pattern: "top posts based on/by likes" (without number - defaults to 10)
    posts_match2 = re.search(r'(?:what\s+are\s+)?top\s+posts?\s+(?:by|with|based\s+on)\s+(?:most\s+)?(\w+)', message_lower)
    if posts_match2:
        metric = posts_match2.group(1)

        column_map = {
            'likes': 'likes',
            'like': 'likes',
            'comments': 'comments',
            'comment': 'comments',
            'engagement': 'likes + comments',
        }
        order_col = column_map.get(metric, 'likes')

        return f"""SELECT creator, caption, likes, comments, posted_date, hashtags
FROM posts
ORDER BY {order_col} DESC
LIMIT 10"""

    # Pattern: "how many restaurants/posts"
    count_match = re.search(r'how\s+many\s+(restaurants?|posts?)', message_lower)
    if count_match:
        table = 'restaurants' if 'restaurant' in count_match.group(1) else 'posts'
        return f"SELECT COUNT(*) as total FROM {table}"

    # Pattern: "restaurants in [city]"
    city_match = re.search(r'restaurants?\s+(?:in|from)\s+([a-zA-Z\s]+)', message_lower)
    if city_match:
        city = city_match.group(1).strip().title()
        return f"""SELECT restaurant_name, instagram_handle, followers, posts_count
FROM restaurants
WHERE city ILIKE '%{city}%' AND status = 'FOUND'
ORDER BY followers DESC
LIMIT 20"""

    # Pattern: "average likes/comments"
    avg_match = re.search(r'average\s+(likes?|comments?)', message_lower)
    if avg_match:
        metric = 'likes' if 'like' in avg_match.group(1) else 'comments'
        return f"""SELECT AVG({metric}) as avg_{metric}, COUNT(*) as total_posts
FROM posts"""

    # Pattern: "posts from [creator]"
    creator_match = re.search(r'posts?\s+(?:from|by)\s+@?([a-zA-Z0-9_]+)', message_lower)
    if creator_match:
        creator = creator_match.group(1)
        return f"""SELECT caption, likes, comments, posted_date, hashtags
FROM posts
WHERE creator ILIKE '%{creator}%'
ORDER BY posted_date DESC
LIMIT 20"""

    # Default: couldn't generate SQL
    return None


# =============================================================================
# RESPONSE FORMATTING
# =============================================================================

def format_response_simple(query_result, user_message: str) -> str:
    """
    Format query results into a user-friendly response.
    Simple implementation for Phase 1.
    """
    if not query_result.success:
        return f"I encountered an error while querying the data: {query_result.error}"

    if query_result.row_count == 0:
        return "I couldn't find any data matching your query. Please try a different search."

    # Use the markdown table from query_data if available
    if query_result.markdown_table:
        response = f"Here's what I found:\n\n{query_result.markdown_table}"
    else:
        # Generate simple response from data
        response = f"Found {query_result.row_count} results:\n\n"
        for i, row in enumerate(query_result.data[:10], 1):
            response += f"{i}. "
            response += ", ".join(f"{k}: {v}" for k, v in row.items() if v)
            response += "\n"

    return response


def format_vector_response(search_result, user_message: str) -> str:
    """
    Format vector search results into a user-friendly response.
    """
    if not search_result.success:
        return f"I encountered an error during the search: {search_result.error}"

    if not search_result.results:
        return "I couldn't find any restaurants matching that description."

    response = "Based on my search, here are the relevant results:\n\n"

    for i, result in enumerate(search_result.results[:5], 1):
        content = result.get('content', '')
        score = result.get('score', 0)

        # Truncate long content
        if len(content) > 200:
            content = content[:200] + "..."

        response += f"**{i}.** {content}\n"
        response += f"   _(Relevance: {score:.2f})_\n\n"

    response += "\n*Note: This search uses semantic matching from restaurant bios and descriptions.*"

    return response


# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Main chat processing function.
    Orchestrates intent detection, tool execution, and response generation.
    """
    start_time = time.time()
    tools_used = []
    sql_executed = None
    confidence = 0.0
    validation_status = 'N/A'
    response_text = "I'm sorry, I couldn't process your request. Please try again."

    message = request.message

    # Step 1: Detect intent
    intent = detect_intent(message)
    logger.info(f"Detected intent: {intent}")

    # Step 2: Execute appropriate tools based on intent
    if intent == 'sql_query' or intent == 'hybrid':
        # Generate and execute SQL query
        sql = generate_sql_from_message(message)

        if sql:
            logger.info(f"Generated SQL: {sql}")
            tools_used.append('query_data')

            # Execute query
            query_result = invoke_query_data(sql, user_query=message)
            sql_executed = query_result.sql_executed

            if query_result.success:
                # Format response
                response_text = format_response_simple(query_result, message)

                # Validate response with allowed_values for simplified anti-hallucination
                tools_used.append('response_validator')
                validation = invoke_response_validator(
                    response_text,
                    sql_executed,
                    allowed_values=query_result.allowed_values
                )

                response_text = validation.validated_response
                confidence = validation.confidence_score
                validation_status = validation.action
            else:
                response_text = f"I encountered an error: {query_result.error}"
                confidence = 0.0
                validation_status = 'ERROR'
        else:
            # Couldn't generate SQL, try vector search
            intent = 'vector_search'

    if intent == 'vector_search':
        # Execute vector search
        tools_used.append('vector_search')
        search_result = invoke_vector_search(message)

        response_text = format_vector_response(search_result, message)
        confidence = 0.8 if search_result.success else 0.0
        validation_status = 'PASS' if search_result.success else 'ERROR'

    elif intent == 'general_question':
        # Handle general questions
        response_text = generate_help_response(message)
        confidence = 1.0
        validation_status = 'PASS'

    # Calculate response time
    response_time_ms = int((time.time() - start_time) * 1000)

    return ChatResponse(
        response=response_text,
        metadata={
            'tools_used': tools_used,
            'sql_executed': sql_executed,
            'confidence': confidence,
            'validation_status': validation_status,
            'intent': intent,
            'response_time_ms': response_time_ms
        }
    )


def generate_help_response(message: str) -> str:
    """Generate response for general questions/help."""
    message_lower = message.lower()

    if 'help' in message_lower or 'what can' in message_lower:
        return """I can help you analyze Instagram data for restaurants. Here's what I can do:

**Query Restaurant Data:**
- "Show top 10 restaurants by followers"
- "How many restaurants are in the database?"
- "List restaurants in [city]"

**Analyze Posts:**
- "Show top 5 posts by likes"
- "What's the average engagement?"
- "Posts from @username"

**Semantic Search:**
- "Find restaurants with outdoor seating"
- "Restaurants with vegan options"
- "Places with rooftop dining"

Just ask me a question and I'll help you find the data!"""

    if 'hello' in message_lower or 'hi' in message_lower:
        return "Hello! I'm your Instagram Analytics assistant. How can I help you today? Try asking about top restaurants, popular posts, or search for specific features."

    return "I'm here to help with Instagram analytics. Could you please be more specific about what you'd like to know? Type 'help' to see what I can do."


# =============================================================================
# LAMBDA HANDLER
# =============================================================================

def handler(event: dict, context: Any) -> dict:
    """
    Lambda handler for chat endpoint.

    Expects event with:
    - message: User's question
    - conversation_history: List of previous messages
    - session_id: Optional session identifier
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        # Parse request
        body = event
        if 'body' in event:
            # API Gateway format
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']

        request = ChatRequest(
            message=body.get('message', ''),
            conversation_history=body.get('conversation_history', []),
            session_id=body.get('session_id')
        )

        if not request.message:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json'
                },
                'body': json.dumps({
                    'error': 'Message is required'
                })
            }

        # Process chat
        response = process_chat(request)

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': json.dumps({
                'response': response.response,
                'metadata': response.metadata,
                'error': response.error
            })
        }

    except Exception as e:
        logger.error(f"Handler error: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': json.dumps({
                'error': f"Internal error: {str(e)}",
                'response': "I apologize, but I encountered an error processing your request. Please try again.",
                'metadata': {
                    'tools_used': [],
                    'confidence': 0,
                    'validation_status': 'ERROR'
                }
            })
        }


# =============================================================================
# LOCAL TESTING
# =============================================================================

def main():
    """Run local test."""
    test_messages = [
        "Show me top 5 restaurants by followers",
        "How many posts are in the database?",
        "Find restaurants with outdoor seating",
        "help"
    ]

    for msg in test_messages:
        print(f"\n{'='*60}")
        print(f"User: {msg}")
        print(f"{'='*60}")

        request = ChatRequest(message=msg, conversation_history=[])
        response = process_chat(request)

        print(f"\nAssistant: {response.response}")
        print(f"\nMetadata: {response.metadata}")


if __name__ == '__main__':
    main()
