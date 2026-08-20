# Job Recommendation API

A no-authentication FastAPI service that turns a resume PDF into actionable
career guidance.

```
POST multipart PDF -> markitdown (PDF -> Markdown) -> OpenRouter LLM
                   -> validate JSON -> RecommendationResponse
```

The response contains a resume summary, ranked job recommendations with fit
scores, and education materials to close skills gaps.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness check. Always `200` when the process is up. |
| `GET` | `/readyz` | Readiness check. `200` when the OpenRouter API key is set, else `503`. |
| `POST` | `/api/v1/recommendations` | Accepts a PDF resume (`multipart/form-data`, field `file`) and returns validated recommendations. |

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
curl -X POST http://localhost:8000/api/v1/recommendations \
  -H "Content-Type: multipart/form-data" \
  -F "file=@resume.pdf"
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
    "markdown_length": 4321
  }
}
```

## Errors

Non-2xx responses use a uniform envelope:

```json
{ "error": { "code": "invalid_document", "message": "The file is not a valid PDF." } }
```

| HTTP | `error.code` | Meaning |
|------|--------------|---------|
| 400 | `invalid_document` | Not a PDF / unreadable document |
| 413 | `document_too_large` | Exceeds upload size cap |
| 415 | `unsupported_media_type` | Non-PDF content type |
| 422 | `conversion_failed` | PDF converted to no usable text |
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
| `LLM_TIMEOUT_SECONDS` | `60` | LLM call timeout in seconds. |
| `LLM_MAX_TOKENS` | `2048` | Max tokens for the LLM response. |
| `MAX_UPLOAD_BYTES` | `10485760` | Max accepted PDF size (10 MiB). |
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

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
uv run pytest                # test (58 tests)
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