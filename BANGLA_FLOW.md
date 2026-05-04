# প্রজেক্ট ২ — Real-Time Code Review Assistant (বাংলা ফ্লো ডকুমেন্টেশন)

এই ডকুমেন্টে পুরো প্রজেক্টটা **কোন ফাইল কী কাজ করে**, **রিকুয়েস্ট আসলে কোন পথে যায়**, এবং কোডের প্রতিটা লেয়ার একসাথে কীভাবে কাজ করে — সব **বাংলায়** ব্যাখ্যা করা আছে।

---

## ১. প্রজেক্টটা আসলে কী?

ব্যবহারকারী একটা কোড স্নিপেট পাঠায়, আর Claude (Anthropic-এর LLM) সেটা স্ট্রিমিং রিভিউ করে। দুইটা টুল আছে যেগুলো Claude নিজে কল করতে পারে — `lookup_documentation` (ভাষার ডকুমেন্টেশন দেখা) আর `check_style_guide` (style guide চেক করা)। পুরো রিভিউ **WebSocket** দিয়ে রিয়েল-টাইমে ব্রাউজারে আসে।

মূল feature:
- **WebSocket স্ট্রিমিং** — Claude যখন token দেয়, তখনই ব্রাউজার দেখায়, REST polling নেই।
- **Manual tool-calling loop** — LangChain ব্যবহার না করে নিজেই Anthropic-এর response parse করে tool call করি এবং result ফেরত পাঠাই।
- **Redis cache** — একই কোড + ভাষা ১ ঘণ্টার মধ্যে আবার আসলে API call স্কিপ হয়।
- **Cost tracking** — প্রতিদিনের cost log হয়, লিমিট পেরোলে নতুন request block হয়।
- **Production-ready** — structured JSON log, health check, rate limit, global exception handler, Docker Compose।

---

## ২. ডিরেক্টরি স্ট্রাকচার (পুরো প্রজেক্ট)

```
project-2-code-review-assistant/
├── app/
│   ├── main.py              ← FastAPI অ্যাপ্লিকেশন এন্ট্রি পয়েন্ট
│   ├── config.py            ← .env থেকে settings পড়া
│   ├── database.py          ← async SQLAlchemy engine + session
│   ├── redis_client.py      ← async Redis pool
│   ├── models/              ← SQLAlchemy ORM মডেল
│   │   ├── review.py        ← Review টেবিল
│   │   ├── tool_call.py     ← ToolCall টেবিল
│   │   └── api_cost.py      ← ApiCost টেবিল (দৈনিক cost)
│   ├── schemas/             ← Pydantic request/response মডেল
│   │   ├── review.py
│   │   ├── tool_schemas.py
│   │   └── analytics.py
│   ├── services/            ← ★ মূল business logic এখানে
│   │   ├── reviewer.py      ← ★★ manual tool-calling loop + streaming
│   │   ├── tools.py         ← tool definition + local implementation
│   │   └── cost.py          ← token → USD হিসাব, daily aggregate
│   ├── routers/             ← FastAPI route handler
│   │   ├── health.py        ← GET /health
│   │   ├── reviews.py       ← WS /review + REST endpoints
│   │   └── analytics.py     ← GET /analytics/summary
│   ├── utils/
│   │   ├── logging.py       ← JSON log setup
│   │   └── exceptions.py    ← global error handler
│   └── static/
│       └── index.html       ← ব্রাউজার ডেমো ক্লায়েন্ট (HTML+JS)
├── tests/                   ← pytest টেস্ট সুট
│   ├── conftest.py          ← fixture (fake Anthropic, FakeRedis, SQLite)
│   ├── test_health.py
│   ├── test_tools.py
│   ├── test_reviewer.py
│   ├── test_routes.py
│   └── test_websocket.py
├── requirements.txt
├── pyproject.toml           ← ruff + mypy + pytest config
├── docker-compose.yml       ← api + postgres + redis
├── Dockerfile
├── .env.example
├── README.md
└── BANGLA_FLOW.md           ← এই ফাইল
```

---

## ৩. প্রতিটা ফাইলের কাজ (এক লাইনে)

### Entry layer

| ফাইল | কাজ |
|------|-----|
| [app/main.py](app/main.py) | FastAPI অ্যাপ তৈরি করে, middleware বসায় (CORS, rate limit, request log), router mount করে, lifespan-এ DB তৈরি করে, এবং `/static` mount করে ডেমো HTML serve করে। |
| [app/config.py](app/config.py) | `.env` থেকে DATABASE_URL, REDIS_URL, ANTHROPIC_API_KEY ইত্যাদি load করে। `pydantic-settings` ব্যবহার করে — কখনো hardcoded key নয়। |
| [app/database.py](app/database.py) | async SQLAlchemy engine, `async_sessionmaker`, `get_db()` dependency, `init_db()` (টেবিল তৈরি), `close_db()`। |
| [app/redis_client.py](app/redis_client.py) | async Redis connection pool, `get_redis()` dependency, `close_redis()`। |

### Models — ডেটাবেসের টেবিল

| ফাইল | টেবিল | কী রাখে |
|------|--------|---------|
| [app/models/review.py](app/models/review.py) | `reviews` | প্রতিটা code review — code, language, review_text, issues_count, tokens_used, cost, cache_hit, timestamps |
| [app/models/tool_call.py](app/models/tool_call.py) | `tool_calls` | প্রতিটা review-এর সাথে কতগুলো tool call হয়েছিল — tool_name, input (JSON), result |
| [app/models/api_cost.py](app/models/api_cost.py) | `api_costs` | প্রতিদিনের aggregate cost — date, total_cost, request_count, cache_hit_count |

### Schemas — request/response shape

| ফাইল | কাজ |
|------|-----|
| [app/schemas/review.py](app/schemas/review.py) | `CodeSubmission` (input), `ReviewResponse`, `ReviewListResponse`, `ReviewExportResponse` |
| [app/schemas/tool_schemas.py](app/schemas/tool_schemas.py) | `DocumentationLookup`, `StyleGuideCheck`, `ToolResult`, `CostInfo` |
| [app/schemas/analytics.py](app/schemas/analytics.py) | `LanguageStat`, `CostSummary`, `AnalyticsSummary` |

### Services — মূল লজিক

| ফাইল | কাজ |
|------|-----|
| [app/services/tools.py](app/services/tools.py) | দুইটা tool-এর definition (Claude-কে যা পাঠাই) + locally execute করার ফাংশন। `lookup_documentation()` Python/JS/TS-এর জন্য PEP/MDN-এর সারাংশ দেয়; `check_style_guide()` regex দিয়ে line length, naming, docstring, indentation ইত্যাদি চেক করে। |
| [app/services/reviewer.py](app/services/reviewer.py) | **★★ পুরো প্রজেক্টের হৃদয়।** validation → cache check → manual tool-calling loop with streaming → DB save → Redis cache → cost log। |
| [app/services/cost.py](app/services/cost.py) | `calculate_cost()` token → USD; `log_api_cost()` daily aggregate update; `check_cost_limit()` লিমিট পেরোলে exception; `get_monthly_cost_summary()` analytics-এর জন্য। |

### Routers — HTTP/WS endpoint

| ফাইল | endpoint | কাজ |
|------|----------|-----|
| [app/routers/health.py](app/routers/health.py) | `GET /health` | DB ping + Redis ping → JSON status |
| [app/routers/reviews.py](app/routers/reviews.py) | `WS /review` | কোড নিয়ে streaming review পাঠায় (এই ফাইলের মূল কাজ) |
| | `GET /reviews` | পুরনো রিভিউ list (pagination, language filter) |
| | `GET /reviews/{id}` | এক রিভিউ + তার tool_calls |
| | `POST /reviews/{id}/export` | markdown ফরম্যাটে export |
| [app/routers/analytics.py](app/routers/analytics.py) | `GET /analytics/summary` | per-language stats + monthly cost summary |

### Utils

| ফাইল | কাজ |
|------|-----|
| [app/utils/logging.py](app/utils/logging.py) | `python-json-logger` দিয়ে structured JSON log। প্রতিটা request-এ `request_id` context-এ থাকে এবং সব log line-এ inject হয়। |
| [app/utils/exceptions.py](app/utils/exceptions.py) | `AppError`, `ReviewError`, `CostLimitExceededError`, `ValidationFailureError` + FastAPI global handler। Stack trace কখনো client-কে দেওয়া হয় না। |

### Frontend

| ফাইল | কাজ |
|------|-----|
| [app/static/index.html](app/static/index.html) | এক-পেজ HTML+JS ডেমো ক্লায়েন্ট। Code editor, language picker, "Review" button — WebSocket দিয়ে server-এর `/review` endpoint-এ connect করে এবং chunk আসলে live render করে। |

---

## ৪. পুরো রিকুয়েস্ট ফ্লো — কোন ফাইল কখন কাজ করে

ধরো ব্যবহারকারী ব্রাউজারে কোড paste করে "Review" বাটন চাপলো। কী হয়:

```
[Browser]                                               [Server]
   │
   │  1. WebSocket /review খোলে
   │ ─────────────────────────────────────────────► [main.py middleware]
   │                                                   request_id তৈরি হয়
   │                                                   logging_middleware log করে
   │                                                       │
   │                                                       ▼
   │                                                [routers/reviews.py
   │                                                 websocket_review()]
   │                                                       │
   │  2. submit_code message পাঠায়                         │
   │ ─────────────────────────────────────────────►       │
   │     {code, language, style_guide?}                   │
   │                                                       ▼
   │                                                [services/reviewer.py
   │                                                 review_code()]
   │                                                       │
   │                                                ┌──────┴──────┐
   │                                                ▼             ▼
   │                                          validate_       cache check
   │                                          submission     (Redis SHA-256)
   │                                                │             │
   │                                                │       cache hit হলে ↓
   │   3a. cache hit হলে — স্টোরড টেক্সট পুরো         │   ◄──────────┘
   │ ◄─────────────────────────────────────────────       cached text পাঠাও
   │       review_chunk + review_complete                       
   │                                                       │
   │                                                cache miss হলে ↓
   │                                                       ▼
   │                                                check_cost_limit()
   │                                                (services/cost.py)
   │                                                       │
   │                                          লিমিট পেরোলে CostLimitExceededError → error frame
   │                                                       │
   │                                                       ▼
   │                                                Anthropic API
   │                                                client.messages.stream(
   │                                                  tools=TOOL_DEFINITIONS,
   │                                                  ...
   │                                                )
   │                                                       │
   │   4. Claude থেকে token-by-token text আসছে,             │
   │      প্রতিটা chunk on_chunk callback-এ যাচ্ছে,           │
   │      callback সরাসরি WebSocket-এ পাঠাচ্ছে               │
   │ ◄─────────────────────────────────────────────       review_chunk × N
   │                                                       │
   │                                                stop_reason চেক:
   │                                                       │
   │                                          ┌────────────┴────────────┐
   │                                          ▼                         ▼
   │                                   stop_reason ==              stop_reason ==
   │                                    "end_turn"                  "tool_use"
   │                                          │                         │
   │                                       break loop          ┌────────┴────────┐
   │                                          │                ▼                 ▼
   │                                          │         _execute_tool()    on_tool_call
   │                                          │         (services/tools.py)  callback
   │                                          │                │                 │
   │                                          │                │                 ▼
   │   5. tool_call event ◄───────────────────┼────────────────┼────── tool_call frame
   │      (tool name + input + result)        │                │      পাঠানো হয়
   │                                          │                ▼
   │                                          │       messages-এ assistant + 
   │                                          │       tool_result append করে
   │                                          │                │
   │                                          │       লুপের পরের iteration
   │                                          │       (নতুন stream() call)
   │                                          │                │
   │                                          ▼                │
   │                                    DB-তে Review +         │
   │                                    ToolCall save          │
   │                                          │                │
   │                                          ▼                │
   │                                    Redis-এ cache save     │
   │                                          │                │
   │                                          ▼                │
   │                                    log_api_cost()         │
   │                                    api_costs টেবিল update│
   │                                          │                │
   │                                          ▼                │
   │   6. review_complete frame ◄──────────────                │
   │      {review_id, issues_count, tokens, cost, cache_hit}   │
   │                                                           │
   │   7. WebSocket close                                      │
   │ ◄─────────────────────────────────────────────            │
```

---

## ৫. Manual tool-calling loop — কোডের ভিতরে কী হচ্ছে?

এই অংশটাই Project 2-এর সবচেয়ে গুরুত্বপূর্ণ skill। LangChain ব্যবহার না করে নিজে loop চালাচ্ছি।

[app/services/reviewer.py](app/services/reviewer.py) এর মূল লুপ:

```python
messages = [{"role": "user", "content": user_message}]

for iteration in range(MAX_TOOL_ITERATIONS):
    async with client.messages.stream(
        model=..., tools=TOOL_DEFINITIONS, messages=messages
    ) as stream:
        async for event in stream:
            if event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    # text chunk → callback → WebSocket
                    await on_chunk(event.delta.text)
        final_message = await stream.get_final_message()

    if final_message.stop_reason != "tool_use":
        break  # end_turn — কাজ শেষ

    # tool_use হলে: প্রতিটা tool execute করো
    assistant_blocks, tool_results = [], []
    for block in final_message.content:
        if block.type == "tool_use":
            result = _execute_tool(block.name, block.input)
            assistant_blocks.append({"type": "tool_use", "id": block.id, ...})
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

    messages.append({"role": "assistant", "content": assistant_blocks})
    messages.append({"role": "user", "content": tool_results})
    # লুপ আবার চলবে — নতুন stream() call
```

**কেন এভাবে?** LangChain এ এই pattern hidden — বুঝা যায় না কোথায় text আর কোথায় tool call। নিজে লিখলে exact বুঝা যায় Anthropic API কীভাবে কাজ করে, এবং কোনো আনচাহা abstraction নেই।

---

## ৬. Cache কীভাবে কাজ করে?

[app/services/reviewer.py:_cache_key()](app/services/reviewer.py)

```python
key = "review:" + sha256(language + ":" + code).hexdigest()[:16]
```

- প্রতিটা review শেষে: Redis-এ `key → JSON({review_text, issues_count})`, TTL 1 ঘণ্টা।
- নতুন request আসলে প্রথমে এই key চেক — থাকলে DB-তে নতুন Review row insert (cache_hit=True) এবং stored text-টা একসাথে on_chunk-এ পাঠাই।
- API call পুরো স্কিপ হয়, তাই tokens=0, cost=0।
- `api_costs.cache_hit_count` increment হয় → analytics-এ cache hit rate দেখা যায়।

---

## ৭. Cost tracking কীভাবে?

[app/services/cost.py](app/services/cost.py)

- প্রতিটা API call শেষে `log_api_cost(input_tokens, output_tokens, cache_hit)`।
- `calculate_cost()` per-1M pricing (settings থেকে আসে, hardcoded না) দিয়ে USD হিসাব।
- আজকের তারিখের `ApiCost` row আছে কিনা দেখে — থাকলে update, না থাকলে insert।
- প্রতিটা request-এর আগে `check_cost_limit()` কল হয় — `daily_total >= limit` হলে `CostLimitExceededError` (HTTP 429) raise।

---

## ৮. Tests — কীভাবে চালায়, কী করে

```bash
pip install -r requirements.txt
pytest
```

মোট **৩১টা টেস্ট, সবগুলো pass**। কোনো real Anthropic API call হয় না — [tests/conftest.py](tests/conftest.py)-এ `FakeAnthropicClient` আছে যেটা scripted stream return করে। SQLite (aiosqlite) দিয়ে in-memory DB, FakeRedis দিয়ে in-memory cache।

| ফাইল | কী টেস্ট করে |
|------|---------------|
| `test_health.py` | `/health` ও `/` endpoint |
| `test_tools.py` | tool definition + lookup_documentation + check_style_guide |
| `test_reviewer.py` | validation, single-turn streaming, multi-turn tool-calling, cache hit |
| `test_routes.py` | REST list / get / export / analytics |
| `test_websocket.py` | পুরো WebSocket flow — simple review, tool-call review, error case |

---

## ৯. লোকালি চালানোর ধাপ (Docker Compose)

```bash
git clone <repo>
cd project-2-code-review-assistant
cp .env.example .env
# .env-এ ANTHROPIC_API_KEY=sk-ant-... বসাও

docker compose up -d
# তিনটা container উঠবে: code-review-api, code-review-db, code-review-redis

curl http://localhost:8000/health
# {"status":"ok","db":"connected","redis":"connected",...}

# ব্রাউজারে ডেমো:
open http://localhost:8000
```

stack বন্ধ করতে: `docker compose down`। ডেটাবেস সহ মুছে ফেলতে: `docker compose down -v`।

---

## ১০. একনজরে — কোন ফাইল কোন কাজের জন্য

| কাজ | ফাইল |
|-----|------|
| FastAPI app create + middleware + lifespan | [app/main.py](app/main.py) |
| `.env` থেকে config load | [app/config.py](app/config.py) |
| async DB engine + session | [app/database.py](app/database.py) |
| async Redis pool | [app/redis_client.py](app/redis_client.py) |
| ORM মডেল | [app/models/](app/models/) |
| Pydantic schema | [app/schemas/](app/schemas/) |
| **Tool definition + execution** | [app/services/tools.py](app/services/tools.py) |
| **Manual tool-calling loop + streaming** | [app/services/reviewer.py](app/services/reviewer.py) |
| Cost calculation + daily aggregate | [app/services/cost.py](app/services/cost.py) |
| `/health` endpoint | [app/routers/health.py](app/routers/health.py) |
| `/review` WebSocket + `/reviews` REST | [app/routers/reviews.py](app/routers/reviews.py) |
| `/analytics/summary` | [app/routers/analytics.py](app/routers/analytics.py) |
| Structured JSON log | [app/utils/logging.py](app/utils/logging.py) |
| Global exception handler | [app/utils/exceptions.py](app/utils/exceptions.py) |
| Browser ডেমো ক্লায়েন্ট | [app/static/index.html](app/static/index.html) |
| টেস্ট fixture (fake Anthropic, FakeRedis) | [tests/conftest.py](tests/conftest.py) |

---

**সারসংক্ষেপ:** ব্যবহারকারী → `app/main.py` (middleware) → `app/routers/reviews.py` (WebSocket handler) → `app/services/reviewer.py` (tool-calling loop with streaming) → Anthropic API + `app/services/tools.py` (local tools) → DB ([app/models/](app/models/)) + Redis cache + cost log ([app/services/cost.py](app/services/cost.py)) → ব্রাউজারে live chunk → final metadata।
