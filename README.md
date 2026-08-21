# Job Recommendation API

A FastAPI service that turns a resume PDF or photo into actionable career
guidance. Anonymous callers get a small free tier; API keys unlock the
standard tier.

```
POST multipart document -> markitdown (+ OCR for scanned/photo resumes)
                        -> OpenRouter LLM
                        -> validate JSON -> RecommendationResponse
```

The response contains a resume summary, ranked job recommendations with fit
scores, and education materials to close skills gaps.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness check. Always `200` when the process is up. |
| `GET` | `/readyz` | Readiness check. `200` when the OpenRouter API key is set, else `503`. The `model` field is shown in development or to a valid API key. |
| `POST` | `/api/v1/recommendations` | Accepts a resume as a PDF or photo (`multipart/form-data`, field `file`; `application/pdf`, `image/jpeg`, `image/png`, `image/webp`) and returns validated recommendations. |

## Authentication

The recommendations endpoint accepts an optional credential:

```
Authorization: Bearer <api-key>
```

- **Valid key**: authenticated identity, standard rate-limit tier
  (60 req/min per key by default).
- **No header**: anonymous identity (while `ANONYMOUS_ENABLED=true`),
  small free tier (5 req/hour per client IP by default).
- **Invalid key or non-Bearer scheme**: `401 unauthorized`.

Generate a key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keys are configured via `API_KEYS` (comma-separated) or `API_KEYS_FILE`
(one per line, `#` comments, keep the file outside the repo with mode 600).
Keys are stored in memory as SHA-256 digests only; the plaintext is never
logged or returned. The first 12 hex chars of the digest (the `key_id`) are
safe to log.

**Rotation without downtime**: add the new key alongside the old one,
redeploy, then remove the old key in a second deploy.

Set `AUTH_REQUIRED=true` or `ANONYMOUS_ENABLED=false` to make the API
key-only. `GET /healthz` and `GET /readyz` always remain open.

## Rate limiting

Per-identity sliding-window limits (in-process, per worker):

| Tier | Default budget |
|------|----------------|
| Authenticated (`Bearer <key>`) | 60 requests / 60 s per key |
| Anonymous | 5 requests / 3600 s per client IP |

Every recommendations response (success and 429) carries:

| Header | Meaning |
|--------|---------|
| `X-RateLimit-Limit` | Requests allowed per window for this tier. |
| `X-RateLimit-Remaining` | Requests left in the current window. |
| `X-RateLimit-Reset` | Unix epoch seconds when the window resets. |
| `Retry-After` | (429 only) Seconds until the next attempt can succeed. |

Exceeding the budget returns `429 rate_limited`. Invalid credentials (401)
consume no budget. The limiter is per-worker: N workers multiply the
effective limits by N (see Deployment notes). Document conversion is
additionally capped at `CONVERT_CONCURRENCY` concurrent jobs and a
`CONVERT_DEADLINE_SECONDS` wall-clock deadline.

## Quickstart

Requirements: Python 3.14, `uv`.

```bash
uv sync --group dev
```

### Configure

Copy the example env file and set your OpenRouter API key. Environment
variables always win over `.env` values.

```bash
cp .env.example .env
# edit .env and set OPENROUTER_API_KEY
```

### Run

```bash
uv run job-recommendation-api
# or
uv run python -m job_recommendation_api
# or
uv run uvicorn job_recommendation_api.main:app
```

The API listens on `http://0.0.0.0:8000`. Interactive docs are at
`http://localhost:8000/docs` in development mode (`ENVIRONMENT=development`
or `DOCS_ENABLED=true`); production hides them.

### curl example

```bash
# Anonymous (free tier)
curl -X POST http://localhost:8000/api/v1/recommendations \
  -H "Content-Type: multipart/form-data" \
  -F "file=@resume.pdf"

# Authenticated (standard tier)
curl -X POST http://localhost:8000/api/v1/recommendations \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@resume.pdf"

# Photo resume (scanned / photo of a resume)
curl -X POST http://localhost:8000/api/v1/recommendations \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@resume.jpg;type=image/jpeg"
```

Example response:

```json
{
  "analysis": {
    "summary": "Experienced backend engineer focused on Python, APIs, and cloud infrastructure.",
    "top_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes"],
    "jobs": [
      {
        "title": "Senior Backend Engineer",
        "fit_score": 0.87,
        "seniority_level": "senior",
        "rationale": "Strong Python and API design background aligns with the role requirements.",
        "key_skills": ["Python", "FastAPI", "PostgreSQL"]
      }
    ]
  }
}
```

### Response `meta` block

The runtime `meta` block (model, cache state, extraction diagnostics) is
omitted by default in production. It is included when:

- the server runs with `ENVIRONMENT=development`, or
- an authenticated request passes `?include_meta=true`.

Anonymous production requests passing `?include_meta=true` get the default
response (meta omitted), not an error:

```json
{
  "analysis": { "...": "..." },
  "meta": {
    "model": "openai/gpt-4o-mini",
    "markdown_length": 4321,
    "cache": "miss",
    "markdown_truncated": false,
    "dropped_facts": [],
    "injection_lines_removed": 0
  }
}
```

## Extraction cache

Successful extractions (markdown + structured profile) are cached in-process,
keyed by the SHA-256 of the uploaded bytes and the extraction pipeline
version, with a TTL (`EXTRACTION_CACHE_TTL_SECONDS`) and an LRU cap
(`EXTRACTION_CACHE_MAX_ENTRIES`). Re-uploading the same document skips
conversion, OCR, and profile extraction entirely - only the recommendation
call runs (it is never cached). Responses carry `meta.cache` and an
`X-Cache: HIT|MISS` header. The cache is per-worker; multi-worker deployments
degrade to per-worker hit rates.

## Errors

Non-2xx responses use a uniform envelope:

```json
{ "error": { "code": "invalid_document", "message": "The file is not a valid PDF or image document." } }
```

| HTTP | `error.code` | Meaning |
|------|--------------|---------|
| 400 | `invalid_document` | Not a PDF / image / unreadable document |
| 401 | `unauthorized` | Missing/invalid API key (carries `WWW-Authenticate: Bearer`) |
| 413 | `document_too_large` | Exceeds upload size cap |
| 415 | `unsupported_media_type` | Unsupported content type |
| 422 | `validation_error` | Request failed validation (generic message; detail in server logs only) |
| 422 | `conversion_failed` | Document (or OCR) produced no usable text, or conversion timed out |
| 422 | `document_too_complex` | Exceeds structural caps (`MAX_PDF_PAGES`, `MAX_PAGE_INCHES`) |
| 422 | `not_a_resume` | Document converted, but does not look like a resume |
| 422 | `ocr_budget_exceeded` | Document requires more OCR pages than `MAX_OCR_PAGES` allows |
| 422 | `llm_invalid_output` | Model returned malformed/non-schema JSON |
| 429 | `rate_limited` | Identity exceeded its rate-limit window (carries `Retry-After`) |
| 502 | `llm_error` | Upstream OpenRouter/model failure |
| 504 | `llm_timeout` | LLM call exceeded timeout |
| 500 | `configuration_error` | Server misconfiguration |

## Configuration

All settings are environment-driven (`pydantic-settings`, read from `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | *(required)* | OpenRouter API key. |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | Model used for analysis. |
| `OCR_MODEL` | `openai/gpt-4o-mini` | Vision model used for OCR of scanned/photo resumes. |
| `LLM_TIMEOUT_SECONDS` | `60` | LLM call timeout in seconds. |
| `LLM_MAX_TOKENS` | `4096` | Max tokens for the LLM response. |
| `MAX_UPLOAD_BYTES` | `10485760` | Max accepted document size (10 MiB). |
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `PROFILE_MODEL` | `openai/gpt-4o-mini` | Model for the profile extraction stage. |
| `OCR_TEMPERATURE` | `0.0` | Temperature for OCR vision calls (0 = deterministic). |
| `LLM_TEMPERATURE` | `0.0` | Temperature for profile and recommendation calls. |
| `PROFILE_FIDELITY` | `lenient` | `lenient` (drop + log) or `strict` (fail) on unsupported facts. |
| `MAX_OCR_PAGES` | `10` | Per-document OCR page/call budget. |
| `EXTRACTION_CACHE_MAX_ENTRIES` | `256` | LRU cap for the in-process extraction cache. |
| `EXTRACTION_CACHE_TTL_SECONDS` | `3600` | Entry TTL for the extraction cache. |
| `ENVIRONMENT` | `production` | `development` includes the response `meta` block by default and serves `/docs`. |
| `API_KEYS` | *(empty)* | Comma-separated valid API keys (`Authorization: Bearer`). |
| `API_KEYS_FILE` | *(unset)* | File with one key per line (`#` comments); keep outside the repo, mode 600. |
| `AUTH_REQUIRED` | `false` | When true, anonymous requests are rejected (401). |
| `ANONYMOUS_ENABLED` | `true` | When false, keyless requests are rejected (key-only API). |
| `RATE_LIMIT_ENABLED` | `true` | Master switch for rate limiting. |
| `RATE_LIMIT_AUTH_REQUESTS` | `60` | Authenticated requests per window. |
| `RATE_LIMIT_AUTH_WINDOW_SECONDS` | `60` | Authenticated window length. |
| `RATE_LIMIT_ANON_REQUESTS` | `5` | Anonymous requests per window (per client IP). |
| `RATE_LIMIT_ANON_WINDOW_SECONDS` | `3600` | Anonymous window length. |
| `RATE_LIMIT_MAX_TRACKED_IDENTITIES` | `10000` | LRU bound on limiter state. |
| `CONVERT_CONCURRENCY` | `4` | Max concurrent document conversions. |
| `MAX_PDF_PAGES` | `50` | Structural page cap (422 `document_too_complex` beyond it). |
| `MAX_IMAGES_PER_PAGE` | `20` | Embedded images decoded per page (excess skipped). |
| `MAX_PAGE_INCHES` | `30` | Max page edge length in inches. |
| `MAX_IMAGE_PIXELS` | `50000000` | Pillow decompression-bomb ceiling (decoded pixels). |
| `MAX_IMAGE_DIMENSION` | `10000` | Max width/height (px) for uploaded images. |
| `CONVERT_DEADLINE_SECONDS` | `30` | Wall-clock cap on the conversion stage. |
| `CORS_ORIGINS` | *(empty)* | Comma-separated origin allowlist; empty = no CORS middleware. `*` fails startup. |
| `DOCS_ENABLED` | `ENVIRONMENT=development` | Serve `/docs` + `/openapi.json`. |

## Deployment notes

- **Run a single worker** (or accept multiplied limits): the rate limiter and
  extraction cache are in-process. N uvicorn workers multiply the effective
  rate limits and fragment the cache. If you must scale horizontally, put a
  shared limiter (Redis) in front or accept per-worker budgets.
- **TLS / HSTS at the proxy**: the app does not terminate TLS. Terminate TLS
  at a reverse proxy and set `Strict-Transport-Security` there. The app sets
  `X-Content-Type-Options`, `Referrer-Policy`, and `X-Frame-Options` itself.
- **Client IPs behind a proxy**: anonymous rate limiting keys on the socket
  peer. Behind a load balancer, either preserve the client IP at the proxy
  (transparent proxying) or every client shares one bucket - prefer running
  key-only (`ANONYMOUS_ENABLED=false`) in that setup.
- **Key material**: keep `API_KEYS_FILE` outside the repo (mode 600). `.env`
  is git-ignored; never commit keys.
- **Docs exposure**: `/docs` and `/openapi.json` are development-only by
  default; set `DOCS_ENABLED=true` explicitly for a staging environment.

## Development

```bash
make lint                    # ruff lint
make typecheck               # mypy --strict
make test                    # pytest
make format                  # ruff format
make run                     # start the server
```

Or run the underlying tools directly:

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy src tests        # typecheck
uv run pytest                # test (249 tests)
```

Pre-commit hooks (ruff lint, ruff format, mypy) are configured in
`.pre-commit-config.yaml`:

```bash
uv run pre-commit install    # install into .git/hooks
uv run pre-commit run --all-files  # run once against everything
```

## Architecture

See `docs/` for the development blueprint (`PLAN.md`), architecture decisions
(`architecture.md`), task tracking (`tasks.md`), and the full API contract
(`api-contract.md`).

Key decisions:
- Layered architecture: `api -> services -> llm / schemas`, with Protocol
  boundaries around markitdown and the OpenRouter SDK.
- Async LLM client (native `send_async`); the sync markitdown converter runs in
  a threadpool under a concurrency limiter and wall-clock deadline.
- Structured LLM output (`json_schema`) plus mandatory Pydantic re-validation.
- Typed domain errors mapped to a uniform HTTP envelope at the edge.
- Security hardening (see `docs/03-security-hardening/`): hashed API keys,
  per-identity sliding-window rate limits, PDF structural caps, prompt
  delimiter escaping, and https-only output URLs.