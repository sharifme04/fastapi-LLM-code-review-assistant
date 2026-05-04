"""Pydantic schemas for review API requests and responses."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CodeSubmission(BaseModel):
    """Request schema for submitting code for review (WebSocket or REST)."""

    code: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description="Code snippet to review",
        examples=["def hello():\n    print('Hello, World!')"],
    )
    language: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Programming language of the code",
        examples=["python"],
    )
    style_guide: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Optional style guide to use (e.g. 'pep8', 'google', 'airbnb')",
        examples=["pep8"],
    )


class ToolCallDetail(BaseModel):
    """Embedded tool call info in review response."""

    tool_name: str
    tool_input: dict
    result: str
    created_at: datetime


class ReviewResponse(BaseModel):
    """Response schema for a single code review."""

    id: int
    user_id: str
    code: str
    language: str
    review_text: Optional[str] = None
    issues_count: int
    tokens_used: int
    cost: float
    cache_hit: bool
    created_at: datetime
    updated_at: datetime
    tool_calls: list[ToolCallDetail] = []

    model_config = {"from_attributes": True}


class ReviewListResponse(BaseModel):
    """Response schema for listing multiple reviews."""

    reviews: list[ReviewResponse]
    total: int
    page: int
    page_size: int


class ReviewExportResponse(BaseModel):
    """Markdown export of a review."""

    review_id: int
    language: str
    markdown: str
    exported_at: datetime
