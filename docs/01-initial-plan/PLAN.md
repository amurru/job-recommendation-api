# Development Blueprint: Job Recommendation API

## Context and Objective

Build a **no-authentication** FastAPI service that turns a resume PDF into
actionable career guidance. The request flow is:

```
POST multipart PDF -> markitdown (PDF -> Markdown) -> OpenRouter LLM
                   -> validate JSON -> RecommendationResponse
```

**What** (scope): a single accepting endpoint plus health/readiness checks.

**Why**: demonstrate production-grade Python service design with a clean
layered architecture, dependency injection, typed contracts, structured LLM
outputs, defensive validation, and full test coverage.

**Non-goals** (kept out deliberately, for this iteration):
- Authentication / authorization / rate limiting (explicitly requested "no auth").
- Persistent storage, databases, caching, job queues.
- PDF OCR for image-only PDFs (markitdown uses text extraction; scanned PDFs
  without a text layer are a known limitation - see Risks).
- Streaming/SSE responses (single-shot JSON response only).

### Assumptions (made explicit; see Open Questions for decisions to confirm)

| Assumption | Default | Rationale |
|------------|---------|-----------|
| Python runtime | 3.14 (existing `.python-version`) | Follow repo state |
| Packaging / deps | `uv` + `pyproject.toml` (uv_build) | Existing scaffold |
| Default model | `openai/gpt-4o-mini` | Cheap, broad structured-output support |
| File size cap | 10 MB | Reasonable resume upper bound |
| LLM timeout | 60 s | Generous for analysis; tune later |
| Response shape | `{ summary, jobs[], education_materials[] }` | Matches requirement wording |

## Repository Dependency Map

Current state: empty `uv` scaffold, no commits yet, no existing domain code.
Risk is therefore low everywhere; the map below is the **target** structure,
annotated with roles and dependencies in dependency order.

| Module / Path | Role | Depends On | Risk |
|---------------|------|-----------|------|
| `pyproject.toml` | Dependency + tooling config, scripts | - | Low |
| `src/job_recommendation_api/config.py` | `pydantic-settings` `Settings`, env-driven | `pydantic-settings` | Low |
| `src/job_recommendation_api/errors.py` | Domain exception hierarchy | - | Low |
| `src/job_recommendation_api/schemas/recommendation.py` | Pydantic response models + JSON Schema export | `pydantic` | Low |
| `src/job_recommendation_api/services/prompts.py` | Prompt templates + structured-output JSON Schema | schemas | Low |
| `src/job_recommendation_api/llm/client.py` | `LLMClient` Protocol (async) + `OpenRouterLLMClient` | `openrouter`, config, errors | Medium (third-party SDK shape) |
| `src/job_recommendation_api/services/document_converter.py` | `DocumentConverter` Protocol + markitdown impl | `markitdown`, errors | Medium (PDF edge cases) |
| `src/job_recommendation_api/services/recommendation.py` | Orchestration service | converter, llm, schemas, prompts, errors | Low |
| `src/job_recommendation_api/api/deps.py` | FastAPI dependency wiring | config, converter, llm | Low |
| `src/job_recommendation_api/api/routes/health.py` | `/healthz`, `/readyz` | app factory | Low |
| `src/job_recommendation_api/api/routes/recommendations.py` | `POST /api/v1/recommendations` | deps, service, schemas, errors | Medium (multipart upload) |
| `src/job_recommendation_api/api/router.py` | v1 `APIRouter` aggregation | routes | Low |
| `src/job_recommendation_api/main.py` | `create_app()` factory, lifespan, middleware | config, router, errors | Low |
| `tests/` | pytest suite (unit + API integration) | all source modules | Low |
| `.github/` (optional) | CI (test + lint + typecheck) | - | Low |

## Architectural Guardrails

### Allowed
- **Layered architecture** with strict one-way dependencies:
  `api -> services -> llm / schemas`; `api` never imports concrete infra directly.
- **Protocols for infrastructure boundaries** (`LLMClient`, `DocumentConverter`)
  so both are mockable and swappable (`typing.Protocol`).
- **Dependency injection** via FastAPI `Depends`; singletons built once in
  `lifespan` and shared, never instantiated per request.
- **App factory** (`create_app(settings=None)`) returning a `FastAPI` instance;
  enables clean test construction and config override.
- **`pydantic-settings`** for all configuration; secrets via environment only.
- **Pydantic v2 models** as the single source of truth for the response, and
  also exported as the LLM's structured-output JSON Schema.
- **Structured LLM output** (`response_format` json_schema) **plus** mandatory
  Pydantic re-validation of whatever the model returns (defense in depth).
- **Async LLM client** using the OpenRouter SDK's native `send_async` method;
  only genuinely blocking sync work (markitdown) is offloaded to a threadpool
  via `anyio.to_thread.run_sync` / `run_in_threadpool`.
- **Typed everything**: full annotations, `mypy --strict` at minimum.
- **Deterministic, environment-aware logging** (structured JSON or key=value)
  with request-id propagation via `contextvars` so service-layer logs carry
  the same correlation id as the HTTP request.

### Forbidden
- **No blocking calls on the event loop.** markitdown runs in a threadpool,
  never directly inside an `async def` route or service method. The LLM client
  is async-native (`send_async`); do NOT wrap it in `run_in_threadpool`.
- **No API key, model string, or secret in source**, git history, or logs.
  Single source: `Settings` from environment.
- **No raw `json.loads` trust of the LLM response.** Always go through
  `model_validate_json` / schema validation; ignore, sanitize, or error on
  malformed output explicitly.
- **No impromptu extra endpoint.** Keep the surface to health + recommendation
  until a spec says otherwise.
- **No global mutable state** (module-level clients, caches) that breaks tests
  or lifespan.
- **No shared `MarkItDown()` instance** across threads (its internal
  `requests.Session` is not thread-safe). Create per-call.
- **No broad `except Exception` swallowing**; map to typed domain errors.

### Constraints
- Python `>=3.14` (locked by `.python-version`).
- `markitdown[pdf]` extra required (pulls `pdfminer.six`, `pdfplumber`).
- OpenRouter API key required at startup for the recommendation path; do not
  make the whole app fail to start if unset (fail fast only on request, or
  surface clearly in `/readyz`).
- File upload: enforce size and `application/pdf` content type; reject early
  with `413` / `415`.
- LLM calls carry a timeout; timeouts surface as a typed error -> `504`.

---

## Atomic Specs

> Each spec is independently testable. IDs are used in `tasks.md` and the
> execution sequence. `Defn of done` = acceptance criteria all observable.

### Spec ID-001: Project scaffolding and dependency manifest
- **Objective**: Establish the install/run/test/lint surface so every later
  spec can import its dependencies.
- **Acceptance Criteria**:
  - `pyproject.toml` declares runtime deps: `fastapi`, `uvicorn[standard]`,
    `python-multipart`, `pydantic-settings`, `markitdown[pdf]`, `openrouter`.
  - Dev/test group declares: `pytest`, `pytest-asyncio`, `httpx`, `ruff`,
    `mypy`, `types-*` as needed.
  - `uv sync` succeeds on Python 3.14; `uv run python -c "import fastapi, markitdown, openrouter"` exits 0.
  - `[project.scripts]` entry point: `job-recommendation-api = "job_recommendation_api.main:main"`
    (references `main.py`, not `__init__.py`). A `__main__.py` also exists for
    `python -m job_recommendation_api` and delegates to `main:main`.
- **Affected Components**: `pyproject.toml`, `src/job_recommendation_api/__main__.py`.
- **Contracts / Interfaces**: none (foundational).
- **Guardrails**: pin `markitdown` to `>=0.1.6` (current); keep runtime deps minimal.
- **Dependencies**: none.

### Spec ID-002: Settings / configuration module
- **Objective**: Centralize env-driven config with validation so nothing is
  hard-coded or silently defaulted unsafely.
- **Acceptance Criteria**:
  - `Settings(BaseSettings)` in `config.py` with `env_prefix="JRA_"` (or none,
    documented) and `env_file=".env"`.
  - Fields: `openrouter_api_key: SecretStr`, `openrouter_model: str`
    (default `openai/gpt-4o-mini`), `llm_timeout_seconds: float` (default 60),
    `llm_max_tokens: int`, `max_upload_bytes: int` (default 10 MiB),
    `log_level: str`.
  - `openrouter_api_key` is required and validated non-empty; a missing key
    raises a clear, actionable error at first access.
  - `load_settings()` in `config.py` returns a `Settings` instance - a plain
    factory, NOT a FastAPI dependency (the dependency is `deps.get_settings`,
    see ID-013). Do NOT use `lru_cache` on `load_settings()` (it causes
    cross-test pollution via cached values that persist across the test
    session).
  - `.env` loading order: `pydantic-settings` loads environment variables first,
    then `.env` file. Env vars always win over `.env` values. Document this in
    `.env.example` to avoid developer confusion.
- **Affected Components**: `src/job_recommendation_api/config.py`, `.env.example`.
- **Contracts**: `Settings` field names/types above; `load_settings() -> Settings` (documented in api-contract.md).
- **Guardrails**: never log the key (`SecretStr` masks on repr); no secrets in `pyproject`.
- **Dependencies**: ID-001.

### Spec ID-003: Domain schema models (Pydantic)
- **Objective**: Define the typed response contract and export its JSON Schema
  for both API docs and the LLM structured-output request.
- **Acceptance Criteria**:
  - `schemas/recommendation.py` defines:
    - `JobRecommendation { title: str, fit_score: float (0..1), seniority_level: Literal["intern","junior","mid","senior","staff","principal","executive"], rationale: str, key_skills: list[str] }`
    - `LearningResource { topic: str, kind: Literal["course","book","certification","tutorial","project"], title: str, provider: str|None = None, url: HttpUrl|None = None, rationale: str }` (provider/url optional-default-null).
    - `ResumeAnalysis { summary: str, top_skills: list[str] (1..20 items), jobs: list[JobRecommendation], education_materials: list[LearningResource] }`
    - `RecommendationResponse { analysis: ResumeAnalysis, meta: ResponseMeta }` (meta: model used, markdown length, token usage if available).
  - `fit_score` constrained with `Field(ge=0, le=1)`; lists use `min_length`/`max_length` where sensible.
  - `provider` and `url` on `LearningResource` are **optional-default-null**
    (`str | None = None`, `HttpUrl | None = None`): an absent key validates to
    `None`; they are omitted from the LLM schema's `required` list, so the model
    may omit them rather than emitting `null`.
  - **Two JSON Schema exports** (both derived from Pydantic, never hand-maintained):
    - `RESUME_ANALYSIS_SCHEMA = ResumeAnalysis.model_json_schema()` - this is the
      schema sent to the LLM (it does not include `meta`, which is runtime-only).
    - `RecommendationResponse.model_json_schema()` - used for API documentation
      and response validation.
  - Unit test asserts validation (reject `fit_score=1.5`, `kind` not in enum,
    `seniority_level="lead"` rejected; a `LearningResource` without `provider`
    or `url` keys validates to `None`, and `RESUME_ANALYSIS_SCHEMA` omits both
    from `required`).
- **Affected Components**: `src/job_recommendation_api/schemas/recommendation.py`, `src/job_recommendation_api/schemas/__init__.py`.
- **Contracts**: exact field names above; `RESUME_ANALYSIS_SCHEMA` (JSON Schema from `ResumeAnalysis`) is the contract consumed by ID-007 and ID-006. `RecommendationResponse` schema is for API docs only.
- **Guardrails**: Pydantic v2 style (`model_config`, `ConfigDict`); keep schemas pure (no I/O).
- **Dependencies**: ID-001.

### Spec ID-004: Domain error hierarchy
- **Objective**: Replace ad-hoc exceptions with a typed, mappable error model.
- **Acceptance Criteria**:
  - `errors.py` defines `AppError(Exception)` base with `code: str` + `detail: str`.
  - Subclasses: `InvalidDocumentError`, `DocumentConversionError`,
    `DocumentTooLargeError`, `UnsupportedMediaTypeError`, `LLMError` (plus
    `LLMTimeoutError`, `LLMInvalidOutputError`), `ConfigurationError`.
  - Each carries a stable `code` string used by the exception handler mapping.
  - Unit test confirms inheritance and default messages.
- **Affected Components**: `src/job_recommendation_api/errors.py`.
- **Contracts**: error `code` values listed in api-contract.md's error model.
- **Guardrails**: no HTTP semantics in the domain errors (they stay transport-agnostic).
- **Dependencies**: ID-001.

### Spec ID-005: Document converter service (markitdown)
- **Objective**: Encapsulate PDF -> Markdown behind a Protocol; isolate the
  third-party API in one file.
- **Acceptance Criteria**:
  - `DocumentConverter` Protocol: `def convert(pdf_bytes: bytes, *, name: str) -> str`.
  - `MarkItDownConverter` implements it: wraps `io.BytesIO` and calls
    `MarkItDown().convert_stream(stream, stream_info=StreamInfo(extension=".pdf", mimetype="application/pdf"))`, returns `result.markdown`.
    **Create a new `MarkItDown()` instance per call** - the class initializes
    an internal `requests.Session` that is not thread-safe for concurrent use.
    Per-call instantiation is safe and the overhead is negligible.
  - Empty/non-text result raises `DocumentConversionError`; non-PDF bytes raise `InvalidDocumentError`.
  - Blocking call executed off the event loop at the service tier, not inside this sync function.
  - Unit test uses a tiny generated PDF (e.g. `reportlab`, or a committed fixture PDF) and a stubbed failure case.
- **Affected Components**: `src/job_recommendation_api/services/document_converter.py`.
- **Contracts**: `DocumentConverter.convert(bytes, name=) -> str`.
- **Guardrails**: never share a `MarkItDown()` instance across threads; do not keep PDF bytes after conversion.
- **Dependencies**: ID-001, ID-004.

### Spec ID-006: LLM client abstraction (OpenRouter SDK)
- **Objective**: Wrap the `openrouter` SDK behind an injectable async Protocol
  with timeout, retry, and typed error mapping.
- **Acceptance Criteria**:
  - `LLMClient` Protocol: `async def complete(messages: list[dict[str, str]], *, schema: dict) -> dict` (returns parsed dict). The client is prompt-agnostic: it receives fully-built `[{"role": ..., "content": ...}]` messages and does NOT import `services/prompts.py` or build prompts itself.
  - `OpenRouterLLMClient`:
    - Constructed with `settings`; opens `OpenRouter(api_key=...)` (async context-managed) in lifespan.
    - Calls `await client.chat.send_async(model=..., messages=messages, response_format=ChatFormatJSONSchemaConfig(type="json_schema", json_schema=ChatJSONSchemaConfig(name="resume_recommendations", strict=True, schema_=schema)), stream=False)`.
    - Extracts content, parses JSON, returns dict; maps API/HTTP failures -> `LLMError`, timeouts -> `LLMTimeoutError`, malformed JSON -> `LLMInvalidOutputError`.
    - **Retry with exponential backoff** for transient errors: HTTP 429 (rate limit), 502, 503, 504, and connection errors. Max 2 retries (3 total attempts), backoff starting at 1s. Do NOT retry on 4xx (except 429) or on `LLMInvalidOutputError`.
    - **Model fallback**: if the configured model rejects `json_schema` response_format (some providers don't support it), log a warning and fall back to `FormatJSONObjectConfig(type="json_object")` mode. Pydantic re-validation still catches schema violations.
  - Unit test with a fake `OpenRouter` (monkeypatch) asserting correct args, error mapping, and retry behavior.
- **Affected Components**: `src/job_recommendation_api/llm/client.py`, `src/job_recommendation_api/llm/__init__.py`.
- **Contracts**: `LLMClient.complete(messages: list[dict[str, str]], *, schema: dict) -> dict` (async).
- **Guardrails**: timeout applied; API key only from settings; never log prompt content + key; treat the SDK response strictly; must NOT depend on `services/prompts.py` (keeps `llm` below `services` in the layer graph).
- **Dependencies**: ID-002, ID-004.

### Spec ID-007: Prompt and structured-output schema
- **Objective**: Own the prompt (system + user) and the exact JSON Schema the
  model must emit, so they are reviewable and versioned.
- **Acceptance Criteria**:
  - `services/prompts.py` defines `SYSTEM_PROMPT`, a `build_user_prompt(resume_markdown: str) -> str`, and `RECOMMENDATION_SCHEMA = ResumeAnalysis.model_json_schema()` (the schema for the LLM output, NOT including `meta` which is runtime-only).
  - System prompt instructs: analyze the resume, then emit ONLY valid JSON
    matching the schema (summary, top skills, ranked jobs with rationale, education
    materials with rationale); no markdown fences; no extra keys.
  - Unit test asserts the prompt embeds the resume and the schema has the expected top-level required keys.
- **Affected Components**: `src/job_recommendation_api/services/prompts.py`.
- **Contracts**: `RECOMMENDATION_SCHEMA` (JSON Schema object from `ResumeAnalysis`) consumed by ID-006/ID-008.
- **Guardrails**: prompt is a constant (no runtime string interpolation except resume); length-capped resume slice in prompt to guard token budget.
- **Dependencies**: ID-003.

### Spec ID-008: Recommendation orchestration service
- **Objective**: Compose converter + LLM + validation into the single business
  operation, with async LLM and threadpool-offloaded sync converter.
- **Acceptance Criteria**:
  - `RecommendationService.recommend(pdf_bytes, name) -> RecommendationResponse` (async method):
    1. `await run_in_threadpool(converter.convert, ...)` -> markdown (sync, offloaded).
    2. Assemble messages from prompts (ID-007): `messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(markdown)}]`.
    3. `await llm.complete(messages, schema=RECOMMENDATION_SCHEMA)` -> dict (async, native).
    4. `RecommendationResponse.model_validate(dict_with_meta)` (enrich with meta).
  - On `LLMInvalidOutputError`, optionally retry once with a corrective prompt:
    rebuild the messages with an appended corrective user instruction (guarded,
    bounded); on repeated failure, surface the typed error.
  - Async method; the async route calls it directly (no threadpool wrapping needed).
  - Unit tests: fake converter + fake llm; asserts happy path composition; asserts converter error and LLM error propagate unchanged.
- **Affected Components**: `src/job_recommendation_api/services/recommendation.py`.
- **Contracts**: `RecommendationService(converter, llm_client).recommend(pdf_bytes, name) -> RecommendationResponse` (async); the service owns message assembly (system + user from `prompts.py`).
- **Guardrails**: service has no HTTP/FastAPI imports; no retry loops without bound; no swallowing of configured errors.
- **Dependencies**: ID-003, ID-004, ID-005, ID-006, ID-007.

### Spec ID-009: App factory + lifespan + middleware
- **Objective**: Provide `create_app()`, manage component lifecycle, and add
  cross-cutting middleware.
- **Acceptance Criteria**:
  - `create_app(settings: Settings | None = None) -> FastAPI` wires:
    - settings resolution: `app.state.settings = settings if settings is not None else load_settings()`; `config.load_settings()` is the single construction point.
    - lifespan: build converter, `OpenRouter` client (async context manager), `RecommendationService` once; store on `app.state`.
    - **Graceful shutdown**: lifespan's shutdown phase must `await llm_client.close()` (or exit the `async with OpenRouter(...)` context) to release HTTP connections cleanly.
    - `api/v1` router include at `/api/v1`.
    - CORS not required for a no-auth API (or permissive local default, documented).
    - request-id middleware: generates/echoes `X-Request-ID`, stores it in a
      `contextvars.ContextVar` so service-layer and LLM-client logs automatically
      include the correlation id without explicit parameter passing.
    - structured logging middleware: configures JSON/key=value log format.
  - `main.py` exposes `app = create_app()` and a `main()` uvicorn runner; `__main__.py` delegates to `main:main`.
  - App imports fail only on real config errors, not on missing key (defer key check to request or `/readyz`).
- **Affected Components**: `src/job_recommendation_api/main.py`, `__main__.py`.
- **Contracts**: `create_app(settings=None) -> FastAPI`; `app.state.{settings,converter,llm_client,recommendation_service}`.
- **Guardrails**: no blocking startup network calls; graceful shutdown closes SDK context.
- **Dependencies**: ID-002, ID-012 (handlers), ID-010/011 (routes).

### Spec ID-010: Health routes
- **Objective**: Liveness and readiness for operations.
- **Acceptance Criteria**:
  - `GET /healthz` -> `200 {"status":"ok"}` (liveness, no deps).
  - `GET /readyz` -> `200 {"status":"ready","model":...} `or `503` if `openrouter_api_key` missing (readiness).
  - Unit test both states.
- **Affected Components**: `src/job_recommendation_api/api/routes/health.py`.
- **Contracts**: paths + response shapes (documented in api-contract.md).
- **Guardrails**: avoid revealing secrets in readiness payload (no key echo).
- **Dependencies**: ID-002, ID-009.

### Spec ID-011: Recommendations endpoint
- **Objective**: The core accepting endpoint with upload validation and clean
  error mapping.
- **Acceptance Criteria**:
  - `POST /api/v1/recommendations` accepts `multipart/form-data` field `file` (PDF).
  - Validates content type is `application/pdf` (reject `415` by raising `UnsupportedMediaTypeError`), size <= `max_upload_bytes` (reject `413` by raising `DocumentTooLargeError`, streaming check / read then check). Both map to HTTP through the shared handlers (ID-012), not raw `HTTPException`.
  - Reads bytes, calls service directly (`await service.recommend(...)`), returns
    `RecommendationResponse` serialized (`200`). The service is async; only the
    converter step inside it uses `run_in_threadpool`.
  - **Multipart size handling**: `python-multipart` (used by Starlette) spools
    uploads to a `SpooledTemporaryFile` (default 1MB threshold, then to disk).
    For a 10MB cap this is acceptable. Read the upload, check length, reject
    with `413` if over limit before passing to the service.
  - Maps typed domain errors to HTTP via shared handlers (ID-012); returns
    `400` invalid doc, `413` too large, `415` unsupported media type, `422` LLM invalid output / empty conversion, `504` LLM timeout, `502` upstream LLM error.
  - API integration test: valid fixture PDF (mock LLM) -> 200 with schema; oversized file -> 413; wrong type -> 415.
- **Affected Components**: `src/job_recommendation_api/api/routes/recommendations.py`.
- **Contracts**: request/response per api-contract.md.
- **Guardrails**: never trust client filename/extension (validate bytes/mime); stream large files without loading unbounded memory (read up to limit + 1 byte to detect overflow); cleanup temp resources.
- **Dependencies**: ID-008, ID-012, ID-013.

### Spec ID-012: Global exception handlers
- **Objective**: Convert domain errors to consistent HTTP error envelopes.
- **Acceptance Criteria**:
  - Registered handlers map `AppError` subclasses to status codes:
    `InvalidDocumentError`->400, `DocumentTooLargeError`->413,
    `UnsupportedMediaTypeError`->415, `DocumentConversionError`/`LLMInvalidOutputError`->422,
    `LLMError`->502, `LLMTimeoutError`->504, `ConfigurationError`->500.
  - Error envelope: `{"error":{"code": "...", "message": "..."}}` with stable codes.
  - `RequestValidationError` -> 422 with structured detail (default acceptable, but consistent shape).
  - Unit/integration test asserts mapping + envelope.
- **Affected Components**: `src/job_recommendation_api/api/errors.py` (handlers), wired in `main.py`.
- **Contracts**: error envelope + codes in api-contract.md.
- **Guardrails**: never leak stack traces or internal fields to the client; log full detail server-side.
- **Dependencies**: ID-004.

### Spec ID-013: Dependency wiring
- **Objective**: Expose `Depends`-friendly accessors for the singletons.
- **Acceptance Criteria**:
  - `api/deps.py` defines `get_settings`, `get_converter`, `get_llm_client`,
    `get_recommendation_service` reading from `request.app.state`.
  - `get_settings` is the **single** FastAPI dependency for settings and reads
    `request.app.state.settings`; it does NOT construct `Settings`. Construction
    happens only via `config.load_settings()` (ID-002), invoked by `create_app`
    (ID-009). Route/test overrides use
    `app.dependency_overrides[get_settings] = lambda: test_settings`.
  - Typed, with `Annotated[...]`-style aliases usable in routes.
  - Test: constructing these against a test app returns the same singleton instance.
- **Affected Components**: `src/job_recommendation_api/api/deps.py`.
- **Contracts**: dependency callables per above.
- **Guardrails**: no construction inside deps (build only in lifespan).
- **Dependencies**: ID-002, ID-005, ID-006, ID-008, ID-009.

### Spec ID-014: Unit test suite
- **Objective**: Lock behavior of every non-HTTP unit in isolation.
- **Acceptance Criteria**:
  - `pytest` green for: config validation, schemas, errors, converter (real tiny PDF + failure stub), llm client (fake SDK), prompts, recommendation service (fakes).
  - Uses fakes/monkeypatch, no network, no real API key.
- **Affected Components**: `tests/unit/*`.
- **Contracts**: fixtures in `tests/conftest.py`.
- **Guardrails**: no `pytest` time-based flakiness; keep fakes as Protocols (mypy-clean).
- **Dependencies**: ID-002..ID-008.

### Spec ID-015: API integration test suite
- **Objective**: Verify the HTTP surface end-to-end with a stubbed LLM.
- **Acceptance Criteria**:
  - `httpx.AsyncClient(transport=ASGITransport(app))` tests: health 200/503, recommendation 200 (mock LLM), 415, 413, 422 malformed LLM output, error envelope shape.
  - Lifespan-compatible (tests run through `TestClient`/ASGITransport so lifespan executes).
- **Affected Components**: `tests/api/*`, `tests/conftest.py`.
- **Contracts**: api-contract.md.
- **Guardrails**: fake the LLM at the `app.state.llm_client` boundary via dependency override.
- **Dependencies**: ID-009..ID-013.

### Spec ID-016: Code quality tooling
- **Objective**: Enforce the guardrails mechanically.
- **Acceptance Criteria**:
  - `ruff` config (line-length 100, import sorting, docstring rules) passes with zero errors.
  - `mypy --strict` passes (with targeted overrides documented).
  - Optional `.pre-commit-config.yaml` with ruff + mypy hooks.
  - A `Makefile` or `justfile` (or documented `uv run` commands) for `lint`, `typecheck`, `test`.
- **Affected Components**: `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`), `.pre-commit-config.yaml`.
- **Contracts**: none.
- **Guardrails**: do not relax to `# type: ignore` casually; each is justified.
- **Dependencies**: ID-001 (parallelizable with most).

### Spec ID-017: README + env example + run docs
- **Objective**: Make the project runnable and understandable.
- **Acceptance Criteria**:
  - `README.md`: what it does, quickstart (install, env, run, curl example), endpoint table, architecture pointer to `docs/`.
  - `.env.example` lists all vars with defaults; `.gitignore` includes `.env`.
  - `curl` example showing a full request/response.
- **Affected Components**: `README.md`, `.env.example`, `.gitignore`.
- **Contracts**: none.
- **Guardrails**: no secrets committed (`.env` ignored); example uses placeholder key.
- **Dependencies**: ID-009..ID-011 (accurate endpoint/behavior).

## Execution Sequence

Ordered by dependency; grouped into phases. Items in the same phase are
parallelizable where noted.

**Phase 0 - Foundation**
1. ID-001 (scaffold) - blocks all runtime specs
2. ID-016 (quality tooling) - **must complete before Phase 1** so lint/typecheck
   rules apply as code lands. Do not defer.

**Phase 1 - Contracts & layers (parallelizable within)**
3. ID-002 (settings)
4. ID-004 (errors)
5. ID-003 (schemas) - blocks ID-007, ID-008

**Phase 2 - Infrastructure adapters (parallel: 6, 7; 8 after 3)**
6. ID-005 (converter)
7. ID-006 (llm client)
8. ID-007 (prompts/schema) - after ID-003

**Phase 3 - Business + HTTP**
9. ID-008 (service) - after 3,4,5,6,7
10. ID-012 (exception handlers) - after ID-004
11. ID-009 (app factory) - after 2,10 (routes stubbed then filled)
12. ID-013 (deps) - after 2,5,6,8,9
13. ID-010 (health) - after 9,11
14. ID-011 (recommendations) - after 8,10,13

**Phase 4 - Verification**
15. ID-014 (unit tests) - as each Phase 2/3 unit lands (can be interleaved)
16. ID-015 (integration tests) - after 9-13

**Phase 5 - Release docs**
17. ID-017 (README/env) - after 9-13 (last)

Parallelization opportunities: (ID-002, ID-004) together; (ID-005, ID-006)
together after ID-004; ID-014 fires continuously.

## Open Questions and Risks

### Open questions (confirm before locking)
1. **Model choice** - default `openai/gpt-4o-mini`; confirm desired model /
   cost ceiling. Structured-output support varies by model; verify the chosen
   model supports `json_schema` on OpenRouter.
2. **Response schema richness** - current schema includes `summary` + `top_skills`
   + rationale fields. Confirm if a leaner `{jobs, education_materials}` shape
   is preferred or if extra fields are desired (e.g. salary bands, companies).
3. **Failure semantics** - should a failed single recommendation 500, or return
   a partial/empty result with `200` + warnings? (Blueprint assumes fail-loud.)

### Risks
- **Image-only / scanned PDFs**: markitdown has no OCR without an extra (e.g.
  `markitdown[ocr]` / Azure doc-intel). Documents without a text layer will
  come back empty -> `422`. Mitigation: detect empty markdown early, return
  clear error message; document limitation.
- **Third-party SDK drift**: `openrouter` and `markitdown` are evolving; API
  shapes may differ from this blueprint. Mitigation: adapter files isolate
  each SDK in one module; verify against installed version in ID-005/ID-006.
  **Confirmed SDK shapes** (as of 2026-08-20): OpenRouter uses
  `ChatFormatJSONSchemaConfig(type="json_schema", json_schema=ChatJSONSchemaConfig(name=..., schema_=..., strict=True))`
  and async via `client.chat.send_async(...)`. markitdown uses
  `MarkItDown().convert_stream(BytesIO, stream_info=StreamInfo(...))`.
- **Structured-output provider variance**: some providers reject `json_schema`.
  Mitigation: fallback to `FormatJSONObjectConfig(type="json_object")` mode +
  strict Pydantic validation + bounded retry; keep the schema the single
  validator. If the configured model doesn't support structured outputs on any
  available provider, use `require_parameters: true` in provider preferences
  or switch to a known-compatible model.
- **Token / cost blowup**: large resumes + verbose schema. Mitigation: cap
  prompt resume length, `max_tokens`, and timeouts in settings.
- **No auth / public surface**: unauthenticated LLM endpoint is a cost
  amplification target. Out of scope per request, but rate limiting is the
  natural next iteration. Flagged as follow-up.
- **Concurrent PDF parsing**: `MarkItDown` creates an internal `requests.Session`
  that is not thread-safe. Mitigation: per-call instantiation (confirmed in
  ID-005). Do NOT share a single instance across requests.