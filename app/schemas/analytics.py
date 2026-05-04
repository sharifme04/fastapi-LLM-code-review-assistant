"""Pydantic schemas for analytics responses."""

from pydantic import BaseModel, Field


class LanguageStat(BaseModel):
    """Statistics for a single programming language."""

    language: str
    count: int
    avg_issues: float = Field(..., description="Average issues found per review")
    total_cost: float = Field(..., description="Total LLM cost for this language")


class CostSummary(BaseModel):
    """Cost summary for the current month."""

    total_cost: float
    total_requests: int
    total_cache_hits: int
    cache_hit_rate: float = Field(..., description="Percentage of requests served from cache")
    avg_cost_per_request: float


class AnalyticsSummary(BaseModel):
    """Full analytics summary response."""

    total_reviews: int
    languages: list[LanguageStat]
    cost_summary: CostSummary
    avg_issues_per_review: float
    total_tool_calls: int
