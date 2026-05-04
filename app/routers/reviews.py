"""Review endpoints: REST list/get/export + WebSocket streaming review.

The WebSocket endpoint at /review is the primary interface — clients send
{type: "submit_code", code, language, style_guide?} and receive a stream of
{type: "review_chunk" | "tool_call" | "review_complete" | "error"} messages.

The REST endpoints under /reviews are for browsing past reviews.
"""

import json
import logging
from typing import Optional

import redis.asyncio as aioredis
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session, get_db
from app.models.review import Review
from app.redis_client import redis_pool
from app.schemas.review import (
    ReviewExportResponse,
    ReviewListResponse,
    ReviewResponse,
    ToolCallDetail,
)
from app.services.reviewer import render_markdown_export, review_code
from app.utils.exceptions import (
    AppError,
    CostLimitExceededError,
    ReviewError,
    ValidationFailureError,
)

logger = logging.getLogger("code_review_assistant")
router = APIRouter(tags=["Reviews"])


# --------------------------- REST endpoints --------------------------- #


@router.get("/reviews", response_model=ReviewListResponse)
async def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    language: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ReviewListResponse:
    """List past reviews with pagination, optionally filtered by language."""
    base = select(Review).options(selectinload(Review.tool_calls))
    count_base = select(func.count(Review.id))

    if language:
        base = base.where(Review.language == language.lower())
        count_base = count_base.where(Review.language == language.lower())

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    rows_result = await db.execute(
        base.order_by(Review.created_at.desc()).offset(offset).limit(page_size)
    )
    rows = rows_result.scalars().all()

    return ReviewListResponse(
        reviews=[_review_to_schema(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/reviews/{review_id}", response_model=ReviewResponse)
async def get_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """Fetch a single review with its tool calls."""
    result = await db.execute(
        select(Review)
        .options(selectinload(Review.tool_calls))
        .where(Review.id == review_id)
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail=f"Review {review_id} not found")
    return _review_to_schema(review)


@router.post("/reviews/{review_id}/export", response_model=ReviewExportResponse)
async def export_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
) -> ReviewExportResponse:
    """Export a review as a self-contained markdown document."""
    from datetime import datetime, timezone

    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail=f"Review {review_id} not found")

    return ReviewExportResponse(
        review_id=review.id,
        language=review.language,
        markdown=render_markdown_export(review),
        exported_at=datetime.now(timezone.utc),
    )


# --------------------------- WebSocket --------------------------- #


@router.websocket("/review")
async def websocket_review(websocket: WebSocket) -> None:
    """Stream a code review over a WebSocket.

    Protocol:
        client → {"type": "submit_code", "code": ..., "language": ..., "style_guide": ...?}
        server → {"type": "review_chunk", "chunk": "...", "cache_hit": false}
        server → {"type": "tool_call", "tool": "...", "input": {...}, "result": "..."}
        server → {"type": "review_complete", "metadata": {...}}
        server → {"type": "error", "error": "...", "detail": ...?}

    Errors close the socket with a 1011 (internal error) code.
    """
    await websocket.accept()

    try:
        raw = await websocket.receive_text()
    except WebSocketDisconnect:
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "error": "Invalid JSON"})
        await websocket.close(code=1003)
        return

    if payload.get("type") != "submit_code":
        await websocket.send_json(
            {"type": "error", "error": "Expected first message of type 'submit_code'"}
        )
        await websocket.close(code=1003)
        return

    code = payload.get("code", "")
    language = (payload.get("language") or "").lower()
    style_guide = payload.get("style_guide")
    user_id = payload.get("user_id", "anonymous")

    logger.info(
        "WebSocket review starting",
        extra={"language": language, "code_length": len(code), "user_id": user_id},
    )

    async def on_chunk(text: str) -> None:
        await websocket.send_json({"type": "review_chunk", "chunk": text})

    async def on_tool_call(name: str, tool_input: dict, result: str) -> None:
        await websocket.send_json(
            {
                "type": "tool_call",
                "tool": name,
                "input": tool_input,
                "result": result,
            }
        )

    redis = aioredis.Redis(connection_pool=redis_pool)
    try:
        async with async_session() as session:
            try:
                review = await review_code(
                    code=code,
                    language=language,
                    db=session,
                    redis=redis,
                    user_id=user_id,
                    style_guide=style_guide,
                    on_chunk=on_chunk,
                    on_tool_call=on_tool_call,
                )
                await session.commit()
            except (
                ValidationFailureError,
                CostLimitExceededError,
                ReviewError,
                AppError,
            ) as e:
                await session.rollback()
                logger.warning(
                    "WebSocket review failed",
                    extra={"error": e.message, "status_code": e.status_code},
                )
                await websocket.send_json(
                    {"type": "error", "error": e.message, "detail": e.detail}
                )
                await websocket.close(code=1011)
                return

        await websocket.send_json(
            {
                "type": "review_complete",
                "metadata": {
                    "review_id": review.id,
                    "issues_count": review.issues_count,
                    "tokens_used": review.tokens_used,
                    "cost": review.cost,
                    "cache_hit": review.cache_hit,
                    "language": review.language,
                },
            }
        )
        await websocket.close()

    except WebSocketDisconnect:
        logger.info("Client disconnected mid-stream")
    except Exception as e:
        logger.exception("Unexpected WebSocket error: %s", str(e))
        try:
            await websocket.send_json(
                {"type": "error", "error": "Internal server error"}
            )
            await websocket.close(code=1011)
        except RuntimeError:
            pass
    finally:
        await redis.aclose()


# --------------------------- Helpers --------------------------- #


def _review_to_schema(review: Review) -> ReviewResponse:
    return ReviewResponse(
        id=review.id,
        user_id=review.user_id,
        code=review.code,
        language=review.language,
        review_text=review.review_text,
        issues_count=review.issues_count,
        tokens_used=review.tokens_used,
        cost=review.cost,
        cache_hit=review.cache_hit,
        created_at=review.created_at,
        updated_at=review.updated_at,
        tool_calls=[
            ToolCallDetail(
                tool_name=tc.tool_name,
                tool_input=tc.tool_input,
                result=tc.result,
                created_at=tc.created_at,
            )
            for tc in (review.tool_calls or [])
        ],
    )
