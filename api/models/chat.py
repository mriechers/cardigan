"""Chat models for Cardigan API.

Includes models for:
- Chat messages (individual conversation turns)
- Chat sessions (persistent conversation containers)
- Request/response schemas for chat endpoints
- Cost tracking and comparison
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ChatSessionStatus(str, Enum):
    """Status of a chat session."""

    active = "active"
    archived = "archived"
    cleared = "cleared"


class ChatMessage(BaseModel):
    """Single message in conversation (stored in database)."""

    id: Optional[int] = Field(None, description="Message ID (from database)")
    role: str = Field(..., description="Message role: 'user', 'assistant', or 'system'")
    content: str = Field(..., description="Message content")
    created_at: Optional[datetime] = Field(None, description="When message was created")
    tokens: Optional[int] = Field(None, description="Token count (assistant messages only)")
    cost: Optional[float] = Field(None, description="Cost in USD (assistant messages only)")
    model: Optional[str] = Field(None, description="Model used (assistant messages only)")
    duration_ms: Optional[int] = Field(None, description="Response time in ms (assistant messages only)")


class ChatSession(BaseModel):
    """Chat session with metadata and cumulative cost tracking."""

    id: str = Field(..., description="Session UUID")
    job_id: int = Field(..., description="Associated job ID")
    project_name: str = Field(..., description="Project name for context")
    created_at: datetime = Field(..., description="When session was created")
    updated_at: datetime = Field(..., description="Last activity timestamp")
    total_tokens: int = Field(default=0, description="Total tokens used in session")
    total_cost: float = Field(default=0.0, description="Total cost in USD for session")
    message_count: int = Field(default=0, description="Number of messages in session")
    status: ChatSessionStatus = Field(default=ChatSessionStatus.active, description="Session status")
    model: Optional[str] = Field(None, description="Primary model used in session")


class ChatSessionCreate(BaseModel):
    """Request to create a new chat session."""

    job_id: int = Field(..., description="Job ID (must be completed)")
    project_name: str = Field(..., description="Project name for context")


class ChatSessionResponse(BaseModel):
    """Response with session details and messages."""

    session: ChatSession = Field(..., description="Session metadata")
    messages: List[ChatMessage] = Field(default_factory=list, description="Message history")
    can_chat: bool = Field(..., description="Whether chat is available (job completed)")


class SessionListResponse(BaseModel):
    """List of sessions for a job."""

    sessions: List[ChatSession] = Field(..., description="List of chat sessions")
    total: int = Field(..., description="Total number of sessions")


# Legacy models for backward compatibility with prototype
class ChatRequest(BaseModel):
    """Request to send a chat message (REST endpoint)."""

    message: str = Field(..., description="User message to send")
    project_name: Optional[str] = Field(None, description="Project context for the chat")
    conversation_history: List[ChatMessage] = Field(
        default_factory=list, description="Previous messages in the conversation"
    )
    session_id: Optional[str] = Field(None, description="Session ID for persistent chat")


class ChatResponse(BaseModel):
    """Response from chat endpoint."""

    response: str = Field(..., description="Assistant's response")
    tokens_used: int = Field(..., description="Total tokens used in request/response")
    cost: float = Field(..., description="Cost in USD for this chat turn")
    model: str = Field(..., description="Model used (e.g., 'claude-sonnet-4-20250514')")
    message_id: Optional[int] = Field(None, description="Saved message ID (if session provided)")


class SessionStatsResponse(BaseModel):
    """Detailed statistics for a chat session."""

    session_id: str = Field(..., description="Session ID")
    total_cost: float = Field(..., description="Total cost in USD")
    total_tokens: int = Field(..., description="Total tokens used")
    message_count: int = Field(..., description="Total messages")
    user_messages: int = Field(..., description="User message count")
    assistant_messages: int = Field(..., description="Assistant message count")
    models_used: List[str] = Field(..., description="Unique models used")
    avg_response_tokens: float = Field(..., description="Average tokens per assistant response")
    avg_response_cost: float = Field(..., description="Average cost per assistant response")
    duration_minutes: float = Field(..., description="Session duration in minutes")


class CostComparisonResponse(BaseModel):
    """Cost comparison between automated phases and chat sessions."""

    job_id: int = Field(..., description="Job ID")
    automated_phases: dict = Field(
        ..., description="Cost breakdown by automated phase", example={"analyst": {"cost": 0.02, "tokens": 1500}}
    )
    chat_sessions: List[dict] = Field(
        ...,
        description="Cost breakdown by chat session",
        example=[{"id": "...", "cost": 0.12, "tokens": 8000, "messages": 14}],
    )
    totals: dict = Field(..., description="Total costs", example={"automated": 0.075, "chat": 0.12, "combined": 0.195})
