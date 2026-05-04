"""Pytest fixtures for the code review assistant.

- In-memory SQLite (aiosqlite) replaces Postgres for unit tests.
- FakeRedis replaces real Redis (no docker dependency).
- The Anthropic SDK is patched with a fake AsyncAnthropic that exposes a
  scriptable streaming interface — see fake_anthropic_client below.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.redis_client import get_redis


# --- Async event loop ---
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# --- Test database (SQLite via aiosqlite) ---
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_async_session = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture(autouse=True)
async def _setup_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with test_async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# --- Fake Redis (in-memory) ---
class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass


_fake_redis = FakeRedis()


async def _override_get_redis() -> AsyncGenerator[FakeRedis, None]:
    yield _fake_redis


# --- Apply dependency overrides at import time ---
app.dependency_overrides[get_db] = _override_get_db
app.dependency_overrides[get_redis] = _override_get_redis


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def fake_redis() -> FakeRedis:
    """A fresh FakeRedis instance for direct service-level tests."""
    return FakeRedis()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A direct DB session for service-level tests."""
    async with test_async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------- Fake Anthropic streaming SDK ---------------- #


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeBlock:
    """Mimics anthropic content blocks (text or tool_use)."""

    def __init__(self, type: str, **kwargs: Any) -> None:
        self.type = type
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeFinalMessage:
    def __init__(
        self,
        content: list[_FakeBlock],
        stop_reason: str,
        input_tokens: int = 100,
        output_tokens: int = 50,
    ) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeDelta:
    def __init__(self, type: str, text: str = "") -> None:
        self.type = type
        self.text = text


class _FakeEvent:
    def __init__(self, type: str, delta: _FakeDelta | None = None) -> None:
        self.type = type
        if delta is not None:
            self.delta = delta


class FakeStream:
    """An async context manager that yields scripted events.

    Iteration yields events; get_final_message() returns the configured
    FakeFinalMessage. Multiple iterations (one per tool-loop turn) are
    supported by the FakeAnthropicClient — it pops scripts off a list.
    """

    def __init__(self, events: list[_FakeEvent], final: _FakeFinalMessage) -> None:
        self._events = events
        self._final = final

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def __aiter__(self):
        async def _gen():
            for ev in self._events:
                yield ev
        return _gen()

    async def get_final_message(self) -> _FakeFinalMessage:
        return self._final


class FakeMessages:
    def __init__(self, scripts: list[FakeStream]) -> None:
        self._scripts = scripts

    def stream(self, **kwargs: Any) -> FakeStream:
        if not self._scripts:
            raise RuntimeError("No more scripted streams available")
        return self._scripts.pop(0)


class FakeAnthropicClient:
    """Drop-in replacement for anthropic.AsyncAnthropic with scripted streams."""

    def __init__(self, scripts: list[FakeStream]) -> None:
        self.messages = FakeMessages(scripts)


def make_text_stream(text: str, input_tokens: int = 100, output_tokens: int = 50) -> FakeStream:
    """Build a FakeStream that emits `text` in two chunks then stops with end_turn."""
    half = len(text) // 2 or 1
    events = [
        _FakeEvent("content_block_delta", delta=_FakeDelta("text_delta", text[:half])),
        _FakeEvent("content_block_delta", delta=_FakeDelta("text_delta", text[half:])),
    ]
    final = _FakeFinalMessage(
        content=[_FakeBlock("text", text=text)],
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return FakeStream(events, final)


def make_tool_use_stream(
    pre_text: str,
    tool_name: str,
    tool_input: dict,
    tool_use_id: str = "tool_1",
    input_tokens: int = 80,
    output_tokens: int = 30,
) -> FakeStream:
    """Build a FakeStream that emits some text then a tool_use stop_reason."""
    events = [
        _FakeEvent("content_block_delta", delta=_FakeDelta("text_delta", pre_text)),
    ]
    final = _FakeFinalMessage(
        content=[
            _FakeBlock("text", text=pre_text),
            _FakeBlock("tool_use", id=tool_use_id, name=tool_name, input=tool_input),
        ],
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return FakeStream(events, final)


@pytest.fixture
def patch_anthropic():
    """Patch anthropic.AsyncAnthropic to return a FakeAnthropicClient.

    Usage:
        def test_x(patch_anthropic):
            patch_anthropic([make_text_stream("hello"), ...])
            # call the code under test
    """
    def _factory(scripts: list[FakeStream]):
        client = FakeAnthropicClient(list(scripts))
        return patch("anthropic.AsyncAnthropic", return_value=client)

    started = []

    def install(scripts: list[FakeStream]):
        p = _factory(scripts)
        started.append(p.start())
        return started[-1]

    yield install

    # Stop any started patches in reverse order
    for _ in range(len(started)):
        try:
            patch.stopall()
            break
        except Exception:
            continue
