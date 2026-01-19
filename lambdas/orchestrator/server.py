"""
Local Development Server

Simple Flask server for testing the orchestrator locally.
Serves the chat UI and provides the /chat API endpoint.
"""

import json
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from flask import Flask, request, jsonify, send_from_directory
    from flask_cors import CORS
except ImportError:
    print("Please install Flask and flask-cors:")
    print("  pip install flask flask-cors")
    sys.exit(1)

# Import orchestrator (with fallback for missing AWS deps)
try:
    from lambdas.orchestrator.handler import process_chat, ChatRequest
    ORCHESTRATOR_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import orchestrator: {e}")
    print("Running in mock mode only.")
    ORCHESTRATOR_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
UI_PATH = PROJECT_ROOT / 'ui'


# =============================================================================
# MOCK MODE (for testing without AWS)
# =============================================================================

MOCK_RESPONSES = {
    'top restaurants': {
        'response': """Here are the top 5 restaurants by followers:

| Rank | Restaurant | Handle | Followers | Posts |
|------|------------|--------|-----------|-------|
| 1 | Cafe Roma | @caferoma | 45.2K | 234 |
| 2 | The Garden Bistro | @gardenbistro | 38.7K | 187 |
| 3 | Ocean View | @oceanviewdining | 32.1K | 156 |
| 4 | Sunset Grill | @sunsetgrill | 28.5K | 203 |
| 5 | Mountain Kitchen | @mountainkitchen | 24.8K | 145 |

These restaurants have the highest Instagram following in our database.""",
        'metadata': {
            'tools_used': ['query_data', 'response_validator'],
            'sql_executed': 'SELECT restaurant_name, instagram_handle, followers, posts_count FROM restaurants ORDER BY followers DESC LIMIT 5',
            'confidence': 0.95,
            'validation_status': 'PASS',
            'intent': 'sql_query'
        }
    },
    'posts': {
        'response': """Here are the top posts by engagement:

| Post | Creator | Likes | Comments | Date |
|------|---------|-------|----------|------|
| "Amazing sunset dinner" | @caferoma | 2.3K | 156 | 2024-01-15 |
| "New menu alert!" | @gardenbistro | 1.9K | 203 | 2024-01-14 |
| "Brunch vibes" | @oceanviewdining | 1.7K | 98 | 2024-01-13 |

Posts with food photography and natural lighting tend to perform best.""",
        'metadata': {
            'tools_used': ['query_data', 'response_validator'],
            'sql_executed': "SELECT caption, creator, likes, comments FROM posts ORDER BY likes DESC LIMIT 5",
            'confidence': 0.92,
            'validation_status': 'PASS',
            'intent': 'sql_query'
        }
    },
    'search': {
        'response': """Based on my search, here are restaurants that match:

| Restaurant | Bio Excerpt | Followers |
|------------|-------------|-----------|
| Garden Bistro | "Beautiful outdoor patio..." | 38.7K |
| Ocean View | "Waterfront dining with terrace..." | 32.1K |
| Sunset Grill | "Rooftop seating available..." | 28.5K |

*Note: This search uses semantic matching from restaurant bios and descriptions.*""",
        'metadata': {
            'tools_used': ['vector_search'],
            'sql_executed': None,
            'confidence': 0.88,
            'validation_status': 'PASS',
            'intent': 'vector_search'
        }
    },
    'help': {
        'response': """I can help you analyze Instagram data for restaurants. Here's what I can do:

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

Just ask me a question and I'll help you find the data!""",
        'metadata': {
            'tools_used': [],
            'sql_executed': None,
            'confidence': 1.0,
            'validation_status': 'PASS',
            'intent': 'general_question'
        }
    }
}


def get_mock_response(message: str) -> dict:
    """Get mock response based on message content."""
    message_lower = message.lower()

    if 'restaurant' in message_lower and ('top' in message_lower or 'show' in message_lower or 'list' in message_lower):
        return MOCK_RESPONSES['top restaurants']
    elif 'post' in message_lower or 'like' in message_lower or 'engagement' in message_lower:
        return MOCK_RESPONSES['posts']
    elif 'outdoor' in message_lower or 'search' in message_lower or 'find' in message_lower:
        return MOCK_RESPONSES['search']
    elif 'help' in message_lower or 'hello' in message_lower or 'hi' in message_lower:
        return MOCK_RESPONSES['help']
    else:
        return {
            'response': f"I understand you're asking about: {message}\n\nCould you please be more specific? Try asking about top restaurants, popular posts, or search for specific features.",
            'metadata': {
                'tools_used': [],
                'sql_executed': None,
                'confidence': 0.7,
                'validation_status': 'PASS',
                'intent': 'general_question'
            }
        }


# =============================================================================
# ROUTES
# =============================================================================

@app.route('/')
def index():
    """Serve the chat UI."""
    return send_from_directory(UI_PATH, 'chat-test.html')


@app.route('/chat', methods=['POST', 'OPTIONS'])
def chat():
    """Handle chat requests."""
    if request.method == 'OPTIONS':
        return '', 200

    try:
        data = request.get_json()
        message = data.get('message', '')
        conversation_history = data.get('conversation_history', [])
        session_id = data.get('session_id')
        use_mock = data.get('mock', False)  # Default to LIVE mode

        if not message:
            return jsonify({'error': 'Message is required'}), 400

        logger.info(f"Received message: {message[:50]}...")

        # Use mock mode or real orchestrator
        if use_mock or not ORCHESTRATOR_AVAILABLE:
            result = get_mock_response(message)
            return jsonify(result)
        else:
            # Use real orchestrator
            chat_request = ChatRequest(
                message=message,
                conversation_history=conversation_history,
                session_id=session_id
            )
            response = process_chat(chat_request)
            return jsonify({
                'response': response.response,
                'metadata': response.metadata,
                'error': response.error
            })

    except Exception as e:
        logger.error(f"Error processing chat: {e}", exc_info=True)
        return jsonify({
            'error': str(e),
            'response': 'Sorry, an error occurred. Please try again.',
            'metadata': {
                'tools_used': [],
                'confidence': 0,
                'validation_status': 'ERROR'
            }
        }), 500


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'orchestrator_available': ORCHESTRATOR_AVAILABLE
    })


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    debug = os.environ.get('DEBUG', 'true').lower() == 'true'

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║          Instagram Analytics Chat - Local Server             ║
╠══════════════════════════════════════════════════════════════╣
║  Chat UI:    http://localhost:{port}/                         ║
║  API:        http://localhost:{port}/chat                     ║
║  Health:     http://localhost:{port}/health                   ║
║                                                              ║
║  Orchestrator: {'Available' if ORCHESTRATOR_AVAILABLE else 'Mock Mode Only'}                             ║
╚══════════════════════════════════════════════════════════════╝
    """)

    app.run(host='0.0.0.0', port=port, debug=debug)
