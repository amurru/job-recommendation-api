# Task Board: Security Hardening (SH)

Status convention:
- `[x]` pending
- `[~]` in progress
- `[x]` complete
- `[!]` blocked (note the blocker)

Update this file as work progresses. Each task references its spec in
[`PLAN.md`](./PLAN.md).

## Phase 0 - Authentication

| ID | Task | Spec | Status | Notes |
|----|------|------|--------|-------|
| SH-001.1 | Add `api_keys`, `api_keys_file`, `auth_required`, `anonymous_enabled` settings | SH-001 | `[x]` | |
| SH-001.2 | Create `auth.py`: `Identity`, `IdentityKind`, `ApiKeyStore` (SHA-256 digests, constant-time compare) | SH-001 | `[x]` | Plaintext keys not retained after construction |
| SH-001.3 | Add `get_identity` dependency in `api/deps.py` (Bearer parsing, anonymous fallback, 401 on invalid) | SH-001 | `[x]` | |
| SH-001.4 | Unit tests: key resolution, 401 paths, anonymous config flip, key-file parsing, no-plaintext-in-store assertion | SH-001 | `[x]` | |
| SH-002.1 | Add `UnauthorizedError` (401, code `unauthorized`) to `errors.py`; wire handler | SH-002 | `[x]` | Include `WWW-Authenticate: Bearer` |
| SH-002.2 | Declare `IdentityDep` on `POST /api/v1/recommendations`; enforce `auth_required` / `anonymous_enabled` before any parsing | SH-002 | `[x]` | Health routes stay open |
| SH-002.3 | Integration tests: anonymous 200, bad key 401 envelope, auth-required blocks anonymous, healthz/readyz open | SH-002 | `[x]` | |
| SH-003.1 | Document auth in `.env.example` + README "Authentication" section (header format, key generation, rotation) | SH-003 | `[x]` | |

## Phase 1 - Rate Limiting

| ID | Task | Spec | Status | Notes |
|----|------|------|--------|-------|
| SH-004.1 | Add `rate_limit_*` settings (enabled, auth/anon requests + windows, max tracked identities) | SH-004 | `[x]` | |
| SH-004.2 | Create `ratelimit.py`: `RateLimitDecision`, `RateLimiter` Protocol, `SlidingWindowRateLimiter` (per-identity windows, LRU-bounded state, thread-safe) | SH-004 | `[x]` | |
| SH-004.3 | Unit tests: window boundaries, per-identity isolation, LRU eviction, thread safety, disabled mode | SH-004 | `[x]` | Injected clock or generous margins; no flaky timing |
| SH-005.1 | Add `RateLimitedError` (429, code `rate_limited`); handler emits `Retry-After` | SH-005 | `[x]` | |
| SH-005.2 | Limiter check in recommendations route after identity, before body parsing; attach `X-RateLimit-Limit/Remaining/Reset` to success + denial | SH-005 | `[x]` | 401 path consumes no budget |
| SH-005.3 | Integration tests: anon burst -> 429 with headers, recovery after window, separate auth budget, 401 no-consumption | SH-005 | `[x]` | |
| SH-006.1 | Add `convert_concurrency` setting; build `anyio.CapacityLimiter` in lifespan, expose via `app.state` | SH-006 | `[x]` | |
| SH-006.2 | Wrap `run_sync(_convert, ...)` in the limiter inside `RecommendationService` (LLM calls excluded) | SH-006 | `[x]` | |
| SH-006.3 | Tests: second request waits while saturated; token released after; limiter not held during LLM calls | SH-006 | `[x]` | |

## Phase 2 - Document Parsing Bounds

| ID | Task | Spec | Status | Notes |
|----|------|------|--------|-------|
| SH-007.1 | Add `max_pdf_pages`, `max_images_per_page`, `max_page_inches` settings | SH-007 | `[x]` | |
| SH-007.2 | Add `DocumentTooComplexError` (422, code `document_too_complex`) | SH-007 | `[x]` | |
| SH-007.3 | Enforce page-count + page-dimension caps in `pdf_converter.convert` before per-page work | SH-007 | `[x]` | |
| SH-007.4 | Cap `_extract_page_images` at `max_images_per_page` (skip excess) | SH-007 | `[x]` | |
| SH-007.5 | Tests: 51-page reject, oversized page reject, 21-image page processes 20, legit resume unaffected, 422 mapping | SH-007 | `[x]` | |
| SH-008.1 | Add `max_image_pixels`, `max_image_dimension`, `convert_deadline_seconds` settings | SH-008 | `[x]` | |
| SH-008.2 | Set explicit `Image.MAX_IMAGE_PIXELS` at converter construction; dimension cap in `_wrap_image_as_pdf` | SH-008 | `[x]` | Never rely on Pillow default |
| SH-008.3 | Wrap conversion in wall-clock deadline (`wait_for`), map expiry to `conversion_failed`, ensure limiter token release | SH-008 | `[x]` | Deadline covers limiter wait + conversion |
| SH-008.4 | Tests: bomb image rejected, oversized dimensions rejected, sleeping converter hits deadline, error mapping | SH-008 | `[x]` | |

## Phase 3 - HTTP Surface Hardening

| ID | Task | Spec | Status | Notes |
|----|------|------|--------|-------|
| SH-009.1 | Validate `X-Request-ID` (`^[A-Za-z0-9_-]{1,64}$`), replace invalid with `uuid4().hex` | SH-009 | `[x]` | Replacement, not rejection |
| SH-009.2 | Tests: UUID passthrough, overlength replaced, hostile chars replaced, absent -> generated | SH-009 | `[x]` | |
| SH-010.1 | Add `docs_enabled` (default: development); gate `docs_url` / `openapi_url` in `create_app` | SH-010 | `[x]` | |
| SH-010.2 | Integration tests: dev serves /docs + /openapi.json; production 404s all three | SH-010 | `[x]` | |
| SH-011.1 | Sanitize `RequestValidationError` response (drop `detail`; keep full log server-side) | SH-011 | `[x]` | Intentional contract tightening |
| SH-011.2 | Integration test: malformed multipart -> 422 without echoed input fragments | SH-011 | `[x]` | |
| SH-012.1 | Add `cors_origins` setting; install CORSMiddleware only when non-empty; restricted methods/headers; reject `*` at startup | SH-012 | `[x]` | Default: no middleware |
| SH-012.2 | Integration tests: middleware absent when unset; allowed origin gets headers; disallowed does not | SH-012 | `[x]` | |
| SH-013.1 | Create `api/middleware.py`: security headers (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`) on all responses | SH-013 | `[x]` | HSTS documented as proxy concern |
| SH-013.2 | Gate `?include_meta=true` to keyed identities or development mode | SH-013 | `[x]` | Anonymous requesting it: meta silently omitted |
| SH-013.3 | Gate `/readyz` model disclosure to development mode or valid key | SH-013 | `[x]` | |
| SH-013.4 | Tests: meta gating matrix, readyz body variance, headers on success + error responses | SH-013 | `[x]` | |

## Phase 4 - LLM Content Hardening

| ID | Task | Spec | Status | Notes |
|----|------|------|--------|-------|
| SH-014.1 | Add `sanitize_untrusted` to `prompts.py`; apply in `build_user_prompt` + `build_profile_prompt`; extend system-prompt untrusted rule | SH-014 | `[x]` | Single choke point at prompt boundary |
| SH-014.2 | Tests: delimiter forgery neutralized, prose angle brackets minimally altered, both builders sanitize | SH-014 | `[x]` | |
| SH-015.1 | Constrain `LearningResource.url` to https (`pattern=r"^https://"`) | SH-015 | `[x]` | Corrective retry absorbs http-only emissions |
| SH-015.2 | Replace OCR exception interpolation with static marker `*[Error processing page]*`; log exception server-side | SH-015 | `[x]` | |
| SH-015.3 | Tests: http URL rejected at schema, https accepted, static marker emitted on OCR failure | SH-015 | `[x]` | |

## Phase 5 - Verification & Release

| ID | Task | Spec | Status | Notes |
|----|------|------|--------|-------|
| SH-016.1 | New unit suites: `test_auth.py`, `test_ratelimit.py`, `test_structural_caps.py`, `test_middleware.py` | SH-016 | `[x]` | |
| SH-016.2 | New integration suite `tests/api/test_security.py` (full matrix from PLAN SH-016) | SH-016 | `[x]` | |
| SH-016.3 | Update intentionally-changed existing tests with spec-ID comments; full `pytest` green; `ruff` + `mypy --strict` clean | SH-016 | `[x]` | |
| SH-017.1 | `.env.example`: all new settings with defaults | SH-017 | `[x]` | |
| SH-017.2 | README: Authentication, rate-limit semantics/headers, new error rows, settings table, deployment notes | SH-017 | `[x]` | |
| SH-017.3 | Finalize `architecture.md` ADR statuses + changelog | SH-017 | `[x]` | |

## Execution order reminder

- Phase 2 (SH-007/008) is independent of Phases 0-1 and can proceed in
  parallel from day one.
- Within Phase 1, SH-006 is independent of SH-004/005.
- Within Phase 3, SH-009..012 are mutually independent; SH-013 needs
  SH-001 + SH-010.
- SH-016/017 close the feature.
