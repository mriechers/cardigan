"""Glossary router for Cardigan API.

Read the project glossary summary and add Whisper prompt terms from the
media upload intake form. Correction-table writes stay with the worker's
editorial-feedback extraction and the transcript-review approve flow.
"""

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.middleware.rate_limit import RATE_EXPENSIVE, RATE_READ, limiter
from api.services import glossary

logger = logging.getLogger(__name__)

router = APIRouter()


class GlossarySummary(BaseModel):
    """Summary of the project glossary."""

    whisper_terms: List[str]
    whisper_term_count: int
    correction_count: int


class AddTermsRequest(BaseModel):
    """Terms to append to the Whisper Prompt Terms section."""

    terms: List[str] = Field(..., min_length=1, max_length=100)


class AddTermsResponse(BaseModel):
    """Result of a term-append request."""

    added: int


@router.get("", response_model=GlossarySummary)
@limiter.limit(RATE_READ)
async def get_glossary_summary(request: Request) -> GlossarySummary:
    """Return whisper prompt terms and correction-table row count."""
    return GlossarySummary(**glossary.read_glossary_summary())


@router.post("/terms", response_model=AddTermsResponse)
@limiter.limit(RATE_EXPENSIVE)
async def add_whisper_terms(request: Request, body: AddTermsRequest) -> AddTermsResponse:
    """Append new terms to the Whisper Prompt Terms section (deduplicated)."""
    cleaned = [t for t in (term.strip() for term in body.terms) if t]
    if not cleaned:
        raise HTTPException(status_code=400, detail="No non-empty terms provided")
    if any(len(t) > 200 for t in cleaned):
        raise HTTPException(status_code=400, detail="Terms must be 200 characters or fewer")
    added = glossary.add_whisper_terms(cleaned)
    return AddTermsResponse(added=added)
