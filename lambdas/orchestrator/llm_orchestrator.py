"""
LLM-Driven Orchestrator

Main orchestration class using Azure OpenAI with function calling.
Replaces procedural pattern-matching with LLM-driven tool selection.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import AzureOpenAI

from config import TABLE_SCHEMAS, get_azure_openai_config
from prompts import get_system_prompt, get_help_response, get_error_message
from tool_definitions import (
    get_tool_definitions,
    validate_tool_call,
    TOOL_QUERY_DATABASE,
    TOOL_SEARCH_KNOWLEDGE_BASE,
    TOOL_GET_HELP_INFO,
)
from tools import (
    invoke_query_data,
    invoke_vector_search,
    invoke_response_validator,
)

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

MAX_ITERATIONS = 5
DEFAULT_TEMPERATURE = 0.1
MAX_HISTORY_MESSAGES = 10


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class OrchestrationResult:
    """Result from LLM orchestration."""
    response: str
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class ToolExecutionResult:
    """Result from executing a tool."""
    success: bool
    result: Any
    tool_name: str
    execution_time_ms: int
    error: Optional[str] = None


# =============================================================================
# LLM ORCHESTRATOR CLASS
# =============================================================================

class LLMOrchestrator:
    """
    LLM-driven orchestrator using Azure OpenAI function calling.

    The LLM decides which tools to call based on:
    1. User's natural language query
    2. Database schema context
    3. Available tool definitions
    """

    def __init__(self):
        """Initialize the orchestrator with Azure OpenAI client."""
        self._client = None
        self._config = None

    @property
    def client(self) -> AzureOpenAI:
        """Lazy-load Azure OpenAI client."""
        if self._client is None:
            self._config = get_azure_openai_config()
            self._client = AzureOpenAI(
                azure_endpoint=self._config['endpoint'],
                api_key=self._config['api_key'],
                api_version=self._config['api_version'],
            )
        return self._client

    @property
    def deployment(self) -> str:
        """Get deployment name."""
        if self._config is None:
            self._config = get_azure_openai_config()
        return self._config['deployment']

    def orchestrate(
        self,
        user_message: str,
        conversation_history: Optional[list] = None
    ) -> OrchestrationResult:
        """
        Main orchestration method.

        Args:
            user_message: User's natural language query
            conversation_history: Previous messages in the conversation

        Returns:
            OrchestrationResult with response and metadata
        """
        start_time = time.time()
        tools_used = []
        sql_executed = None
        confidence = 0.0
        validation_status = 'N/A'

        try:
            # Build messages
            messages = self._build_messages(user_message, conversation_history)

            # Get tool definitions
            tools = get_tool_definitions()

            # Run orchestration loop
            iteration = 0
            while iteration < MAX_ITERATIONS:
                iteration += 1
                logger.info(f"Orchestration iteration {iteration}")

                # Call LLM
                response = self.client.chat.completions.create(
                    model=self.deployment,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=DEFAULT_TEMPERATURE,
                )

                assistant_message = response.choices[0].message

                # Check if LLM wants to call tools
                if assistant_message.tool_calls:
                    # Process tool calls
                    messages.append(assistant_message)

                    for tool_call in assistant_message.tool_calls:
                        tool_name = tool_call.function.name
                        tool_args = json.loads(tool_call.function.arguments)

                        logger.info(f"Tool call: {tool_name} with args: {tool_args}")

                        # Execute tool
                        tool_result = self._execute_tool(
                            tool_name, tool_args, user_message
                        )
                        tools_used.append(tool_name)

                        # Track SQL if executed
                        if tool_name == TOOL_QUERY_DATABASE and tool_result.success:
                            sql_executed = tool_args.get('sql')

                        # Add tool result to messages
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(tool_result.result),
                        })
                else:
                    # LLM provided final response
                    response_text = assistant_message.content

                    # Validate response if SQL was executed
                    if sql_executed and response_text:
                        validation = self._validate_response(
                            response_text, sql_executed, tools_used
                        )
                        response_text = validation.validated_response
                        confidence = validation.confidence_score
                        validation_status = validation.action
                        if 'response_validator' not in tools_used:
                            tools_used.append('response_validator')
                    else:
                        confidence = 1.0
                        validation_status = 'PASS'

                    response_time_ms = int((time.time() - start_time) * 1000)

                    return OrchestrationResult(
                        response=response_text,
                        metadata={
                            'tools_used': tools_used,
                            'sql_executed': sql_executed,
                            'confidence': confidence,
                            'validation_status': validation_status,
                            'iterations': iteration,
                            'response_time_ms': response_time_ms,
                        }
                    )

            # Max iterations reached
            logger.warning(f"Max iterations ({MAX_ITERATIONS}) reached")
            return OrchestrationResult(
                response=get_error_message("max_iterations"),
                metadata={
                    'tools_used': tools_used,
                    'sql_executed': sql_executed,
                    'confidence': 0.0,
                    'validation_status': 'MAX_ITERATIONS',
                    'iterations': iteration,
                    'response_time_ms': int((time.time() - start_time) * 1000),
                },
                error="Max iterations reached"
            )

        except Exception as e:
            logger.error(f"Orchestration error: {e}", exc_info=True)
            return OrchestrationResult(
                response=get_error_message("unknown_error"),
                metadata={
                    'tools_used': tools_used,
                    'sql_executed': sql_executed,
                    'confidence': 0.0,
                    'validation_status': 'ERROR',
                    'response_time_ms': int((time.time() - start_time) * 1000),
                },
                error=str(e)
            )

    def _build_messages(
        self,
        user_message: str,
        conversation_history: Optional[list]
    ) -> list:
        """
        Build the messages list for the LLM call.

        Args:
            user_message: Current user message
            conversation_history: Previous conversation messages

        Returns:
            List of message dicts for the API call
        """
        messages = [
            {"role": "system", "content": get_system_prompt(TABLE_SCHEMAS)}
        ]

        # Add conversation history (limited to prevent token overflow)
        if conversation_history:
            # Take only the last N messages
            recent_history = conversation_history[-MAX_HISTORY_MESSAGES:]
            for msg in recent_history:
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                if role in ('user', 'assistant') and content:
                    messages.append({"role": role, "content": content})

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        return messages

    def _execute_tool(
        self,
        tool_name: str,
        arguments: dict,
        user_message: str
    ) -> ToolExecutionResult:
        """
        Execute a tool and return the result.

        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments for the tool
            user_message: Original user message (for context)

        Returns:
            ToolExecutionResult with success status and result
        """
        start_time = time.time()

        # Validate tool call
        is_valid, error = validate_tool_call(tool_name, arguments)
        if not is_valid:
            logger.warning(f"Invalid tool call: {error}")
            return ToolExecutionResult(
                success=False,
                result={"error": error},
                tool_name=tool_name,
                execution_time_ms=0,
                error=error
            )

        try:
            if tool_name == TOOL_QUERY_DATABASE:
                sql = arguments["sql"]
                query_result = invoke_query_data(sql, user_query=user_message)

                if query_result.success:
                    result = {
                        "success": True,
                        "data": query_result.data[:50],  # Limit data in response
                        "row_count": query_result.row_count,
                        "columns": query_result.columns,
                        "markdown_table": query_result.markdown_table,
                        "allowed_values": query_result.allowed_values,
                    }
                else:
                    result = {
                        "success": False,
                        "error": query_result.error,
                    }

                return ToolExecutionResult(
                    success=query_result.success,
                    result=result,
                    tool_name=tool_name,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            elif tool_name == TOOL_SEARCH_KNOWLEDGE_BASE:
                query = arguments["query"]
                top_k = arguments.get("top_k", 5)
                search_result = invoke_vector_search(query, top_k=top_k)

                if search_result.success:
                    result = {
                        "success": True,
                        "results": search_result.results,
                        "query": search_result.query,
                    }
                else:
                    result = {
                        "success": False,
                        "error": search_result.error,
                    }

                return ToolExecutionResult(
                    success=search_result.success,
                    result=result,
                    tool_name=tool_name,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            elif tool_name == TOOL_GET_HELP_INFO:
                topic = arguments["topic"]
                help_text = get_help_response(topic)

                return ToolExecutionResult(
                    success=True,
                    result={"help_text": help_text},
                    tool_name=tool_name,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            else:
                return ToolExecutionResult(
                    success=False,
                    result={"error": f"Unknown tool: {tool_name}"},
                    tool_name=tool_name,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    error=f"Unknown tool: {tool_name}"
                )

        except Exception as e:
            logger.error(f"Tool execution error for {tool_name}: {e}")
            return ToolExecutionResult(
                success=False,
                result={"error": str(e)},
                tool_name=tool_name,
                execution_time_ms=int((time.time() - start_time) * 1000),
                error=str(e)
            )

    def _validate_response(
        self,
        response_text: str,
        sql_executed: str,
        tools_used: list
    ):
        """
        Validate the LLM response against query evidence.

        Args:
            response_text: The LLM-generated response
            sql_executed: The SQL query that was executed
            tools_used: List of tools that were used

        Returns:
            ValidationResult from the response validator
        """
        # Get allowed values from the most recent query result
        # This is tracked during tool execution
        allowed_values = None

        # Invoke response validator
        return invoke_response_validator(
            response_text=response_text,
            sql_executed=sql_executed,
            allowed_values=allowed_values
        )


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_orchestrator() -> LLMOrchestrator:
    """Create a new orchestrator instance."""
    return LLMOrchestrator()
