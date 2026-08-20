# Job Recommendation API

A no-authentication FastAPI service that turns a resume PDF or photo into
actionable career guidance.

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
| `GET` | `/readyz` | Readiness check. `200` when the OpenRouter API key is set, else `503`. |
| `POST` | `/api/v1/recommendations` | Accepts a resume as a PDF or photo (`multipart/form-data`, field `file`; `application/pdf`, `image/jpeg`, `image/png`, `image/webp`) and returns validated recommendations. |

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
`http://localhost:8000/docs`.

### curl example

```bash
# PDF resume
curl -X POST http://localhost:8000/api/v1/recommendations \
  -H "Content-Type: multipart/form-data" \
  -F "file=@resume.pdf"

# Photo resume (scanned / photo of a resume)
curl -X POST http://localhost:8000/api/v1/recommendations \
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
    ],
    "education_materials": [
      {
        "topic": "System Design",
        "kind": "book",
        "title": "Designing Data-Intensive Applications",
        "provider": "O'Reilly",
        "url": "https://dataintensive.net/",
        "rationale": "Reinforces distributed systems fundamentals relevant to backend roles."
      }
    ]
  },
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
| 413 | `document_too_large` | Exceeds upload size cap |
| 415 | `unsupported_media_type` | Unsupported content type |
| 422 | `conversion_failed` | Document (or OCR) produced no usable text |
| 422 | `not_a_resume` | Document converted, but does not look like a resume |
| 422 | `ocr_budget_exceeded` | Document requires more OCR pages than `MAX_OCR_PAGES` allows |
| 422 | `llm_invalid_output` | Model returned malformed/non-schema JSON |
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
uv run pytest                # test (140 tests)
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
  a threadpool.
- Structured LLM output (`json_schema`) plus mandatory Pydantic re-validation.
- Typed domain errors mapped to a uniform HTTP envelope at the edge.