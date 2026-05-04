"""WebSocket review tests.

Uses Starlette's TestClient which supports WebSockets out of the box.
The reviewer service is patched (via the fake Anthropic SDK) so no real
API calls are made.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import (
    FakeRedis,
    test_async_session,
    make_text_stream,
    make_tool_use_stream,
)


@pytest.fixture
def ws_setup(monkeypatch, patch_anthropic):
    """Wire the WebSocket route to the test DB session + a fresh FakeRedis.

    The route in app/routers/reviews.py creates its own session/Redis (it
    is not driven by FastAPI's Depends), so we patch the symbols it imports.
    """
    fake_redis = FakeRedis()

    # Patch the session factory the WS route uses
    monkeypatch.setattr(
        "app.routers.reviews.async_session", test_async_session
    )

    # Replace the Redis client construction with a fake
    class _RedisFactoryStub:
        @staticmethod
        def from_url(*args, **kwargs):
            return None  # not used directly

    # Patch aioredis.Redis(connection_pool=...) inside the WS route
    import app.routers.reviews as reviews_mod

    original_redis_class = None
    import redis.asyncio as aioredis

    class _FakeRedisInstance(FakeRedis):
        pass

    def _fake_redis_ctor(*args, **kwargs):
        return fake_redis

    monkeypatch.setattr(aioredis, "Redis", _fake_redis_ctor)

    return {"redis": fake_redis, "patch_anthropic": patch_anthropic}


def test_websocket_simple_review(ws_setup):
    """WebSocket review flow with no tool calls."""
    review_text = "Looks ok. [ISSUE:style:1] minor naming."
    ws_setup["patch_anthropic"]([make_text_stream(review_text)])

    client = TestClient(app)
    with client.websocket_connect("/review") as ws:
        ws.send_json(
            {
                "type": "submit_code",
                "code": "def f():\n    return 1\n",
                "language": "python",
            }
        )

        chunks: list[str] = []
        complete = None
        while True:
            msg = ws.receive_json()
            if msg["type"] == "review_chunk":
                chunks.append(msg["chunk"])
            elif msg["type"] == "tool_call":
                pass
            elif msg["type"] == "review_complete":
                complete = msg["metadata"]
                break
            elif msg["type"] == "error":
                pytest.fail(f"unexpected error: {msg}")

        assert "".join(chunks) == review_text
        assert complete is not None
        assert complete["issues_count"] == 1
        assert complete["cache_hit"] is False


def test_websocket_with_tool_call(ws_setup):
    """WebSocket review flow with one tool call then completion."""
    pre = "Checking docs."
    final = "OK. [ISSUE:naming:1] bad."

    ws_setup["patch_anthropic"](
        [
            make_tool_use_stream(
                pre_text=pre,
                tool_name="lookup_documentation",
                tool_input={"language": "python", "topic": "naming conventions"},
            ),
            make_text_stream(final),
        ]
    )

    client = TestClient(app)
    with client.websocket_connect("/review") as ws:
        ws.send_json(
            {
                "type": "submit_code",
                "code": "def F():\n    return 1\n",
                "language": "python",
            }
        )

        tool_events = []
        chunks = []
        complete = None
        while True:
            msg = ws.receive_json()
            if msg["type"] == "review_chunk":
                chunks.append(msg["chunk"])
            elif msg["type"] == "tool_call":
                tool_events.append(msg)
            elif msg["type"] == "review_complete":
                complete = msg["metadata"]
                break
            elif msg["type"] == "error":
                pytest.fail(f"unexpected error: {msg}")

        assert pre in "".join(chunks)
        assert final in "".join(chunks)
        assert len(tool_events) == 1
        assert tool_events[0]["tool"] == "lookup_documentation"
        assert "snake_case" in tool_events[0]["result"]
        assert complete["issues_count"] == 1


def test_websocket_invalid_first_message(ws_setup):
    client = TestClient(app)
    with client.websocket_connect("/review") as ws:
        ws.send_text("not-json")
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_websocket_validation_error_unsupported_language(ws_setup):
    ws_setup["patch_anthropic"]([])
    client = TestClient(app)
    with client.websocket_connect("/review") as ws:
        ws.send_json(
            {"type": "submit_code", "code": "x = 1", "language": "fortran"}
        )
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "Unsupported language" in msg["error"] or "fortran" in msg["error"]
