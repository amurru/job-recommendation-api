# Task Tracking Board

> Single source of truth for implementation progress. Check off items as they
> land. Keep status and notes current.

Legend: `[ ]` pending · `[~]` in progress · `[x]` complete · `[!]` blocked

## Phase 0 - Foundation

- [x] **ID-001** Scaffold deps in `pyproject.toml` (fastapi, uvicorn, python-multipart, pydantic-settings, markitdown[pdf], openrouter; dev: pytest, httpx, ruff, mypy). `uv sync` + import smoke test green. Fix `[project.scripts]` to point to `job_recommendation_api.main:main`.
  - Priority: `high` · Depends: - · Status: `complete`
- [x] **ID-016** Add ruff + mypy config, pre-commit hooks, lint/typecheck/test commands. **Must complete before Phase 1** so rules apply as code lands.
  - Priority: `high` · Depends: ID-001 · Status: `complete` · Notes: added `.pre-commit-config.yaml` (ruff lint, ruff format, mypy) and `Makefile` targets (lint/format/typecheck/test/run); `pre-commit` added to dev group.

## Phase 1 - Contracts & layers

- [x] **ID-002** `config.py` Settings (pydantic-settings): api key, model, timeouts, upload cap; `load_settings()` factory (no `lru_cache`; NOT a FastAPI dependency - the `get_settings` dependency lives in `deps.py`/ID-013); `.env.example` with loading-order note.
  - Priority: `high` · Depends: ID-001 · Status: `complete`
- [x] **ID-004** `errors.py` domain exception hierarchy with stable `code`s.
  - Priority: `high` · Depends: ID-001 · Status: `complete`
- [x] **ID-003** `schemas/recommendation.py` Pydantic models (with `seniority_level` enum + `top_skills` length constraints) + two JSON Schema exports: `ResumeAnalysis.model_json_schema()` for LLM, `RecommendationResponse.model_json_schema()` for API docs; validation unit test.
  - Priority: `high` · Depends: ID-001 · Status: `complete`

## Phase 2 - Infrastructure adapters

- [x] **ID-005** `services/document_converter.py` markitdown PDF->markdown via `convert_stream` + `StreamInfo`; **new `MarkItDown()` per call** (not shared); empty/type guards; unit test with tiny PDF.
  - Priority: `high` · Depends: ID-001, ID-004 · Status: `complete`
- [x] **ID-006** `llm/client.py` `LLMClient` Protocol (async) + `OpenRouterLLMClient` using `send_async` + `ChatFormatJSONSchemaConfig`; retry with backoff for 429/5xx; model fallback to `FormatJSONObjectConfig`; fake-SDK unit test.
  - Priority: `high` · Depends: ID-002, ID-004 · Status: `complete`
- [x] **ID-007** `services/prompts.py` system/user prompt + `RECOMMENDATION_SCHEMA`; unit test prompt embeds resume.
  - Priority: `high` · Depends: ID-003 · Status: `complete`

## Phase 3 - Business + HTTP

- [x] **ID-008** `services/recommendation.py` async orchestration (threadpool converter -> await llm -> validate + meta); unit test with fakes.
  - Priority: `high` · Depends: ID-003, ID-004, ID-005, ID-006, ID-007 · Status: `complete`
- [x] **ID-012** `api/errors.py` exception handlers -> HTTP envelopes; mapping tests.
  - Priority: `high` · Depends: ID-004 · Status: `complete`
- [x] **ID-009** `main.py` `create_app()` + lifespan (build singletons on `app.state`, graceful shutdown closes LLM client) + request-id middleware (contextvars propagation) + logging middleware; `__main__.py`.
  - Priority: `high` · Depends: ID-002, ID-012 · Status: `complete`
- [x] **ID-013** `api/deps.py` dependency accessors (`get_settings`, `get_converter`, `get_llm_client`, `get_recommendation_service`).
  - Priority: `medium` · Depends: ID-002, ID-005, ID-006, ID-008, ID-009 · Status: `complete`
- [x] **ID-010** `api/routes/health.py` `/healthz`, `/readyz`; unit test.
  - Priority: `medium` · Depends: ID-002, ID-009 · Status: `complete`
- [x] **ID-011** `api/routes/recommendations.py` `POST /api/v1/recommendations` multipart upload, size/type validation (document python-multipart spool behavior), async service call.
  - Priority: `high` · Depends: ID-008, ID-012, ID-013 · Status: `complete`

## Phase 4 - Verification

- [x] **ID-014** Unit tests: config, schemas, errors, converter, llm client, prompts, service.
  - Priority: `high` · Depends: ID-002..ID-008 · Status: `complete`
- [x] **ID-015** API integration tests: health, recommendation 200/415/413/422, error envelope.
  - Priority: `high` · Depends: ID-009..ID-013 · Status: `complete`

## Phase 5 - Release docs

- [x] **ID-017** README quickstart + endpoint table, `.env.example`, `curl` example, `.gitignore` `.env`.
  - Priority: `medium` · Depends: ID-009..ID-011 · Status: `complete`

## Blockers / Notes

<!-- Record blockers here with date + spec ID. e.g.
2026-08-20: ID-006 blocked on confirming openrouter SDK response_format type.
-->

### Resolved

- **2026-08-20**: OpenRouter SDK `response_format` shape confirmed:
  `ChatFormatJSONSchemaConfig(type="json_schema", json_schema=ChatJSONSchemaConfig(name=..., schema_=..., strict=True))`.
  Async via `client.chat.send_async(...)`. No longer an open question.
- **2026-08-20**: markitdown `MarkItDown()` is NOT thread-safe (internal
  `requests.Session`). Decision: create per-call in `MarkItDownConverter`.