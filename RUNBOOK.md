# Instagram Analytics Chat - Quick Start

## Prerequisites
- Python 3.11+
- AWS credentials configured (for live mode)

## Start the Application
```bash
cd lambdas/orchestrator
pip install flask flask-cors boto3 openai
python server.py
```

## Access the Chat
Open http://localhost:8000 in your browser

## Test Queries
- "top 5 restaurants by followers"
- "find restaurants with outdoor seating"
- "help"

## Modes
- **Live API**: Queries AWS Lambda (requires credentials)
- **Mock Mode**: Check "Mock Mode" checkbox for offline testing
