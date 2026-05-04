# Real-Time Code Review Assistant

A WebSocket-based code review service powered by Anthropic Claude with **streaming responses**, a **manually-implemented tool-calling loop** (no LangChain), and **Redis caching** of identical submissions.

This is **Project 2** in a five-project AI-engineering portfolio. It builds on Project 1 (ticket classifier) by adding real-time WebSocket UX, multi-turn tool calling, and a small browser demo client.

---

## What it does

1. A user pastes a code snippet into the browser demo (or any WebSocket client).
2. The client opens a WebSocket to `/review` and sends `{type: "submit_code", code, language, style_guide?}`.
3. The server:
   - hashes `(code + language)` and checks Redis for a cached review (1 h TTL),
   - if miss, opens a streaming chat with Claude and registers two tools: `lookup_documentation` and `check_style_guide`,
   - streams Claude's tokens straight to the browser as they arrive,
   - whenever Claude requests a tool, executes it locally, sends the result back to the model, and continues the loop until Claude stops naturally,
   - persists the final review + each tool call to PostgreSQL, updates daily cost counters, caches the review.
4. The browser renders the streaming markdown live, with `[ISSUE:type:line]` markers highlighted and tool-call events shown inline.

---

## Architecture

```
                       ┌────────────────────┐
                       │  Browser client    │
                       │  app/static/       │
                       │   index.html       │
                       └─────────┬──────────┘
                                 │ WebSocket /review
                                 ▼
┌────────────────────────────────────────────────────────────────┐
│  FastAPI (app/main.py)                                         │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Routers                                                 │  │
│  │   - health.py          GET /health                       │  │
│  │   - reviews.py         WS  /review                       │  │
│  │                        GET /reviews                      │  │
│  │                        GET /reviews/{id}                 │  │
│  │                        POST /reviews/{id}/export         │  │
│  │   - analytics.py       GET /analytics/summary            │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Services                                                │  │
│  │   - reviewer.py    manual tool-calling loop + streaming  │  │
│  │   - tools.py       lookup_documentation / check_style    │  │
│  │   - cost.py        token → USD, daily aggregate, limit   │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Utils: logging (JSON), exceptions (global handlers)     │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────┬─────────────────────────────┬─────────────────────────┘
         │                             │
         ▼                             ▼
   PostgreSQL                       Redis                Anthropic
   (reviews,                        (review cache,       Claude API
    tool_calls,                      hash-keyed)         (sonnet)
    api_costs)
```

---

## Project layout

```
project-2-code-review-assistant/
├── app/
│   ├── main.py              FastAPI entry — lifespan, CORS, rate limit, routers
│   ├── config.py            pydantic-settings (.env + env vars)
│   ├── database.py          async SQLAlchemy engine + session factory
│   ├── redis_client.py      async Redis pool + dependency
│   ├── models/              SQLAlchemy models (Review, ToolCall, ApiCost)
│   ├── schemas/             Pydantic v2 request/response schemas
│   ├── services/
│   │   ├── reviewer.py      ★ manual tool-calling loop + streaming
│   │   ├── tools.py         tool definitions + local implementations
│   │   └── cost.py          token-cost math, daily aggregate, limit gate
│   ├── routers/
│   │   ├── health.py        GET /health
│   │   ├── reviews.py       WS /review, REST listing, markdown export
│   │   └── analytics.py     GET /analytics/summary
│   ├── utils/
│   │   ├── logging.py       structured JSON logger w/ request_id
│   │   └── exceptions.py    AppError types + FastAPI handlers
│   └── static/
│       └── index.html       Browser WebSocket demo client
├── tests/                   pytest + httpx + Starlette TestClient WebSockets
├── requirements.txt
├── pyproject.toml           ruff + mypy + pytest config
├── docker-compose.yml       api + postgres + redis
├── Dockerfile               python:3.11-slim
├── .env.example
└── README.md
```

---

## API

| Method | Path                          | Purpose                                            |
|--------|-------------------------------|----------------------------------------------------|
| GET    | `/health`                     | DB + Redis connectivity                            |
| GET    | `/`                           | Browser demo client (HTML)                         |
| WS     | `/review`                     | Submit code, receive streaming review              |
| GET    | `/reviews`                    | List past reviews (pagination, language filter)    |
| GET    | `/reviews/{id}`               | Get one review with its tool calls                 |
| POST   | `/reviews/{id}/export`        | Export review as markdown                          |
| GET    | `/analytics/summary`          | Per-language stats + monthly cost summary          |
| GET    | `/docs`                       | Swagger UI                                         |

### WebSocket protocol

**Client → server (first message):**
```json
{
  "type": "submit_code",
  "code": "def Process_Data(items): ...",
  "language": "python",
  "style_guide": "pep8"
}
```

**Server → client:**
```json
{ "type": "review_chunk", "chunk": "Looks good. [ISSUE:naming:1]…" }
{ "type": "tool_call", "tool": "lookup_documentation",
  "input": {"language": "python", "topic": "naming conventions"},
  "result": "PEP 8 — Naming Conventions: …" }
{ "type": "review_complete",
  "metadata": { "review_id": 12, "issues_count": 3,
                "tokens_used": 850, "cost": 0.0042,
                "cache_hit": false, "language": "python" } }
{ "type": "error", "error": "...", "detail": {...} }
```

---

## Run it locally (docker compose)

```bash
git clone <this repo>
cd project-2-code-review-assistant
cp .env.example .env
# edit .env — set ANTHROPIC_API_KEY=sk-ant-...

docker compose up -d
curl http://localhost:8000/health
open http://localhost:8000          # browser demo client
```

Stop with `docker compose down` (add `-v` to wipe Postgres data).

### Without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# bring up your own Postgres + Redis, then:
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/code_review
export REDIS_URL=redis://localhost:6379/0
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --reload
```

---

## Tests

```bash
pip install -r requirements.txt
pytest
```

The test suite mocks the Anthropic SDK with a **scriptable fake stream** (see [tests/conftest.py](tests/conftest.py)) so no real API calls are made and tests run in under 5 seconds. SQLite (aiosqlite) replaces Postgres for unit tests.

Coverage:
- Health & root endpoints
- Tool implementations (`lookup_documentation`, `check_style_guide`)
- Reviewer flow: validation, single-turn streaming, multi-turn tool-calling loop, cache hit
- REST endpoints (list / get / export / analytics)
- WebSocket flow: simple review, tool-call review, validation errors, malformed input

Current status: **31 / 31 passing**.

---

## Cross-cutting concerns

| Concern              | Implementation                                                                |
|----------------------|-------------------------------------------------------------------------------|
| Secrets              | `.env` + `pydantic-settings`, never hardcoded, `.env` in `.gitignore`         |
| Health check         | `GET /health` — DB + Redis ping                                               |
| Structured logging   | `python-json-logger`, every request gets a `request_id` in headers + logs    |
| Rate limiting        | `slowapi`, default 20/min, configurable via `RATE_LIMIT`                      |
| Error handling       | Global handlers in [app/utils/exceptions.py](app/utils/exceptions.py)         |
| Caching              | Redis SHA-256 hash of `(language, code)`, 1 h TTL                             |
| Cost control         | Per-call cost in [app/services/cost.py](app/services/cost.py:23), daily agg, `CostLimitExceededError` |
| Container parity     | `docker compose` runs the same image used in production                       |

---

## Why this project is portfolio-worthy

- **Streaming over WebSocket** — real low-latency UX, not REST polling.
- **Manual tool-calling loop** — parsing model output, dispatching tools, re-submitting tool results. No LangChain, no abstraction tax. The loop lives in [app/services/reviewer.py](app/services/reviewer.py).
- **Production guardrails** — daily cost limit, structured logs with request IDs, global exception handler, rate limiter, health probe.
- **Fully testable** — fake Anthropic stream, in-memory SQLite, in-memory Redis, no network in tests.

---

## Talking points for interviews

- "I implemented streaming WebSocket reviews — tokens go from Claude to the browser with no buffering layer."
- "I built the tool-calling loop manually. It's a small state machine: stream → check stop_reason → if `tool_use`, dispatch tools, append the `tool_result` user message, loop. The whole thing fits in [reviewer.py](app/services/reviewer.py)."
- "Identical snippets hit Redis instead of the API — content-hashed cache key, 1 h TTL, persisted hit-rate in `api_costs.cache_hit_count`."
- "Tests use a scripted fake Anthropic stream so I can assert against a deterministic multi-turn flow without burning credits or being flaky."
# fastapi-LLM-code-review-assistant
