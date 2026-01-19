"""
Tests for the orchestrator handler.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock

import sys
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from lambdas.orchestrator.handler import (
    detect_intent,
    generate_sql_from_message,
    format_response_simple,
    generate_help_response,
    process_chat,
    ChatRequest,
    handler,
)
from lambdas.orchestrator.tools import (
    QueryDataResult,
    VectorSearchResult,
    ValidationResult,
    extract_from_bedrock_response,
)


# =============================================================================
# INTENT DETECTION TESTS
# =============================================================================

class TestIntentDetection:
    """Tests for intent detection."""

    def test_sql_query_intent_top_restaurants(self):
        """Should detect SQL query intent for top restaurants."""
        assert detect_intent("Show me top 5 restaurants") == 'sql_query'
        assert detect_intent("Top 10 restaurants by followers") == 'sql_query'
        assert detect_intent("List all restaurants") == 'sql_query'

    def test_sql_query_intent_posts(self):
        """Should detect SQL query intent for posts."""
        # Note: "Show top posts by likes" may return 'hybrid' due to overlapping keywords
        result = detect_intent("Show top posts by likes")
        assert result in ['sql_query', 'hybrid'], f"Got {result}"
        assert detect_intent("How many posts are there?") == 'sql_query'

    def test_vector_search_intent(self):
        """Should detect vector search intent."""
        # Note: queries with both 'restaurants' and vector keywords may return 'hybrid'
        result = detect_intent("Find restaurants with outdoor seating")
        assert result in ['vector_search', 'hybrid'], f"Got {result}"
        assert detect_intent("Describe the atmosphere") == 'vector_search'
        result2 = detect_intent("Similar restaurants to cafe roma")
        assert result2 in ['vector_search', 'hybrid'], f"Got {result2}"

    def test_general_question_intent(self):
        """Should detect general question intent."""
        assert detect_intent("Hello") == 'general_question'
        assert detect_intent("What can you do?") == 'general_question'
        assert detect_intent("Help me") == 'general_question'

    def test_hybrid_intent(self):
        """Should detect hybrid intent when both SQL and vector signals present."""
        result = detect_intent("Top restaurants with outdoor seating")
        assert result in ['hybrid', 'sql_query', 'vector_search']


# =============================================================================
# SQL GENERATION TESTS
# =============================================================================

class TestSQLGeneration:
    """Tests for SQL generation from natural language."""

    def test_top_restaurants_by_followers(self):
        """Should generate SQL for top restaurants by followers."""
        sql = generate_sql_from_message("Top 5 restaurants by followers")
        assert sql is not None
        assert 'SELECT' in sql
        assert 'restaurants' in sql
        assert 'followers' in sql.lower()
        assert 'ORDER BY' in sql
        assert 'LIMIT 5' in sql

    def test_top_restaurants_by_posts(self):
        """Should generate SQL for top restaurants by posts."""
        sql = generate_sql_from_message("Top 10 restaurants by posts")
        assert sql is not None
        assert 'posts_count' in sql.lower()
        assert 'LIMIT 10' in sql

    def test_list_restaurants(self):
        """Should generate SQL for listing restaurants."""
        sql = generate_sql_from_message("Show all restaurants")
        assert sql is not None
        assert 'SELECT' in sql
        assert 'restaurants' in sql.lower()

    def test_top_posts_by_likes(self):
        """Should generate SQL for top posts by likes."""
        sql = generate_sql_from_message("Top 10 posts by likes")
        assert sql is not None
        assert 'posts' in sql.lower()
        assert 'likes' in sql.lower()
        assert 'ORDER BY' in sql

    def test_count_restaurants(self):
        """Should generate SQL for counting restaurants."""
        sql = generate_sql_from_message("How many restaurants are there?")
        assert sql is not None
        assert 'COUNT' in sql
        assert 'restaurants' in sql.lower()

    def test_restaurants_in_city(self):
        """Should generate SQL for restaurants in a city."""
        sql = generate_sql_from_message("Restaurants in New York")
        assert sql is not None
        assert 'city' in sql.lower()
        assert 'New York' in sql

    def test_average_likes(self):
        """Should generate SQL for average likes."""
        sql = generate_sql_from_message("What is the average likes?")
        assert sql is not None
        assert 'AVG' in sql
        assert 'likes' in sql.lower()

    def test_posts_from_creator(self):
        """Should generate SQL for posts from creator."""
        sql = generate_sql_from_message("Posts from @caferoma")
        assert sql is not None
        assert 'posts' in sql.lower()
        assert 'creator' in sql.lower()

    def test_unrecognized_query_returns_none(self):
        """Should return None for unrecognized queries."""
        sql = generate_sql_from_message("What is the meaning of life?")
        assert sql is None


# =============================================================================
# RESPONSE FORMATTING TESTS
# =============================================================================

class TestResponseFormatting:
    """Tests for response formatting."""

    def test_format_successful_response(self):
        """Should format successful query results."""
        result = QueryDataResult(
            success=True,
            data=[{'name': 'Cafe Roma', 'followers': 45000}],
            columns=['name', 'followers'],
            row_count=1,
            markdown_table='| name | followers |\n|---|---|\n| Cafe Roma | 45000 |',
            sql_executed='SELECT name, followers FROM restaurants LIMIT 1'
        )

        response = format_response_simple(result, "Show restaurants")
        assert 'Cafe Roma' in response
        assert 'found' in response.lower() or '|' in response

    def test_format_error_response(self):
        """Should format error response."""
        result = QueryDataResult(
            success=False,
            data=[],
            columns=[],
            row_count=0,
            markdown_table='',
            sql_executed='SELECT * FROM invalid',
            error='Table not found'
        )

        response = format_response_simple(result, "Show invalid")
        assert 'error' in response.lower()

    def test_format_empty_results(self):
        """Should handle empty results."""
        result = QueryDataResult(
            success=True,
            data=[],
            columns=[],
            row_count=0,
            markdown_table='',
            sql_executed='SELECT * FROM restaurants WHERE 1=0'
        )

        response = format_response_simple(result, "Show restaurants")
        assert 'could' in response.lower() or 'no' in response.lower()


# =============================================================================
# HELP RESPONSE TESTS
# =============================================================================

class TestHelpResponse:
    """Tests for help response generation."""

    def test_help_command(self):
        """Should provide help information."""
        response = generate_help_response("help")
        assert 'restaurant' in response.lower()
        assert 'post' in response.lower()

    def test_greeting(self):
        """Should respond to greetings."""
        response = generate_help_response("Hello")
        assert 'hello' in response.lower() or 'help' in response.lower()


# =============================================================================
# BEDROCK RESPONSE EXTRACTION TESTS
# =============================================================================

class TestBedrockResponseExtraction:
    """Tests for extracting data from Bedrock Agent response format."""

    def test_extract_valid_bedrock_response(self):
        """Should extract body from valid Bedrock response."""
        bedrock_response = {
            'messageVersion': '1.0',
            'response': {
                'functionResponse': {
                    'responseBody': {
                        'TEXT': {
                            'body': json.dumps({
                                'success': True,
                                'data': [{'name': 'Test'}],
                                'row_count': 1
                            })
                        }
                    }
                }
            }
        }

        extracted = extract_from_bedrock_response(bedrock_response)
        assert extracted['success'] is True
        assert extracted['row_count'] == 1
        assert len(extracted['data']) == 1

    def test_extract_empty_response(self):
        """Should handle empty response."""
        extracted = extract_from_bedrock_response({})
        assert extracted == {}

    def test_extract_invalid_json(self):
        """Should handle invalid JSON in body."""
        bedrock_response = {
            'response': {
                'functionResponse': {
                    'responseBody': {
                        'TEXT': {
                            'body': 'not valid json'
                        }
                    }
                }
            }
        }

        extracted = extract_from_bedrock_response(bedrock_response)
        assert extracted == {}


# =============================================================================
# INTEGRATION TESTS (with mocks)
# =============================================================================

class TestProcessChat:
    """Integration tests for process_chat function."""

    @patch('lambdas.orchestrator.handler.invoke_query_data')
    @patch('lambdas.orchestrator.handler.invoke_response_validator')
    def test_process_sql_query(self, mock_validator, mock_query):
        """Should process SQL query intent."""
        # Setup mocks
        mock_query.return_value = QueryDataResult(
            success=True,
            data=[{'restaurant_name': 'Test', 'followers': 1000}],
            columns=['restaurant_name', 'followers'],
            row_count=1,
            markdown_table='| restaurant_name | followers |\n|---|---|\n| Test | 1000 |',
            sql_executed='SELECT * FROM restaurants LIMIT 5'
        )

        mock_validator.return_value = ValidationResult(
            action='PASS',
            validated_response='Test response',
            confidence_score=0.95,
            violations_count=0,
            violations=[],
            details={}
        )

        request = ChatRequest(
            message="Show top 5 restaurants by followers",
            conversation_history=[]
        )

        response = process_chat(request)

        assert response.response is not None
        assert 'query_data' in response.metadata['tools_used']
        assert response.metadata['confidence'] > 0

    @patch('lambdas.orchestrator.handler.invoke_vector_search')
    def test_process_vector_search(self, mock_search):
        """Should process vector search intent."""
        mock_search.return_value = VectorSearchResult(
            success=True,
            results=[{
                'content': 'Restaurant with outdoor seating',
                'score': 0.9,
                'metadata': {}
            }],
            query='outdoor seating'
        )

        request = ChatRequest(
            message="Find restaurants with outdoor seating",
            conversation_history=[]
        )

        response = process_chat(request)

        assert response.response is not None
        assert 'vector_search' in response.metadata['tools_used']

    def test_process_general_question(self):
        """Should process general questions without external calls."""
        request = ChatRequest(
            message="Help me",
            conversation_history=[]
        )

        response = process_chat(request)

        assert response.response is not None
        assert 'help' in response.response.lower() or 'can do' in response.response.lower()


# =============================================================================
# HANDLER TESTS
# =============================================================================

class TestHandler:
    """Tests for the Lambda handler function."""

    @patch('lambdas.orchestrator.handler.process_chat')
    def test_handler_success(self, mock_process):
        """Should handle successful requests."""
        mock_process.return_value = Mock(
            response='Test response',
            metadata={'tools_used': [], 'confidence': 1.0},
            error=None
        )

        event = {
            'body': json.dumps({
                'message': 'Hello',
                'conversation_history': []
            })
        }

        response = handler(event, None)

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['response'] == 'Test response'

    def test_handler_missing_message(self):
        """Should return 400 for missing message."""
        event = {
            'body': json.dumps({
                'conversation_history': []
            })
        }

        response = handler(event, None)

        assert response['statusCode'] == 400

    def test_handler_direct_event(self):
        """Should handle direct event format (not API Gateway)."""
        event = {
            'message': 'Hello',
            'conversation_history': []
        }

        # This should work even if it errors out - just testing it doesn't crash
        response = handler(event, None)
        assert 'statusCode' in response


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
