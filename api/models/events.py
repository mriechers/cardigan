"""Event models for Cardigan API."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel


class EventType(str, Enum):
    """Valid event types matching database CHECK constraint."""

    job_queued = "job_queued"
    job_started = "job_started"
    job_completed = "job_completed"
    job_failed = "job_failed"
    phase_started = "phase_started"
    phase_completed = "phase_completed"
    phase_failed = "phase_failed"
    cost_update = "cost_update"
    model_selected = "model_selected"
    model_fallback = "model_fallback"
    system_pause = "system_pause"
    system_resume = "system_resume"
    system_error = "system_error"
    user_action = "user_action"
    api_call = "api_call"


class EventData(BaseModel):
    """Structured data for session events."""

    cost: Optional[float] = None
    tokens: Optional[int] = None
    backend: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    phase: Optional[str] = None
    from_model: Optional[str] = None
    to_model: Optional[str] = None
    reason: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class EventCreate(BaseModel):
    """Schema for creating a new event."""

    job_id: Optional[int] = None
    event_type: EventType
    data: Optional[EventData] = None


class SessionEvent(BaseModel):
    """Complete session event record."""

    id: int
    job_id: Optional[int] = None
    timestamp: datetime
    event_type: EventType
    data: Optional[EventData] = None

    class Config:
        from_attributes = True
