# Architecture

## Directory layout (target)

```
job-recommendation-api/
├── pyproject.toml            # deps + tooling (uv_build)
├── .python-version           # 3.14
├── .env.example              # documented config (no secrets)
├── README.md
├── docs/                     # this planning/tracking set
├── src/job_recommendation_api/
│   ├── __init__.py
│   ├── __main__.py           # python -m job_recommendation_api
│   ├── main.py               # create_app() + uvicorn runner
│   ├── config.py             # pydantic-settings Settings
│   ├── errors.py             # domain exception hierarchy
│   ├── api/
│   │   ├── deps.py           # FastAPI dependencies
│   │   ├── errors.py         # exception -> HTTP handlers
│   │   ├── router.py         # /api/v1 aggregation
│   │   └── routes/
│   │       ├── health.py
│   │       └── recommendations.py
│   ├── schemas/
│   │   └── recommendation.py # Pydantic models + JSON Schema export
│   ├── services/
│   │   ├── document_converter.py
│   │   ├── prompts.py
│   │   └── recommendation.py
│   └── llm/
│       └── client.py         # LLMClient Protocol + OpenRouter impl
└── tests/
    ├── conftest.py
    ├── unit/
    └── api/
```

## Layer rules (one-way dependency)

```
api (routes, deps, handlers)
  -> services (business orchestration)
       -> llm / schemas / errors (adapters + contracts)
       -> services.document_converter
```

- `api` never imports concrete third-party clients directly.
- `services` never imports FastAPI/HTTP objects.
- `schemas`/`errors` are pure (no I/O).

## Data flow

```
Client (multipart PDF)
  |
  v
POST /api/v1/recommendations  (async route)
  |  validate: content-type=application/pdf, size <= cap
  v
await RecommendationService.recommend(...)  (async method)
  |  1. await run_in_threadpool(converter.convert, bytes)  -> markdown str
  |     [markitdown sync, offloaded to threadpool; MarkItDown() created per-call]
  |  2. build messages: system + user (services/prompts.py)
  |  3. await llm.complete(messages, schema)  -> dict
  |     [openrouter SDK send_async, native async, with retry+backoff]
  |  4. RecommendationResponse.model_validate(dict + meta)
  v
200 JSON: { analysis: { summary, top_skills, jobs[], education_materials[] }, meta }
```

Only step 1 (PDF parsing) is genuinely blocking and runs in a threadpool.
Step 3 (LLM call) is async-native via the OpenRouter SDK's `send_async` and
runs directly on the event loop. The route handler awaits the service directly
- no threadpool wrapping at the route level.

## Architecture decisions

### ADR-001: Layered + Protocol boundaries
The two external systems (markitdown, OpenRouter) are wrapped behind
`typing.Protocol` interfaces (`DocumentConverter`, `LLMClient`) so:
- Unit tests inject fakes without network/keys.
- SDK upgrades are confined to one file each.
- The orchestration service is testable in isolation.

### ADR-002: App factory + lifespan DI
`create_app(settings=None)` returns a `FastAPI` instance. Lifespan constructs
the converter, LLM client, and service once and stores them on `app.state`;
routes read them via `Depends`. No module-level singletons, so tests can
construct isolated apps and override any dependency. The lifespan's shutdown
phase closes the OpenRouter client (`await client.close()` or exiting the
`async with` context) to release HTTP connections cleanly.

### ADR-003: Structured output + Pydantic re-validation (defense in depth)
The LLM is asked for `response_format` `json_schema` (strict). Regardless of
the provider guarantee, the returned text is parsed and re-validated through
the same Pydantic models that define the HTTP response. Any drift is caught
deterministically, not left to a brittle `json.loads`.

### ADR-004: Async LLM + threadpool only for blocking I/O
The OpenRouter Python SDK provides a native async client (`send_async`). The
LLM client Protocol is `async def complete(...)`, and the orchestration service
is `async def recommend(...)`. Only the markitdown PDF converter is genuinely
synchronous; it runs in a worker thread via `run_in_threadpool` so the event
loop stays free for other requests and health checks. The route handler awaits
the service directly - no threadpool wrapping at the route level.

### ADR-005: Typed errors mapped at the edge
Domain code raises `AppError` subclasses (transport-agnostic). A single
exception-handler layer in `api/errors.py` maps them to HTTP codes and a
uniform `{"error": {"code", "message"}}` envelope. HTTP concerns never leak
into services.

### ADR-006: Config as code, secrets via env only
All tunables live in a `pydantic-settings` model (`config.py`), sourced from
environment / `.env`. The API key is `SecretStr` (masked in repr/logs) and is
never committed. The app does not hard-fail at import on a missing key; it
fails on request or signals unready via `/readyz`.

## Cross-cutting concerns

- **Logging**: structured (JSON) logger; request-id middleware adds
  `X-Request-ID` to responses and stores it in a `contextvars.ContextVar` so
  service-layer and LLM-client logs automatically include the correlation id
  without explicit parameter passing. Never log the API key or raw resume
  content.
- **Observability**: `/healthz` (liveness), `/readyz` (readiness, checks key
  presence). No metrics exporter this iteration.
- **Timeouts**: LLM calls bounded by `llm_timeout_seconds`; uploads bounded by
  `max_upload_bytes`.
- **Retries**: LLM calls retry with exponential backoff (max 2 retries, starting
  at 1s) for transient errors: HTTP 429, 502, 503, 504, and connection errors.
  No retry on 4xx (except 429) or on `LLMInvalidOutputError`.
- **MarkItDown lifecycle**: a new `MarkItDown()` instance is created per
  conversion call. The class initializes an internal `requests.Session` that is
  not thread-safe for concurrent use; per-call instantiation avoids this.

## Changelog

| Date | Change |
|------|--------|
| 2026-08-20 | Initial architecture: layered design, ADRs 001-006. |
| 2026-08-20 | ADR-004 revised: async LLM client (native `send_async`), threadpool only for markitdown. Added contextvars for request-id propagation. Added retry/backoff and MarkItDown per-call lifecycle to cross-cutting concerns. ADR-002 updated with graceful shutdown. |
| 2026-08-20 | Clarified LLM prompt boundary: the recommendation service assembles system + user messages (from `services/prompts.py`) and passes them to a prompt-agnostic `LLMClient.complete(messages, schema)`; `llm/client.py` does NOT depend on `services/prompts.py`. |
