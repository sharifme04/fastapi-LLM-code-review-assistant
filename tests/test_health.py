"""Health endpoint tests."""

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "connected"
    assert body["redis"] == "connected"
    assert "version" in body


@pytest.mark.asyncio
async def test_root_returns_html(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "Code Review Assistant" in response.text
