"""Pydantic schemas for tool calling."""

from pydantic import BaseModel, Field


class DocumentationLookup(BaseModel):
    """Input schema for the documentation lookup tool."""

    language: str = Field(..., description="Programming language to look up docs for")
    topic: str = Field(..., description="Topic or concept to look up")


class StyleGuideCheck(BaseModel):
    """Input schema for the style guide check tool."""

    code_snippet: str = Field(..., description="Code snippet to check against style guide")
    guide_name: str = Field(..., description="Style guide to check against (e.g. 'pep8', 'google')")


class ToolResult(BaseModel):
    """Result from executing a tool."""

    tool_name: str
    tool_use_id: str
    result: str
    is_error: bool = False


class CostInfo(BaseModel):
    """Token usage and cost information for a single API call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    daily_total: float | None = None
    budget_remaining: float | None = None
