"""Cost tracking service.

Logs API token usage, calculates costs, aggregates daily totals,
and raises CostLimitExceededError when daily threshold is breached.
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.api_cost import ApiCost
from app.schemas.tool_schemas import CostInfo
from app.utils.exceptions import CostLimitExceededError

logger = logging.getLogger("code_review_assistant")
settings = get_settings()


def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for a single API call (per-1M pricing).

    Args:
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.

    Returns:
        Cost in USD rounded to 6 decimals.
    """
    input_cost = (input_tokens / 1_000_000) * settings.anthropic_input_price_per_mtok
    output_cost = (output_tokens / 1_000_000) * settings.anthropic_output_price_per_mtok
    return round(input_cost + output_cost, 6)


async def log_api_cost(
    db: AsyncSession,
    input_tokens: int,
    output_tokens: int,
    cache_hit: bool = False,
) -> CostInfo:
    """Log token usage + cost for a single API call and update the daily aggregate.

    Args:
        db: Async database session.
        input_tokens: Input tokens used.
        output_tokens: Output tokens generated.
        cache_hit: Whether the call was served from Redis cache.

    Returns:
        CostInfo with per-request and daily totals.
    """
    cost = 0.0 if cache_hit else calculate_cost(input_tokens, output_tokens)
    today = date.today()

    result = await db.execute(select(ApiCost).where(ApiCost.date == today))
    daily_record = result.scalar_one_or_none()

    if daily_record is None:
        daily_record = ApiCost(
            date=today,
            total_cost=cost,
            request_count=1,
            cache_hit_count=1 if cache_hit else 0,
        )
        db.add(daily_record)
    else:
        daily_record.total_cost += cost
        daily_record.request_count += 1
        if cache_hit:
            daily_record.cache_hit_count += 1

    await db.flush()

    logger.info(
        "API cost logged",
        extra={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "cache_hit": cache_hit,
            "daily_total": daily_record.total_cost,
        },
    )

    return CostInfo(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        daily_total=daily_record.total_cost,
        budget_remaining=settings.daily_cost_limit - daily_record.total_cost,
    )


async def check_cost_limit(db: AsyncSession) -> None:
    """Raise CostLimitExceededError if the daily cost limit has been hit.

    Args:
        db: Async database session.
    """
    today = date.today()
    result = await db.execute(select(ApiCost).where(ApiCost.date == today))
    daily_record = result.scalar_one_or_none()

    if daily_record and daily_record.total_cost >= settings.daily_cost_limit:
        logger.warning(
            "Daily cost limit exceeded",
            extra={
                "daily_total": daily_record.total_cost,
                "limit": settings.daily_cost_limit,
            },
        )
        raise CostLimitExceededError(
            daily_total=daily_record.total_cost,
            limit=settings.daily_cost_limit,
        )


async def get_monthly_cost_summary(db: AsyncSession) -> dict:
    """Get aggregated cost summary for the current month."""
    today = date.today()
    first_of_month = today.replace(day=1)

    result = await db.execute(select(ApiCost).where(ApiCost.date >= first_of_month))
    records = result.scalars().all()

    if not records:
        return {
            "total_cost": 0.0,
            "total_requests": 0,
            "total_cache_hits": 0,
            "cache_hit_rate": 0.0,
            "avg_cost_per_request": 0.0,
        }

    total_cost = sum(r.total_cost for r in records)
    total_requests = sum(r.request_count for r in records)
    total_cache_hits = sum(r.cache_hit_count for r in records)
    cache_hit_rate = (total_cache_hits / total_requests * 100) if total_requests else 0.0
    avg_cost = total_cost / total_requests if total_requests else 0.0

    return {
        "total_cost": round(total_cost, 4),
        "total_requests": total_requests,
        "total_cache_hits": total_cache_hits,
        "cache_hit_rate": round(cache_hit_rate, 2),
        "avg_cost_per_request": round(avg_cost, 6),
    }
