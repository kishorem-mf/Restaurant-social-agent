"""
Orchestrator Lambda Handler

Main entry point for the custom agent orchestrator.
Uses LLM-driven tool calling for intelligent query routing.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from llm_orchestrator import LLMOrchestrator

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
# MAIN ORCHESTRATION
# =============================================================================

def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Main chat processing function.
    Uses LLM-driven orchestration for tool selection and response generation.
    """
    orchestrator = LLMOrchestrator()

    result = orchestrator.orchestrate(
        user_message=request.message,
        conversation_history=request.conversation_history
    )

    return ChatResponse(
        response=result.response,
        metadata=result.metadata,
        error=result.error
    )


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
        "help",
        "Top posts based on likes",
        "What are the top hashtags by engagement?",
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
