"""Chat prototype router for Editorial Assistant v3.0 API.

Provides a simple REST-based chat endpoint for validating the embedded chat pattern.
This is a minimal implementation for UX validation before building full WebSocket/persistence.
"""

import logging
from typing import Dict, List

from fastapi import APIRouter, HTTPException

from api.models.chat import ChatMessage, ChatRequest, ChatResponse
from api.services.chat_context import build_chat_context
from api.services.llm import get_llm_client

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_messages(
    system_prompt: str, conversation_history: List[ChatMessage], user_message: str
) -> List[Dict[str, str]]:
    """Build the messages array for the LLM chat call.

    Args:
        system_prompt: The system prompt with editor personality and project context
        conversation_history: Previous messages in the conversation
        user_message: The new user message to send

    Returns:
        List of message dicts for the LLM API
    """
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history
    for msg in conversation_history:
        messages.append({"role": msg.role, "content": msg.content})

    # Add new user message
    messages.append({"role": "user", "content": user_message})

    return messages


@router.post("/message", response_model=ChatResponse)
async def send_chat_message(request: ChatRequest) -> ChatResponse:
    """Send a message to the chat assistant.

    This endpoint:
    1. Builds a system prompt with editor personality and optional project context
    2. Includes conversation history for multi-turn context
    3. Calls the LLM and returns the response with cost metrics

    Args:
        request: ChatRequest with message, optional project_name, and history

    Returns:
        ChatResponse with assistant response and token/cost metrics

    Raises:
        HTTPException: 500 if LLM call fails
    """
    try:
        # Build system prompt with project context if provided
        system_prompt = build_chat_context(request.project_name)

        # Build messages array
        messages = _build_messages(
            system_prompt=system_prompt, conversation_history=request.conversation_history, user_message=request.message
        )

        # Get LLM client and chat config
        llm = get_llm_client()
        chat_config = llm.config.get("chat", {})

        # Make the LLM call
        response = await llm.chat(
            messages=messages,
            backend=chat_config.get("backend", "openrouter"),
            phase="chat",  # For Langfuse tracing
            max_tokens=chat_config.get("max_tokens", 4096),
            temperature=chat_config.get("temperature", 0.7),
        )

        logger.info(
            f"Chat response: {len(response.content)} chars, " f"{response.total_tokens} tokens, ${response.cost:.4f}"
        )

        return ChatResponse(
            response=response.content, tokens_used=response.total_tokens, cost=response.cost, model=response.model
        )

    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Chat request failed")
