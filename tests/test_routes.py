"""Tests for REST review listing/get/export and analytics endpoints."""

from datetime import datetime, timezone

import pytest

from app.models.review import Review
from app.models.tool_call import ToolCall
from tests.conftest import test_async_session


@pytest.mark.asyncio
async def test_list_reviews_empty(client):
    response = await client.get("/reviews")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["reviews"] == []


@pytest.mark.asyncio
async def test_list_and_get_review_after_seed(client):
    async with test_async_session() as session:
        review = Review(
            user_id="u1",
            code="x = 1",
            language="python",
            review_text="Looks fine. [ISSUE:style:1] something.",
            issues_count=1,
            tokens_used=50,
            cost=0.001,
            cache_hit=False,
        )
        session.add(review)
        await session.flush()
        session.add(
            ToolCall(
                review_id=review.id,
                tool_name="lookup_documentation",
                tool_input={"language": "python", "topic": "x"},
                result="docs",
            )
        )
        await session.commit()
        review_id = review.id

    list_resp = await client.get("/reviews")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    get_resp = await client.get(f"/reviews/{review_id}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["id"] == review_id
    assert body["issues_count"] == 1
    assert len(body["tool_calls"]) == 1


@pytest.mark.asyncio
async def test_get_review_not_found(client):
    response = await client.get("/reviews/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_export_review_returns_markdown(client):
    async with test_async_session() as session:
        review = Review(
            user_id="u1",
            code="def f(): pass",
            language="python",
            review_text="All good.",
            issues_count=0,
            tokens_used=10,
            cost=0.0001,
            cache_hit=False,
        )
        session.add(review)
        await session.commit()
        review_id = review.id

    response = await client.post(f"/reviews/{review_id}/export")
    assert response.status_code == 200
    body = response.json()
    assert body["review_id"] == review_id
    assert "Code Review" in body["markdown"]
    assert "All good." in body["markdown"]


@pytest.mark.asyncio
async def test_analytics_summary_empty(client):
    response = await client.get("/analytics/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_reviews"] == 0
    assert body["languages"] == []


@pytest.mark.asyncio
async def test_analytics_summary_with_data(client):
    async with test_async_session() as session:
        for i in range(3):
            session.add(
                Review(
                    user_id="u1",
                    code=f"x = {i}",
                    language="python",
                    review_text=f"Issue {i}",
                    issues_count=i,
                    tokens_used=100,
                    cost=0.001,
                    cache_hit=False,
                )
            )
        await session.commit()

    response = await client.get("/analytics/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_reviews"] == 3
    assert len(body["languages"]) == 1
    assert body["languages"][0]["language"] == "python"
    assert body["languages"][0]["count"] == 3
