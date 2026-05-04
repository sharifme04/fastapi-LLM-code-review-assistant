"""Analytics endpoint: per-language stats + cost summary."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import Float, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.review import Review
from app.models.tool_call import ToolCall
from app.schemas.analytics import AnalyticsSummary, CostSummary, LanguageStat
from app.services.cost import get_monthly_cost_summary

logger = logging.getLogger("code_review_assistant")
router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(
    db: AsyncSession = Depends(get_db),
) -> AnalyticsSummary:
    """Aggregated analytics: total reviews, per-language breakdown, cost summary."""
    total_result = await db.execute(select(func.count(Review.id)))
    total_reviews = total_result.scalar() or 0

    lang_query = (
        select(
            Review.language.cast(String).label("language"),
            func.count(Review.id).label("count"),
            func.avg(Review.issues_count).cast(Float).label("avg_issues"),
            func.sum(Review.cost).cast(Float).label("total_cost"),
        )
        .group_by(Review.language)
        .order_by(func.count(Review.id).desc())
    )
    lang_result = await db.execute(lang_query)
    languages = [
        LanguageStat(
            language=row.language,
            count=row.count,
            avg_issues=round(row.avg_issues or 0.0, 2),
            total_cost=round(row.total_cost or 0.0, 4),
        )
        for row in lang_result.all()
    ]

    avg_issues_result = await db.execute(
        select(func.avg(Review.issues_count).cast(Float))
    )
    avg_issues_per_review = round(avg_issues_result.scalar() or 0.0, 2)

    tool_calls_result = await db.execute(select(func.count(ToolCall.id)))
    total_tool_calls = tool_calls_result.scalar() or 0

    cost_data = await get_monthly_cost_summary(db)
    cost_summary = CostSummary(**cost_data)

    return AnalyticsSummary(
        total_reviews=total_reviews,
        languages=languages,
        cost_summary=cost_summary,
        avg_issues_per_review=avg_issues_per_review,
        total_tool_calls=total_tool_calls,
    )
