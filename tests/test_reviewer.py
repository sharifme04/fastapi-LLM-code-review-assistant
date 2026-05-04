"""Tests for the reviewer service: validation, caching, and the manual tool-calling loop."""

import pytest
from sqlalchemy import select

from app.models.tool_call import ToolCall
from app.services.reviewer import (
    _cache_key,
    _count_issues,
    review_code,
    validate_submission,
)
from app.utils.exceptions import ValidationFailureError
from tests.conftest import (
    make_text_stream,
    make_tool_use_stream,
    test_async_session,
)


# --------------------------- Validation --------------------------- #


def test_validate_rejects_empty_code():
    with pytest.raises(ValidationFailureError):
        validate_submission("", "python")


def test_validate_rejects_too_many_lines():
    code = "x = 1\n" * 1000
    with pytest.raises(ValidationFailureError):
        validate_submission(code, "python")


def test_validate_rejects_unsupported_language():
    with pytest.raises(ValidationFailureError):
        validate_submission("x = 1", "fortran")


def test_validate_accepts_valid_input():
    validate_submission("def f():\n    return 1\n", "python")


# --------------------------- Helpers --------------------------- #


def test_cache_key_is_deterministic():
    assert _cache_key("a", "python") == _cache_key("a", "python")
    assert _cache_key("a", "python") != _cache_key("b", "python")
    assert _cache_key("a", "python") != _cache_key("a", "javascript")


def test_count_issues():
    text = "Looks good [ISSUE:bug:5] but [ISSUE:style:10] also this."
    assert _count_issues(text) == 2


# --------------------------- Reviewer flow --------------------------- #


@pytest.mark.asyncio
async def test_review_code_simple_text_only(db_session, fake_redis, patch_anthropic):
    """Single-turn review with no tool calls — text streams, review is persisted."""
    review_text = "Looks fine. [ISSUE:style:1] Consider snake_case here."
    patch_anthropic([make_text_stream(review_text, input_tokens=120, output_tokens=40)])

    chunks: list[str] = []

    async def on_chunk(t: str) -> None:
        chunks.append(t)

    review = await review_code(
        code="def Foo():\n    return 1\n",
        language="python",
        db=db_session,
        redis=fake_redis,
        on_chunk=on_chunk,
    )

    assert review.review_text == review_text
    assert review.issues_count == 1
    assert review.tokens_used == 160
    assert review.cost > 0
    assert review.cache_hit is False
    assert "".join(chunks) == review_text


@pytest.mark.asyncio
async def test_review_code_tool_call_then_finish(db_session, fake_redis, patch_anthropic):
    """Two-turn review: model calls a tool, then produces final text."""
    pre_text = "Let me check the docs."
    final_text = "Done. [ISSUE:naming:1] use snake_case."

    patch_anthropic(
        [
            make_tool_use_stream(
                pre_text=pre_text,
                tool_name="lookup_documentation",
                tool_input={"language": "python", "topic": "naming conventions"},
                tool_use_id="tu_1",
                input_tokens=80,
                output_tokens=20,
            ),
            make_text_stream(final_text, input_tokens=200, output_tokens=60),
        ]
    )

    tool_events: list[tuple[str, dict, str]] = []

    async def on_tool_call(name: str, tool_input: dict, result: str) -> None:
        tool_events.append((name, tool_input, result))

    review = await review_code(
        code="def Foo():\n    return 1\n",
        language="python",
        db=db_session,
        redis=fake_redis,
        on_tool_call=on_tool_call,
    )

    # Combined streamed text from both turns
    assert pre_text in review.review_text
    assert final_text in review.review_text

    # Tool was actually executed
    assert len(tool_events) == 1
    assert tool_events[0][0] == "lookup_documentation"
    assert "snake_case" in tool_events[0][2]

    # Tokens summed across both turns
    assert review.tokens_used == 80 + 20 + 200 + 60

    # Tool call persisted (query directly to avoid async-lazy-load)
    review_id = review.id
    await db_session.commit()
    async with test_async_session() as fresh:
        rows = (
            await fresh.execute(select(ToolCall).where(ToolCall.review_id == review_id))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].tool_name == "lookup_documentation"


@pytest.mark.asyncio
async def test_review_code_cache_hit(db_session, fake_redis, patch_anthropic):
    """Second call with the same code+language hits Redis and skips the API."""
    review_text = "Cached review. [ISSUE:bug:2] something."
    patch_anthropic([make_text_stream(review_text)])

    code = "x = 1\n"
    await review_code(
        code=code, language="python", db=db_session, redis=fake_redis
    )

    # Second call — no new patched scripts, so a real API call would fail.
    # The cache hit must serve it.
    chunks: list[str] = []

    async def on_chunk(t: str) -> None:
        chunks.append(t)

    review2 = await review_code(
        code=code,
        language="python",
        db=db_session,
        redis=fake_redis,
        on_chunk=on_chunk,
    )
    assert review2.cache_hit is True
    assert review2.tokens_used == 0
    assert review2.cost == 0.0
    assert "".join(chunks) == review_text
