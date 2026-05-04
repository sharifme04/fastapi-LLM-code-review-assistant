"""Code review service: streaming Claude reviews with manual tool-calling loop.

This is the core of Project 2. It implements the multi-turn tool-calling
loop manually (no LangChain) per the project plan. The flow is:

    1. Hash (code + language) → check Redis for cached review
    2. If cache miss: send messages + tool definitions to Claude (streaming)
    3. As tokens arrive, push them to the on_chunk callback for the WebSocket
    4. When Claude requests a tool, execute it locally, append the result to
       the messages list, and call Claude again — repeat until stop_reason
       is "end_turn"
    5. Persist the final review + tool calls to PostgreSQL, cache it in Redis,
       update cost tracker

The on_chunk / on_tool_call callbacks let the WebSocket route stream events
to the browser without coupling this module to FastAPI.
"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Optional

import anthropic
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.review import Review
from app.models.tool_call import ToolCall
from app.services.cost import calculate_cost, check_cost_limit, log_api_cost
from app.services.tools import (
    SUPPORTED_LANGUAGES,
    TOOL_DEFINITIONS,
    check_style_guide,
    lookup_documentation,
)
from app.utils.exceptions import ReviewError, ValidationFailureError

logger = logging.getLogger("code_review_assistant")
settings = get_settings()

CACHE_TTL = 3600  # 1 hour
MAX_TOOL_ITERATIONS = 5  # safety bound on the tool-calling loop

REVIEW_SYSTEM_PROMPT = """You are an expert code reviewer with deep knowledge of software engineering best practices, design patterns, and common pitfalls across many languages.

Your job is to review the user's code snippet and provide actionable, specific feedback.

Review guidelines:
1. Identify concrete issues — bugs, security risks, performance, readability, complexity, style.
2. For each issue, prefix it with a marker the IDE will pick up:
   [ISSUE:type:line] short description, then a longer explanation.
   Valid types: bug, security, performance, complexity, style, naming, docs.
3. Cite specific line numbers when possible. If a problem spans multiple lines, use the first line.
4. Be constructive — explain WHY something is a problem and suggest a fix.
5. Use the available tools when you need authoritative references:
   - lookup_documentation(language, topic): for language rules, PEP standards, MDN references
   - check_style_guide(code_snippet, guide_name): for style violations against pep8/google/airbnb
6. Do not invent issues. If the code is good, say so and explain why.
7. End with a 1-2 sentence overall verdict.

Format your response as flowing markdown — use the [ISSUE:...] markers inline, not in a table.
"""

# --- Callback type aliases ---
TextChunkCallback = Callable[[str], Awaitable[None]]
ToolCallCallback = Callable[[str, dict, str], Awaitable[None]]


def _cache_key(code: str, language: str) -> str:
    """Deterministic Redis key for a code+language pair."""
    h = hashlib.sha256(f"{language}:{code}".encode()).hexdigest()[:16]
    return f"review:{h}"


def validate_submission(code: str, language: str) -> None:
    """Validate a code submission before sending it to Claude.

    Args:
        code: Source code to review.
        language: Programming language.

    Raises:
        ValidationFailureError: If validation fails.
    """
    if not code or not code.strip():
        raise ValidationFailureError("Code snippet is empty.")

    line_count = code.count("\n") + 1
    if line_count > settings.max_code_lines:
        raise ValidationFailureError(
            f"Code exceeds maximum of {settings.max_code_lines} lines (got {line_count}).",
            detail={"line_count": line_count, "limit": settings.max_code_lines},
        )

    if language.lower() not in SUPPORTED_LANGUAGES:
        raise ValidationFailureError(
            f"Unsupported language: {language!r}. Supported: {sorted(SUPPORTED_LANGUAGES)}",
            detail={"language": language, "supported": sorted(SUPPORTED_LANGUAGES)},
        )


def _execute_tool(name: str, tool_input: dict) -> str:
    """Dispatch tool calls from Claude to the local implementations.

    Args:
        name: Tool name as defined in TOOL_DEFINITIONS.
        tool_input: Validated input dict from Claude.

    Returns:
        Tool result string (plain text, suitable for sending back as tool_result).
    """
    if name == "lookup_documentation":
        return lookup_documentation(
            language=tool_input.get("language", ""),
            topic=tool_input.get("topic", ""),
        )
    if name == "check_style_guide":
        return check_style_guide(
            code_snippet=tool_input.get("code_snippet", ""),
            guide_name=tool_input.get("guide_name", ""),
        )
    return f"Unknown tool: {name}"


async def _try_cache(
    redis: aioredis.Redis, code: str, language: str
) -> Optional[dict]:
    """Look up a cached review by content hash."""
    raw = await redis.get(_cache_key(code, language))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupt cache entry, ignoring")
        return None


async def _store_cache(
    redis: aioredis.Redis,
    code: str,
    language: str,
    payload: dict,
) -> None:
    """Persist the review payload to Redis with TTL."""
    await redis.set(_cache_key(code, language), json.dumps(payload), ex=CACHE_TTL)


def _count_issues(review_text: str) -> int:
    """Count [ISSUE:...] markers in the review markdown."""
    return review_text.count("[ISSUE:")


async def review_code(
    code: str,
    language: str,
    db: AsyncSession,
    redis: aioredis.Redis,
    user_id: str = "anonymous",
    style_guide: Optional[str] = None,
    on_chunk: Optional[TextChunkCallback] = None,
    on_tool_call: Optional[ToolCallCallback] = None,
) -> Review:
    """Run a streaming code review with manual tool-calling loop.

    Args:
        code: Source code to review.
        language: Programming language.
        db: Async DB session (caller commits).
        redis: Redis client.
        user_id: Caller identity (default 'anonymous').
        style_guide: Optional style guide name to bias the review.
        on_chunk: Async callback invoked with each text chunk from Claude.
        on_tool_call: Async callback invoked with (tool_name, tool_input, result).

    Returns:
        Persisted Review row (cache hit or freshly generated).

    Raises:
        ValidationFailureError: If the submission is invalid.
        CostLimitExceededError: If the daily cost limit has been reached.
        ReviewError: If the Anthropic call fails or the loop stalls.
    """
    validate_submission(code, language)

    # --- Cache check ---
    cached = await _try_cache(redis, code, language)
    if cached is not None:
        logger.info("Review cache hit", extra={"language": language, "user_id": user_id})

        if on_chunk is not None:
            await on_chunk(cached["review_text"])

        review = Review(
            user_id=user_id,
            code=code,
            language=language,
            review_text=cached["review_text"],
            issues_count=cached.get("issues_count", _count_issues(cached["review_text"])),
            tokens_used=0,
            cost=0.0,
            cache_hit=True,
        )
        db.add(review)
        await db.flush()

        await log_api_cost(db, input_tokens=0, output_tokens=0, cache_hit=True)
        return review

    # --- Cost gate ---
    await check_cost_limit(db)

    # --- Build initial user message ---
    user_message_parts = [f"Language: {language}"]
    if style_guide:
        user_message_parts.append(f"Style guide: {style_guide}")
    user_message_parts.append("Code to review:")
    user_message_parts.append(f"```{language}\n{code}\n```")
    user_message = "\n".join(user_message_parts)

    messages: list[dict] = [{"role": "user", "content": user_message}]

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    full_review_text = ""
    total_input_tokens = 0
    total_output_tokens = 0
    tool_calls_made: list[tuple[str, dict, str]] = []

    try:
        for iteration in range(MAX_TOOL_ITERATIONS):
            async with client.messages.stream(
                model=settings.anthropic_model,
                max_tokens=2048,
                system=REVIEW_SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            ) as stream:
                async for event in stream:
                    # Text deltas — stream live to the client.
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if getattr(delta, "type", None) == "text_delta":
                            text_piece = delta.text
                            full_review_text += text_piece
                            if on_chunk is not None:
                                await on_chunk(text_piece)

                final_message = await stream.get_final_message()

            usage = final_message.usage
            total_input_tokens += usage.input_tokens
            total_output_tokens += usage.output_tokens

            stop_reason = final_message.stop_reason
            logger.info(
                "Stream iteration finished",
                extra={"iteration": iteration, "stop_reason": stop_reason},
            )

            if stop_reason != "tool_use":
                # end_turn / max_tokens / stop_sequence — we're done
                break

            # --- Manual tool-calling: dispatch each tool_use block ---
            assistant_blocks = []
            tool_results = []
            for block in final_message.content:
                # Re-serialize blocks to dicts for the next request
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    result_text = _execute_tool(block.name, block.input)
                    tool_calls_made.append((block.name, dict(block.input), result_text))

                    if on_tool_call is not None:
                        await on_tool_call(block.name, dict(block.input), result_text)

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        }
                    )

            messages.append({"role": "assistant", "content": assistant_blocks})
            messages.append({"role": "user", "content": tool_results})
        else:
            # Loop exhausted without natural stop
            logger.warning("Tool-calling loop hit max iterations")

    except anthropic.APIError as e:
        logger.error("Anthropic API error during review: %s", str(e))
        raise ReviewError(
            message=f"Claude API call failed: {getattr(e, 'message', str(e))}",
            detail={"api_error": str(e)},
        )

    if not full_review_text.strip():
        raise ReviewError(
            "Empty review generated (no text content from Claude).",
            detail={"iterations": iteration + 1},
        )

    cost = calculate_cost(total_input_tokens, total_output_tokens)
    issues_count = _count_issues(full_review_text)

    review = Review(
        user_id=user_id,
        code=code,
        language=language,
        review_text=full_review_text,
        issues_count=issues_count,
        tokens_used=total_input_tokens + total_output_tokens,
        cost=cost,
        cache_hit=False,
    )
    db.add(review)
    await db.flush()

    for name, tool_input, result_text in tool_calls_made:
        db.add(
            ToolCall(
                review_id=review.id,
                tool_name=name,
                tool_input=tool_input,
                result=result_text,
            )
        )

    await log_api_cost(
        db,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cache_hit=False,
    )

    await _store_cache(
        redis,
        code,
        language,
        {"review_text": full_review_text, "issues_count": issues_count},
    )

    logger.info(
        "Review completed",
        extra={
            "review_id": review.id,
            "language": language,
            "issues": issues_count,
            "tool_calls": len(tool_calls_made),
            "tokens": total_input_tokens + total_output_tokens,
            "cost": cost,
        },
    )

    return review


def render_markdown_export(review: Review) -> str:
    """Render a Review row as a self-contained markdown document."""
    header = (
        f"# Code Review #{review.id}\n\n"
        f"- Language: `{review.language}`\n"
        f"- Created: {review.created_at.isoformat() if review.created_at else 'n/a'}\n"
        f"- Issues found: {review.issues_count}\n"
        f"- Tokens used: {review.tokens_used}\n"
        f"- Cost: ${review.cost:.6f}\n"
        f"- Cache hit: {review.cache_hit}\n\n"
    )
    code_block = f"## Submitted Code\n\n```{review.language}\n{review.code}\n```\n\n"
    body = f"## Review\n\n{review.review_text or '_(empty)_'}\n"
    return header + code_block + body
