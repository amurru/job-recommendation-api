# Job Recommendation API - Feature Plan: Security Hardening (SH)

This directory plans and tracks the third feature iteration: closing the
security gaps found in the full security audit (August 2026) before the
service is exposed beyond localhost.

## Background

The audit confirmed a disciplined codebase (no dangerous sinks, secrets via
`SecretStr`, `.env` never committed, zero known CVEs via `pip-audit`), but
identified two High-severity architectural risks and a set of Medium/Low
hardening gaps:

1. **H1 - Unauthenticated cost amplification**: the service is a no-auth,
   unthrottled proxy to paid LLM APIs. One request can trigger ~15 model
   calls (up to 10 vision OCR calls + profile extraction with retries +
   recommendation with corrective retry). An anonymous loop burns unlimited
   OpenRouter budget and starves the converter threadpool.
2. **H2 - Unbounded PDF structural parsing**: the OCR page budget caps vision
   calls only. `pdfplumber` eagerly iterates every declared page, every
   embedded image on a page is decoded before the budget check, and page
   dimensions are attacker-chosen. A 10 MiB crafted PDF pins a threadpool
   worker with minutes of CPU and hundreds of MB of RAM.
3. **M1 - Prompt injection surface**: prompt delimiters can be spoofed from
   resume content, the injection guard is a low-recall blocklist, and
   `education_materials[].url` accepts any URL (attacker-steerable output
   links).
4. **M2-M5 - HTTP surface gaps**: unvalidated reflected `X-Request-ID` (log
   injection/flooding), interactive docs enabled in production, raw pydantic
   validation errors echoed to clients, fully permissive CORS.
5. **L1-L7 - Information disclosure and hygiene**: anonymous `?include_meta`
   diagnostics, model name disclosure on `/readyz`, upstream OCR exception
   strings embedded into markdown, no security headers.

**What** (scope): five hardening workstreams, phased so each lands
independently testable:

- **Phase 0 - Authentication (SH-001..003)**: proper API-key authentication
  while still allowing limited anonymous posts. Identity resolution, hashed
  key storage, 401 handling, identity plumbed end to end.
- **Phase 1 - Configurable rate limiting (SH-004..006)**: per-identity
  sliding-window limits (authenticated and anonymous tiers), 429 + rate-limit
  headers, and a concurrency cap on the expensive conversion pipeline.
- **Phase 2 - Document parsing bounds (SH-007..008)**: structural caps on
  pages / page size / embedded images, explicit Pillow decompression limits,
  and a wall-clock conversion deadline.
- **Phase 3 - HTTP surface hardening (SH-009..013)**: request-ID validation,
  docs gating, validation-error sanitization, configurable CORS, meta gating,
  security headers.
- **Phase 4 - LLM content hardening (SH-014..015)**: prompt delimiter
  escaping, https-only output URLs, static OCR failure markers.
- **Phase 5 - Verification & release (SH-016..017)**: full test matrix,
  integration tests, docs and `.env.example` updates.

**Why**: H1 and H2 make any public deployment an open drain on paid
infrastructure. Phases 0 and 1 close them together: Phase 0 establishes
*who* is calling (identity), Phase 1 establishes *how much* each identity may
consume (limits). Phases 2-4 close the remaining audit findings in
descending severity order.

**Non-goals** (kept out deliberately, this iteration):
- Full user accounts, JWT issuance/refresh, OAuth. Static API keys are the
  right weight for a single-purpose API (see SH-ADR-001).
- A shared rate-limit store (Redis). The limiter is in-process, behind a
  Protocol, targeting single-worker correctness (same trade-off as the
  extraction cache in `docs/02-extraction-fidelity/`).
- WAF / reverse-proxy configuration. Documented as a deployment
  recommendation, not implemented in-app.
- Changing the `analysis` response shape. All HTTP changes are additive
  (new headers, new error codes, one schema constraint on `url`).

### Assumptions

| Assumption | Default | Rationale |
|------------|---------|-----------|
| Auth scheme | Static API keys via `Authorization: Bearer` | Matches OpenAI-style client expectations; no session state; trivially revocable by config change |
| Key storage | SHA-256 digests in memory (from env / key file) | Plaintext keys never resident longer than startup; key ID (digest prefix) safe to log |
| Anonymous tier | Enabled by default (`anonymous_enabled=true`) | Preserves the product's zero-friction try-it promise; Phase 1 makes the "limited" real |
| Rate limiter | In-process sliding window, per identity | No new infrastructure; Protocol seam allows Redis later |
| Anonymous limit default | 5 requests / hour per IP | Conservative: each request can cost ~15 LLM calls |
| Authenticated limit default | 60 requests / minute per key | Generous for real usage; still bounds runaway clients |
| Structural caps | 50 pages, 30-inch page edge, 20 images/page, 50 M pixel PIL ceiling | Generous for real resumes (1-3 pages); hostile inputs fail fast |
| Conversion deadline | 30 s wall clock | Bounds worst-case threadpool occupancy per request |

## Repository Dependency Map

Current state: extraction-fidelity plan complete, tests green. Changes below
are marked **new** or **changed**.

| Module / Path | Role | Change | Depends On |
|---------------|------|--------|-----------|
| `src/job_recommendation_api/auth.py` | `Identity` dataclass, `IdentityKind`, API-key store (hashed), `get_identity` FastAPI dependency | **new** | config |
| `src/job_recommendation_api/ratelimit.py` | `RateLimiter` Protocol, `SlidingWindowRateLimiter` (thread-safe, per identity), limit decision + headers | **new** | config, auth |
| `src/job_recommendation_api/config.py` | New settings: `api_keys`, `api_keys_file`, `auth_required`, `anonymous_enabled`, `rate_limit_*`, `max_pdf_pages`, `max_images_per_page`, `max_page_inches`, `max_image_pixels`, `convert_deadline_seconds`, `cors_origins`, `docs_enabled` | changed | - |
| `src/job_recommendation_api/errors.py` | `RateLimitedError` (429), `DocumentTooComplexError` (422), `UnauthorizedError` (401) | changed | - |
| `src/job_recommendation_api/api/deps.py` | `IdentityDep`; rate limiter accessor from app.state | changed | auth, ratelimit |
| `src/job_recommendation_api/api/routes/recommendations.py` | Identity dependency, rate-limit check, 401/429 mapping, `X-RateLimit-*` headers, gated `include_meta` | changed | deps |
| `src/job_recommendation_api/api/routes/health.py` | `readyz` model disclosure gated by environment | changed | config |
| `src/job_recommendation_api/api/middleware.py` | Request-ID validation, security headers | **new** | - |
| `src/job_recommendation_api/api/errors.py` | Sanitized `RequestValidationError` handler; 429 handler with `Retry-After` | changed | - |
| `src/job_recommendation_api/main.py` | Lifespan wiring (key store, limiter), CORS from config, docs gating, CapacityLimiter for conversion | changed | all |
| `src/job_recommendation_api/services/ocr/pdf_converter.py` | Structural caps: page count, page dimensions, images per page | changed | config |
| `src/job_recommendation_api/services/document_converter.py` | Explicit `Image.MAX_IMAGE_PIXELS`, image dimension cap on wrap, deadline plumbing | changed | config |
| `src/job_recommendation_api/services/recommendation.py` | Conversion runs under the concurrency limiter + deadline | changed | - |
| `src/job_recommendation_api/services/prompts.py` | Delimiter escaping helper applied to all embedded content | changed | - |
| `src/job_recommendation_api/schemas/recommendation.py` | `LearningResource.url` constrained to https | changed | - |
| `src/job_recommendation_api/services/ocr/pdf_converter.py` | Static OCR failure markers (no upstream exception strings in markdown) | changed | - |
| `tests/` | New suites: auth, ratelimit, structural caps, middleware, prompt escaping; updated integration tests | changed | all |
| `README.md`, `.env.example` | Document auth, limits, new settings | changed | - |

## Architectural Guardrails

### Allowed
- **Protocol boundaries** for new infrastructure seams: `RateLimiter` (and the
  existing `ExtractionCache` pattern). In-memory implementations now, Redis
  later, no call-site changes.
- **Hashed key comparison**: the key store holds SHA-256 digests only.
  Constant-time comparison (`hmac.compare_digest`). Key IDs (digest prefix)
  are the only key-derived value that may be logged.
- **Identity as a first-class dependency**: routes declare `IdentityDep`;
  the limiter and any future per-identity quota read identity, never raw
  headers.
- **Configurable everything**: limits, caps, CORS origins, anonymous-tier
  on/off are `Settings` fields with safe defaults.
- **Fail-loud for structural violations** (`document_too_complex`), matching
  the existing OCR-budget fail-loud pattern (FP-ADR-006).
- **Anonymous tier stays opt-out-able**: `anonymous_enabled=false` turns the
  API into key-only with one config flip.

### Forbidden
- **Never log or return plaintext API keys** - not in errors, not in meta,
  not in debug output.
- **No rate-limit state keyed on attacker-controlled data without
  normalization** (IP from `X-Forwarded-For` only behind a trusted proxy;
  default to the socket peer).
- **No unbounded parsing**: page count, page dimensions, image count, pixel
  count, and wall-clock time must all be capped before conversion work
  scales with attacker-chosen numbers.
- **No upstream exception strings in markdown** that reaches LLM prompts.
- **No raw pydantic error payloads in client responses.**
- **No plaintext delimiter trust**: resume/profile content is escaped before
  prompt embedding; the model must not be able to close the delimiter from
  inside the data.

### Constraints
- Python `>=3.14` (locked by `.python-version`).
- Backward-compatible for legitimate clients: an anonymous `POST` with no
  `Authorization` header still succeeds (within limits) by default.
- `GET /healthz` and `GET /readyz` remain unauthenticated (liveness/readiness
  probes).
- All new settings documented in `.env.example` and the README table.

---

## Atomic Specs

> Each spec is independently testable. IDs are used in `tasks.md` and the
> execution sequence. `Defn of done` = acceptance criteria all observable.

### Spec SH-001: Identity model and API-key store
- **Objective**: Establish who is calling. Static API keys resolved to an
  `Identity`, with hashed in-memory storage and safe logging.
- **Acceptance Criteria**:
  - `auth.py` (**new**) defines:
    - `IdentityKind = Literal["key", "anonymous"]`.
    - `Identity` frozen dataclass: `kind: IdentityKind`,
      `key_id: str | None` (digest prefix, e.g. first 12 hex chars),
      `ip: str | None` (anonymous tier only, socket peer).
    - `ApiKeyStore`: constructed from `settings.api_keys` (comma-separated)
      and/or `settings.api_keys_file` (one key per line, `#` comments).
      Stores `{sha256(key): key_id}`. `verify(key) -> Identity | None` uses
      `hmac.compare_digest` on digests. Plaintext keys are not retained after
      construction.
  - `config.py` gains: `api_keys: str = ""`, `api_keys_file: Path | None`,
    `auth_required: bool = False`, `anonymous_enabled: bool = True`.
  - `api/deps.py` gains `get_identity(request, settings) -> Identity`:
    - No `Authorization` header -> anonymous identity (if
      `anonymous_enabled`, else `UnauthorizedError`).
    - `Authorization: Bearer <key>` -> verify; invalid key raises
      `UnauthorizedError` (401, `WWW-Authenticate: Bearer`).
    - Non-Bearer scheme raises `UnauthorizedError`.
  - `Identity` (kind + key_id, never the key) is attached to the request
    state for logging.
  - Unit tests: valid key resolves; unknown key 401s; malformed header 401s;
    anonymous allowed/denied per config; key file parsing (comments, blank
    lines); digest store never contains plaintext (assert via repr/attrs).
- **Affected Components**: `auth.py` (**new**), `config.py`, `api/deps.py`,
  `.env.example`.
- **Contracts**: `ApiKeyStore.verify(key) -> Identity | None`;
  `Identity(kind, key_id, ip)`.
- **Guardrails**: hashed storage; constant-time compare; key_id only in logs.
- **Dependencies**: none.

### Spec SH-002: Route protection and 401 contract
- **Objective**: Protect the expensive endpoint while preserving the
  anonymous try-it path.
- **Acceptance Criteria**:
  - `POST /api/v1/recommendations` declares `identity: IdentityDep`.
  - With `auth_required=true` and no valid key -> 401
    `{"error": {"code": "unauthorized", ...}}` with `WWW-Authenticate:
    Bearer`; the LLM pipeline is never invoked.
  - With `anonymous_enabled=false` and no key -> same 401.
  - `GET /healthz` and `GET /readyz` remain unauthenticated.
  - Invalid-credential requests do not consume rate-limit budget (the 401 is
    raised before the limiter check).
  - Integration tests: anonymous happy path still 200; bad key 401 with the
    uniform envelope; `auth_required=true` blocks anonymous; health routes
    open.
- **Affected Components**: `api/routes/recommendations.py`, `errors.py`
  (`UnauthorizedError`, code `unauthorized`, 401), `api/errors.py`.
- **Contracts**: 401 `unauthorized` error code; `WWW-Authenticate: Bearer`.
- **Guardrails**: auth check runs before any parsing, conversion, or LLM
  call; health probes stay open.
- **Dependencies**: SH-001.

### Spec SH-003: Key management ergonomics and docs
- **Objective**: Make key rotation and provisioning operational.
- **Acceptance Criteria**:
  - `.env.example` documents `API_KEYS`, `API_KEYS_FILE`, `AUTH_REQUIRED`,
    `ANONYMOUS_ENABLED` with defaults and a generation hint
    (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
  - README gains an "Authentication" section: header format, anonymous
    tier, key generation, rotation (add new key, deploy, remove old).
  - Multiple keys may be active simultaneously (rotation without downtime).
- **Affected Components**: `.env.example`, `README.md`.
- **Contracts**: documentation only.
- **Guardrails**: no example keys that look real; no secrets in docs.
- **Dependencies**: SH-001.

### Spec SH-004: Rate limiter (Protocol + sliding window)
- **Objective**: Per-identity, configurable request budgets.
- **Acceptance Criteria**:
  - `ratelimit.py` (**new**) defines:
    - `RateLimitDecision` dataclass: `allowed: bool`, `limit: int`,
      `remaining: int`, `reset_epoch: float`, `retry_after_seconds: int`.
    - `RateLimiter` Protocol: `check(identity: Identity) ->
      RateLimitDecision`.
    - `SlidingWindowRateLimiter(limits: dict[IdentityKind,
      WindowLimit])`: thread-safe sliding window per identity key
      (`key_id` or `ip`), lazy pruning, bounded memory (max tracked
      identities with LRU eviction so anonymous IP flooding cannot grow
      state unboundedly).
  - `config.py` gains: `rate_limit_enabled: bool = True`,
    `rate_limit_auth_requests: int = 60`,
    `rate_limit_auth_window_seconds: int = 60`,
    `rate_limit_anon_requests: int = 5`,
    `rate_limit_anon_window_seconds: int = 3600`,
    `rate_limit_max_tracked_identities: int = 10_000`.
  - Unit tests: window boundary behavior (request allowed at window start,
    blocked within, allowed after expiry); per-identity isolation; LRU
    eviction of stale identities; thread safety under small concurrency;
    disabled limiter allows everything.
- **Affected Components**: `ratelimit.py` (**new**), `config.py`.
- **Contracts**: `RateLimiter.check(identity) -> RateLimitDecision`.
- **Guardrails**: bounded state (LRU on tracked identities); no wall-clock
  sleeps in the check path; Protocol seam for Redis later.
- **Dependencies**: SH-001 (identity).

### Spec SH-005: 429 enforcement and rate-limit headers
- **Objective**: Enforce limits at the route with a standard HTTP contract.
- **Acceptance Criteria**:
  - `recommendations.py` checks the limiter after identity resolution,
    before body parsing/conversion. Denied -> `RateLimitedError` (429, code
    `rate_limited`) with `Retry-After` seconds; the LLM pipeline is never
    invoked.
  - Successful and denied responses carry:
    `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
    (epoch seconds). 429 additionally carries `Retry-After`.
  - `api/errors.py` maps `RateLimitedError` with the `Retry-After` header.
  - Integration tests: burst past the anonymous limit -> 429 with headers;
    `Retry-After` decrements to allow again; authenticated tier has its own
    budget; 401 path does not consume budget.
- **Affected Components**: `api/routes/recommendations.py`,
  `api/errors.py`, `errors.py`.
- **Contracts**: 429 `rate_limited`; `X-RateLimit-*`, `Retry-After` headers.
- **Guardrails**: limiter check precedes all expensive work; headers on both
  success and denial (clients can self-throttle).
- **Dependencies**: SH-002, SH-004.

### Spec SH-006: Conversion concurrency cap
- **Objective**: Bound threadpool occupancy so slow conversions cannot
  starve the event loop's worker pool even under a rate-limit bypass
  (multi-worker, proxy misconfig).
- **Acceptance Criteria**:
  - `main.py` builds an `anyio.CapacityLimiter(total_tokens=settings.convert_concurrency)`
    (new setting, default `4`) and exposes it via `app.state`.
  - `RecommendationService.recommend` wraps the `run_sync(_convert, ...)`
  call (and only it) in the limiter.
  - While saturated, additional requests wait (async, no thread blocked) and
    remain subject to their rate-limit window; the conversion deadline
    (SH-008) applies to waiters too.
  - Unit/integration tests: with concurrency 1 and a blocked fake converter,
    a second request waits; after the first completes, the second proceeds;
    limiter is not held during LLM calls.
- **Affected Components**: `main.py`, `services/recommendation.py`,
  `config.py`.
- **Contracts**: `convert_concurrency` setting; `app.state.convert_limiter`.
- **Guardrails**: the limiter scopes only the sync conversion, never the
  async LLM calls (those are bounded by rate limits + provider timeouts).
- **Dependencies**: SH-001 (identity, for coherent testing), independent of
  SH-004/005 in code.

### Spec SH-007: PDF structural caps
- **Objective**: Close H2. Cap every attacker-chosen structural dimension of
  a PDF before work scales with it.
- **Acceptance Criteria**:
  - `config.py` gains: `max_pdf_pages: int = 50`,
    `max_images_per_page: int = 20`, `max_page_inches: float = 30.0`.
  - `pdf_converter.py` `convert()`:
    - After `pdfplumber.open`, if `len(pdf.pages) > max_pdf_pages` ->
      `DocumentTooComplexError`.
    - Per page, if width or height exceeds `max_page_inches * 72` points ->
      `DocumentTooComplexError`.
    - `_extract_page_images` stops at `max_images_per_page` images per page
      (excess images skipped; a marker is not needed - text-layer extraction
      still runs).
  - `errors.py` gains `DocumentTooComplexError` (code
    `document_too_complex`, HTTP 422, message names the violated cap).
  - Caps are checked before any image decoding or OCR call for that scope.
  - Unit tests: 51-page PDF rejected fast; oversized page rejected; page
    with 21 embedded images processes the first 20 and skips the rest;
    legitimate 3-page resume unaffected; error maps to 422
    `document_too_complex` (not re-wrapped as `conversion_failed`).
- **Affected Components**: `services/ocr/pdf_converter.py`,
  `services/document_converter.py` (re-raise passthrough), `errors.py`,
  `config.py`.
- **Contracts**: `document_too_complex` -> 422; three new settings.
- **Guardrails**: caps are config, not constants; checks precede decode/OCR
  work; fail-loud (consistent with FP-ADR-006).
- **Dependencies**: none (independent of Phase 0/1).

### Spec SH-008: Image decoding bounds and conversion deadline
- **Objective**: Cap decoded-pixel memory and wall-clock time for the
  conversion stage.
- **Acceptance Criteria**:
  - `document_converter.py` sets `Image.MAX_IMAGE_PIXELS =
    settings.max_image_pixels` (new setting, default `50_000_000`, roughly
    a 150 MB RGB ceiling) at converter construction - explicit, not the
    Pillow default (~179 M).
  - `_wrap_image_as_pdf` additionally rejects images whose width or height
    exceeds `max_image_dimension` (new setting, default `10_000` px) with
    `InvalidDocumentError`.
  - A wall-clock deadline (`convert_deadline_seconds`, new setting, default
    `30.0`) wraps the sync conversion in `recommendation.py`:
    `wait_for(run_sync(...), timeout=...)`. On expiry ->
    `DocumentConversionError` ("conversion timed out"), the threadpool task
    is cancelled at the next await point, and the capacity limiter token is
    released (SH-006).
  - Unit tests: decompression-bomb image rejected; oversized-dimension image
    rejected; a sleeping fake converter triggers the deadline error;
    deadline error maps to 422 `conversion_failed`.
- **Affected Components**: `services/document_converter.py`,
  `services/recommendation.py`, `config.py`.
- **Contracts**: `max_image_pixels`, `max_image_dimension`,
  `convert_deadline_seconds` settings.
- **Guardrails**: deadline covers limiter wait + conversion, not LLM calls
  (those have their own timeout); explicit pixel ceiling, never the library
  default.
- **Dependencies**: SH-007 (same files), SH-006 (deadline interacts with the
  limiter).

### Spec SH-009: Request-ID validation
- **Objective**: Close M2. The reflected `X-Request-ID` cannot inject into or
  flood logs.
- **Acceptance Criteria**:
  - `main.py` `_get_request_id` accepts only `^[A-Za-z0-9_-]{1,64}$`;
    anything else (including over-length) is replaced with `uuid4().hex`.
  - Unit tests: valid UUID passes through; 65+ char header replaced; header
    with newline/quote/unicode replaced; generated ID used when absent.
- **Affected Components**: `main.py`.
- **Contracts**: `X-Request-ID` response header remains, but content is
  validated.
- **Guardrails**: allowlist charset + hard length cap; no rejection (just
  replacement) so clients with odd proxies are never broken.
- **Dependencies**: none.

### Spec SH-010: Docs and OpenAPI gating
- **Objective**: Close M3. No free API-surface disclosure in production.
- **Acceptance Criteria**:
  - `config.py` gains `docs_enabled: bool` defaulting to
    `environment == "development"`.
  - `create_app` passes `docs_url="/docs" if docs_enabled else None`,
    `redoc_url=None`, `openapi_url="/openapi.json" if docs_enabled else None`.
  - Integration tests: development mode serves `/docs` and
    `/openapi.json`; production mode returns 404 for all three.
- **Affected Components**: `main.py`, `config.py`.
- **Contracts**: none (routes appear/disappear).
- **Guardrails**: tied to the existing `environment` setting; explicit
  override available for staging.
- **Dependencies**: none.

### Spec SH-011: Validation-error sanitization
- **Objective**: Close M4. Pydantic internals and reflected inputs stay
  server-side.
- **Acceptance Criteria**:
  - The `RequestValidationError` handler logs `exc.errors()` (full detail,
    server-side) but returns only
    `{"error": {"code": "validation_error", "message": "Request validation
    failed."}}` - no `detail` key.
  - Integration test: uploading a malformed multipart field returns 422
    without echoing input fragments or model paths.
- **Affected Components**: `api/errors.py`.
- **Contracts**: response body for `validation_error` loses the `detail`
  field (additive-removal; documented in `api-contract.md`).
- **Guardrails**: log detail preserved for debugging; client body generic.
- **Dependencies**: none.

### Spec SH-012: Configurable CORS
- **Objective**: Close M5. Replace `allow_origins=["*"]` with an explicit
  allowlist.
- **Acceptance Criteria**:
  - `config.py` gains `cors_origins: str = ""` (comma-separated). Empty ->
    no CORSMiddleware installed (pure API, no browser frontend today).
  - Non-empty -> middleware installed with exactly those origins,
    `allow_methods=["POST", "GET", "OPTIONS"]`, `allow_headers` limited to
    what the API uses (`Authorization`, `Content-Type`,
    `X-Request-ID`), `allow_credentials=False` (explicit).
  - Integration tests: no middleware when unset; allowed origin gets CORS
    headers; disallowed origin does not.
- **Affected Components**: `main.py`, `config.py`.
- **Contracts**: `CORS_ORIGINS` setting.
- **Guardrails**: default-deny; credentials never allowed; wildcard origins
  rejected at startup if explicitly configured (`*` in the list -> startup
  error) to prevent config foot-guns.
- **Dependencies**: none.

### Spec SH-013: Diagnostics gating and security headers
- **Objective**: Close L1, L2, L5. Diagnostics are opt-in for operators;
  baseline security headers are present.
- **Acceptance Criteria**:
  - `?include_meta=true` is honored only for authenticated identities
    (`kind == "key"`) or in development mode; anonymous requests requesting
    it get the default (meta omitted), not an error.
  - `/readyz` includes `model` only when `environment == "development"` or
    the caller presents a valid key; otherwise the 200 body is
    `{"status": "ready"}`.
  - A small middleware adds on every response: `X-Content-Type-Options:
    nosniff`, `Referrer-Policy: no-referrer`,
    `X-Frame-Options: DENY`. (`Strict-Transport-Security` is documented as a
    reverse-proxy responsibility, not set in-app.)
  - Integration tests: anonymous `include_meta=true` omits meta; keyed
    request includes it; production `readyz` hides the model; headers
    present on all responses including errors.
- **Affected Components**: `api/routes/recommendations.py`,
  `api/routes/health.py`, `api/middleware.py` (**new**), `main.py`.
- **Contracts**: `meta` visibility rules; `/readyz` body variance.
- **Guardrails**: diagnostics gating never changes the success/failure of a
  request; headers are static strings.
- **Dependencies**: SH-001 (identity for the gating rules), SH-010 (same
  environment flag).

### Spec SH-014: Prompt delimiter escaping
- **Objective**: Close M1 (part 1). Resume/profile content cannot forge or
  close prompt delimiters from inside the data.
- **Acceptance Criteria**:
  - `prompts.py` gains `sanitize_untrusted(text: str) -> str` replacing
    `<resume>`, `</resume>`, `<profile>`, `</profile>` (case-insensitive)
    with look-alike bracket forms, applied inside `build_user_prompt` and
    `build_profile_prompt` to every embedded block.
  - The system prompts note that the delimiters are canonical and any
    delimiter-looking text inside the data is content (existing
    untrusted-data rule extended with one sentence).
  - Unit tests: a resume containing `</resume><system>...` round-trips as
    inert content; legitimate resumes with angle brackets in prose are
    minimally altered (only exact delimiter sequences change); both prompt
    builders apply the sanitizer.
- **Affected Components**: `services/prompts.py`,
  `tests/unit/test_prompts.py`.
- **Contracts**: prompt assembly only; no API change.
- **Guardrails**: sanitizer applied at the prompt boundary (single choke
  point), not scattered at call sites; cache stores pre-sanitizer markdown
  (sanitization is deterministic and cheap, re-applied per prompt build).
- **Dependencies**: none.

### Spec SH-015: Output URL constraint and OCR error hygiene
- **Objective**: Close M1 (part 2) and L3. Model-steered output links are
  https-only; upstream exception strings never reach prompts.
- **Acceptance Criteria**:
  - `LearningResource.url` gains `pattern=r"^https://"` (Pydantic field
    constraint). A model emitting `http://` or other schemes fails
    validation -> the existing corrective retry handles it.
  - `pdf_converter.py` replaces `f"*[Error processing page {n}: {exc}]*"`
    with a static marker `*[Error processing page]*`; the exception is
    logged server-side instead.
  - Unit tests: `http://` URL rejected at the schema; https URL accepted;
    OCR failure path emits the static marker and logs (capped) the
    exception.
- **Affected Components**: `schemas/recommendation.py`,
  `services/ocr/pdf_converter.py`.
- **Contracts**: `education_materials[].url` is https-only (documented as a
  tightening; the corrective retry makes it invisible to well-behaved
  models).
- **Guardrails**: schema-enforced (not prompt-enforced); no exception text
  in any string that reaches an LLM message.
- **Dependencies**: none.

### Spec SH-016: Verification (test suite + integration matrix)
- **Objective**: Lock every new behavior; keep all existing tests green.
- **Acceptance Criteria**:
  - New unit suites: `tests/unit/test_auth.py`, `test_ratelimit.py`,
    `test_structural_caps.py`, `test_middleware.py` (request-ID, security
    headers), updated `test_prompts.py` (escaping), `test_schemas.py` (URL
    constraint).
  - New integration suite `tests/api/test_security.py` covering the matrix:
    anonymous allowed within limit; 401 paths (bad key, bad scheme,
    anonymous-disabled, auth-required); 429 with headers and recovery;
    structural-cap 422s; docs 404 in production; sanitized validation
    errors; CORS behavior; meta/readyz gating.
  - All existing suites pass unmodified except where the contract
    intentionally changed (validation-error body, readyz body in
    production) - those tests are updated with a comment referencing the
    spec ID.
  - `ruff` and `mypy --strict` pass; `pytest` fully green; no test requires
    network or real keys (fakes for `ApiKeyStore` inputs and the limiter
    where appropriate).
  - Test isolation: new config-dependent tests construct `Settings`
    explicitly (no local `.env` leakage).
- **Affected Components**: `tests/**`, `tests/conftest.py`.
- **Contracts**: fakes implement the new Protocols cleanly.
- **Guardrails**: no timing-flaky tests; sliding-window tests use injected
  clock or generous margins; concurrency tests use small thread counts.
- **Dependencies**: SH-001..SH-015.

### Spec SH-017: Release docs + env example
- **Objective**: Make the hardening runnable and auditable.
- **Acceptance Criteria**:
  - `.env.example` documents every new setting with defaults.
  - `README.md`: Authentication section (SH-003), rate-limit semantics and
    headers, new error rows (`unauthorized` 401, `rate_limited` 429,
    `document_too_complex` 422), updated settings table, deployment notes
    (reverse proxy, TLS/HSTS, shared limiter note for multi-worker).
  - `docs/03-security-hardening/architecture.md` gains final ADR statuses
    and a changelog section.
  - `docs/01-initial-plan/` and `docs/02-extraction-fidelity/` remain
    untouched (historical records).
- **Affected Components**: `.env.example`, `README.md`,
  `docs/03-security-hardening/architecture.md`.
- **Contracts**: documentation only.
- **Guardrails**: no secrets; `.env` stays ignored.
- **Dependencies**: SH-016.

---

## Execution Sequence

Ordered by dependency; grouped into phases. Items in the same phase are
parallelizable where noted.

**Phase 0 - Authentication** (closes the "who" half of H1)
1. SH-001 (identity + key store) - unblocks everything
2. SH-002 (route protection + 401)
3. SH-003 (key docs)

**Phase 1 - Rate limiting** (closes the "how much" half of H1)
4. SH-004 (limiter core) - after SH-001
5. SH-005 (429 + headers) - after SH-002, SH-004
6. SH-006 (conversion concurrency cap) - independent of SH-004/005, can run
   in parallel

**Phase 2 - Document parsing bounds** (closes H2; independent of Phases 0-1)
7. SH-007 (PDF structural caps) - can start immediately in parallel
8. SH-008 (image bounds + deadline) - after SH-007 (same files), pairs with
   SH-006 for the deadline/limiter interaction

**Phase 3 - HTTP surface** (closes M2-M5, L1, L2, L5)
9. SH-009 (request-ID) - independent, parallel
10. SH-010 (docs gating) - independent, parallel
11. SH-011 (validation sanitization) - independent, parallel
12. SH-012 (CORS) - independent, parallel
13. SH-013 (diagnostics gating + headers) - after SH-001, SH-010

**Phase 4 - LLM content hardening** (closes M1, L3)
14. SH-014 (delimiter escaping) - independent, parallel
15. SH-015 (URL constraint + OCR markers) - independent, parallel

**Phase 5 - Verification & release**
16. SH-016 (test matrix) - after all above
17. SH-017 (docs + env) - after SH-016

Parallelization: Phase 2 is fully independent of Phases 0-1 and can proceed
concurrently. Within Phase 3, SH-009..012 are mutually independent. SH-016
fires continuously per spec (each spec carries its own unit tests; SH-016 is
the integration matrix + final sweep).

---

## Open Questions and Risks

### Open questions (confirm before locking)
1. **Anonymous tier default** - `anonymous_enabled=true` with 5 req/hour
   preserves the product's zero-friction promise, but if this deploys
   publicly before Phase 1 lands, Phase 0 alone leaves anonymous users
   unlimited. Interim mitigation: deploy Phases 0 and 1 together, or set
   `anonymous_enabled=false` until Phase 1 merges.
2. **Key distribution** - static keys in env assume a small, operator-managed
   caller set. If self-service signup is ever needed, this graduates to an
   accounts system (out of scope; SH-ADR-001 records the decision).
3. **Rate-limit fairness across workers** - the in-process limiter is
   per-worker, so N workers multiply the effective limit by N. Acceptable
   for the default single-worker deployment; the Protocol seam exists for
   Redis if multi-worker arrives. Confirm single-worker is the deployment
   target.
4. **`document_too_complex` vs `invalid_document`** - a distinct 422 code
   keeps the audit trail explicit (structural violation vs unreadable
   file). Confirm no client depends on the old code for oversized
   *structural* inputs (byte-size overflow remains `document_too_large`
   413, unchanged).

### Risks
- **Anonymous abuse before Phase 1**: mitigated by the open question above
  (deploy 0+1 together or disable anonymous until then).
- **Rate limiter memory under IP flooding**: anonymous identity keys are
  client IPs; a botnet could create many windows. Mitigated by
  `rate_limit_max_tracked_identities` LRU eviction - worst case, eviction
  lets a flooded identity restart its window (bounded harm, bounded memory).
- **Structural caps rejecting legitimate documents**: real resumes are
  1-3 pages; the 50-page cap is 10x headroom over the OCR budget (10
  pages). Scanned portfolio PDFs beyond 50 pages fail with a clear message
  and the cap is configurable.
- **Conversion deadline kills slow-but-valid conversions**: OCR of 10
  scanned pages at provider latency (~5-10 s/page worst case) can approach
  30 s. Mitigation: deadline is configurable; default chosen above the
  p99 of legitimate conversions; the error message distinguishes timeout
  from corruption.
- **https-only URL constraint vs model behavior**: models occasionally emit
  `http://` for real resources; the corrective retry usually fixes it, but
  repeated failure would 422. Acceptable: a dropped link is better than an
  attacker-stealed one; revisit with an allowlist if it bites.
- **Breaking-ish contract changes**: removing `detail` from validation
  errors and gating `/readyz` model info are intentional tightenings;
  existing clients that read those fields must be checked (none known -
  the service has no deployed consumers yet).
- **Key leakage via config**: keys arrive via env/file; the file mode
  should be `0600` and never inside the repo. `.gitignore` already covers
  `.env*`; `API_KEYS_FILE` path must be documented as outside the repo.
