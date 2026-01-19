 Custom Agent Architecture Plan

## High-Level Overview

1. **API Gateway** → Single REST endpoint `/chat` receives user queries
2. **Orchestrator Lambda** → LangGraph-based orchestration, coordinates all agents
3. **Azure OpenAI** → LLM for intent detection, SQL generation, response formatting
4. **LangGraph** → Framework for tools, guardrails, pre/post processing, multi-agent
5. **S3 Data Lake** → Delta tables store posts/restaurants data (already exists)
6. **Stateless Design** → Client passes conversation_history in each request
7. **Response Flow** → Query → Intent → Tool Selection → Execute → Validate → Return
8. **Bedrock Knowledge Base** → Vector search via existing KB (ID: `QQJTQJ1VWU`)
9. **No Bedrock Agent** → Custom orchestration with full control over prompts and validation
10. Required Iceberg tables posts and restarants already exist.
---

# Implementation Phases

## Phase 1: Foundation (Start Small)

### 1.1 Define Input/Output JSON Contracts

**Chat Request Schema:**
```json
{
  "message": "string (required) - User's question",
  "conversation_history": [
    {
      "role": "user | assistant",
      "content": "string"
    }
  ],
  "session_id": "string (optional) - For tracking"
}
```

**Chat Response Schema:**
```json
{
  "response": "string - Markdown formatted answer",
  "metadata": {
    "tools_used": ["string array - Tools invoked"],
    "sql_executed": "string | null - SQL query if any",
    "confidence": "number 0-1",
    "validation_status": "PASS | SANITIZE | BLOCK"
  },
  "error": "string | null - Error message if failed"
}
```

**Tool Input/Output Contracts:**

| Tool | Input | Output |
|------|-------|--------|
| `query_data` | `{ "sql": "string" }` | `{ "data": [], "columns": [], "row_count": int }` |
| `vector_search` | `{ "query": "string", "top_k": int }` | `{ "results": [{ "content": "", "score": float }] }` |
| `response_validator` | `{ "response_text": "string", "sql_executed": "string" }` | `{ "action": "PASS|BLOCK", "validated_response": "string" }` |

---

### 1.2 Validate Existing Lambdas

#### Current State Analysis

**Issue:** Lambdas currently use **Bedrock Agent format** (nested structure). Need wrapper or refactor for direct invocation.

---

**`query_data` Lambda** - ✅ Core logic works, needs format adapter

Current Input (Bedrock format):
```json
{
  "function": "execute_sql",
  "parameters": [{"name": "sql", "value": "SELECT ..."}]
}
```

Current Output (Bedrock format):
```json
{
  "messageVersion": "1.0",
  "response": {
    "functionResponse": {
      "responseBody": {
        "TEXT": {
          "body": {
            "success": true,
            "data": [...],
            "columns": [...],
            "row_count": 5,
            "markdown_table": "...",
            "evidence_metadata": {...}
          }
        }
      }
    }
  }
}
```

**Target Output (Direct format):**
```json
{
  "success": true,
  "data": [...],
  "columns": [...],
  "row_count": 5,
  "markdown_table": "...",
  "sql_executed": "..."
}
```

| Check | Status | Notes |
|-------|--------|-------|
| Returns data, columns, row_count | ✅ Yes | Nested in Bedrock format |
| SQL validation | ✅ Yes | Blocks DROP, DELETE, etc. |
| Error handling | ✅ Yes | Returns error details |
| **Needs:** Format adapter | ⬜ TODO | Extract from nested structure |

---

**`vector_search` Lambda** - ✅ Uses Bedrock Knowledge Base (MUST HAVE)

**Existing Setup:** Bedrock Knowledge Base (ID: `QQJTQJ1VWU`) with `bedrock_test` lambda wrapper.

**For Custom Agent:** Create wrapper that calls Bedrock KB directly via AWS SDK.

**Target Input (Direct format):**
```json
{
  "query": "restaurants with outdoor seating",
  "top_k": 5
}
```

**Target Output (Direct format):**
```json
{
  "results": [
    {
      "content": "Cafe Roma offers beautiful outdoor seating...",
      "score": 0.92,
      "metadata": {"source": "restaurant_profiles"}
    }
  ],
  "query": "restaurants with outdoor seating"
}
```

**Implementation:** Use `boto3` Bedrock Agent Runtime API:
```python
# In orchestrator/tools.py
import boto3

bedrock_agent = boto3.client('bedrock-agent-runtime')
KNOWLEDGE_BASE_ID = "QQJTQJ1VWU"

def vector_search(query: str, top_k: int = 5) -> dict:
    response = bedrock_agent.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={'text': query},
        retrievalConfiguration={
            'vectorSearchConfiguration': {'numberOfResults': top_k}
        }
    )
    return {
        'results': [
            {
                'content': r['content']['text'],
                'score': r['score'],
                'metadata': r.get('metadata', {})
            }
            for r in response['retrievalResults']
        ],
        'query': query
    }
```

| Check | Status | Notes |
|-------|--------|-------|
| Knowledge Base exists | ✅ Yes | ID: `QQJTQJ1VWU` |
| Wrapper lambda exists | ✅ Yes | `bedrock_test` |
| Direct SDK access | ⬜ TODO | Add to orchestrator tools |

---

**`response_validator` Lambda** - ✅ Core logic works, needs format adapter

Current Input (Bedrock format):
```json
{
  "function": "validate_response",
  "parameters": [
    {"name": "response_text", "value": "..."},
    {"name": "sql_executed", "value": "SELECT ..."}
  ]
}
```

Current Output (Bedrock format):
```json
{
  "messageVersion": "1.0",
  "response": {
    "functionResponse": {
      "responseBody": {
        "TEXT": {
          "body": {
            "action": "PASS|SANITIZE|BLOCK",
            "validated_response": "...",
            "confidence_score": 0.95,
            "violations_count": 0
          }
        }
      }
    }
  }
}
```

**Target Output (Direct format):**
```json
{
  "action": "PASS",
  "validated_response": "...",
  "confidence_score": 0.95,
  "violations": []
}
```

| Check | Status | Notes |
|-------|--------|-------|
| Accepts response_text + sql_executed | ✅ Yes | As parameters array |
| Returns action + validated_response | ✅ Yes | Nested in Bedrock format |
| Independent SQL re-execution | ✅ Yes | Built-in anti-hallucination |
| **Needs:** Format adapter | ⬜ TODO | Extract from nested structure |

---

#### Adapter Strategy

**Option A: Modify existing lambdas** (add dual-mode support)
```python
def handler(event, context):
    # Detect format
    if 'function' in event and 'parameters' in event:
        # Bedrock Agent format
        return bedrock_response(result)
    else:
        # Direct invocation format
        return direct_response(result)
```

**Option B: Create wrapper functions** (recommended - no changes to existing)
```python
# In orchestrator/tools.py
def invoke_query_data(sql: str) -> dict:
    response = lambda_client.invoke('query_data', {
        'function': 'execute_sql',
        'parameters': [{'name': 'sql', 'value': sql}]
    })
    # Extract from nested Bedrock format
    body = response['response']['functionResponse']['responseBody']['TEXT']['body']
    return body
```

---

#### Test Commands

```bash
# Test query_data (Bedrock format)
aws lambda invoke --function-name instagram-query-data \
  --payload '{"function":"execute_sql","parameters":[{"name":"sql","value":"SELECT * FROM posts LIMIT 3"}]}' \
  /tmp/out.json && cat /tmp/out.json | jq '.response.functionResponse.responseBody.TEXT.body'

# Test response_validator (Bedrock format)
aws lambda invoke --function-name instagram-response-validator \
  --payload '{"function":"validate_response","parameters":[{"name":"response_text","value":"Test response"},{"name":"sql_executed","value":"SELECT * FROM posts LIMIT 1"}]}' \
  /tmp/out.json && cat /tmp/out.json | jq '.response.functionResponse.responseBody.TEXT.body'

# Test Bedrock Knowledge Base (direct SDK call)
aws bedrock-agent-runtime retrieve \
  --knowledge-base-id QQJTQJ1VWU \
  --retrieval-query '{"text": "restaurants with outdoor seating"}' \
  --retrieval-configuration '{"vectorSearchConfiguration": {"numberOfResults": 3}}'
```

---

### 1.3 Simple Chat UI (Testing Interface)

**Location:** `ui/chat-test.html` (single file, no build required)

**Features:**
- Text input for messages
- Conversation history display (bubbles)
- Raw JSON request/response viewer (collapsible)
- Tool usage visualization (which tools were called)
- Metadata display (confidence, SQL executed)
- Local storage for conversation persistence
- Clear conversation button

**Tech:** Vanilla HTML/CSS/JS + Tailwind CDN (no build required)

**UI Mockup:**
```
┌─────────────────────────────────────────────────┐
│  Instagram Analytics Chat (Test UI)             │
├─────────────────────────────────────────────────┤
│                                                 │
│  [User] Show top 5 restaurants by followers     │
│                                                 │
│  [Assistant] Here are the top 5 restaurants...  │
│  | Rank | Restaurant | Followers |              │
│  |------|------------|-----------|              │
│  | 1    | Cafe Roma  | 45,000    |              │
│                                                 │
│  ▼ Metadata                                     │
│  ┌─────────────────────────────────────────┐   │
│  │ Tools: query_data, response_validator   │   │
│  │ Confidence: 0.95                        │   │
│  │ SQL: SELECT ... ORDER BY followers ...  │   │
│  └─────────────────────────────────────────┘   │
│                                                 │
├─────────────────────────────────────────────────┤
│  [Type your message...]              [Send]     │
├─────────────────────────────────────────────────┤
│  [Clear] [Show Raw JSON]                        │
└─────────────────────────────────────────────────┘
```

**API Connection:**
- Points to local orchestrator (Phase 2) or mock server
- Shows loading state during API calls
- Displays errors gracefully

---

## Phase 1 Implementation Order

1. **Create Chat UI** (`ui/chat-test.html`)
   - Build static UI with mock responses first
   - Add API integration hooks (commented out)

2. **Test existing services** (via AWS CLI)
   - Verify query_data lambda works with current format
   - Verify response_validator lambda works
   - Verify Bedrock Knowledge Base retrieval works (ID: `QQJTQJ1VWU`)

3. **Create orchestrator skeleton** (`lambdas/orchestrator/`)
   - Basic handler with hardcoded response
   - Add tool wrapper functions (extract from Bedrock format)
   - Test locally with `python -m pytest`

4. **Connect UI to orchestrator**
   - Run orchestrator locally (or deploy to Lambda)
   - Point UI to endpoint
   - End-to-end test


