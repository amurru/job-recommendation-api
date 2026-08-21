# API Contract

Base path: `/api/v1`

## Endpoints

### `GET /healthz` - liveness

No dependencies. Always responds when the process is up.

```json
{ "status": "ok" }
```

### `GET /readyz` - readiness

Checks that required configuration (OpenRouter API key) is present.

```json
// 200
{ "status": "ready", "model": "openai/gpt-4o-mini" }

// 503
{ "status": "unready", "reason": "OPENROUTER_API_KEY is not set" }
```

### `POST /api/v1/recommendations` - analyze resume and recommend

Multipart upload of a resume as a PDF or a photo (JPEG/PNG/WebP). Returns job +
education recommendations. Scanned/photo resumes are read with an LLM-vision
OCR pass so image-only documents still produce usable text.

**Request**

- Content-Type: `multipart/form-data`
- Field `file`: the document. Accepted content types:
  `application/pdf`, `image/jpeg`, `image/png`, `image/webp`.

**Validation**

- `file` content type must be one of the accepted types -> else `415`.
- `file` size must not exceed `max_upload_bytes` (default 10 MiB) -> else `413`.
- Empty / unparseable / no-text document (including OCR failures) -> `422`.

**Response `200`**

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

**Field definitions**

| Path | Type | Constraint |
|------|------|-----------|
| `analysis.summary` | string | 1..1000 chars |
| `analysis.top_skills` | string[] | 1..20 items |
| `analysis.jobs[].title` | string | non-empty |
| `analysis.jobs[].fit_score` | number | 0.0..1.0 |
| `analysis.jobs[].seniority_level` | string | intern/junior/mid/senior/staff/principal/executive (enum) |
| `analysis.jobs[].rationale` | string | non-empty |
| `analysis.jobs[].key_skills` | string[] | 0..20 items |
| `analysis.education_materials[].topic` | string | non-empty |
| `analysis.education_materials[].kind` | string | course/book/certification/tutorial/project |
| `analysis.education_materials[].title` | string | non-empty |
| `analysis.education_materials[].provider` | string \| null | optional (default `null`) |
| `analysis.education_materials[].url` | URL \| null | optional (default `null`); valid URL if present |
| `analysis.education_materials[].rationale` | string | non-empty |
| `meta.model` | string | model identifier used |
| `meta.markdown_length` | integer \| null | length of extracted Markdown |

## Error model

Uniform envelope for all non-2xx application errors:

```json
{ "error": { "code": "invalid_document", "message": "The file is not a valid PDF or image document." } }
```

| HTTP | `error.code` | Meaning |
|------|--------------|---------|
| 400 | `invalid_document` | Not a readable PDF or image / unreadable document |
| 413 | `document_too_large` | Exceeds upload size cap |
| 415 | `unsupported_media_type` | Unsupported content type (not PDF or image) |
| 422 | `conversion_failed` | Document (or OCR) produced no usable text |
| 422 | `llm_invalid_output` | Model returned malformed/non-schema JSON |
| 502 | `llm_error` | Upstream OpenRouter/model failure |
| 504 | `llm_timeout` | LLM call exceeded timeout |
| 500 | `configuration_error` | Server misconfiguration |

`RequestValidationError` (missing `file`, wrong field name) returns FastAPI's
`422` with the standard `detail` array.

## LLM prompt contract

The LLM receives a two-message conversation.

**System message** (constant):

```text
You are a career advisory assistant. Given the text of a candidate's resume
(markdown), analyze it and produce a single JSON object that matches the
provided JSON schema exactly.

Rules:
- Output ONLY valid JSON, no markdown fences, no commentary, no extra keys.
- Rank "jobs" by fit (highest first) and give each a rationale and matching skills.
- Recommend "education_materials" that close gaps relevant to the recommended jobs.
- fit_score is a float between 0.0 and 1.0.
- Keep values concise and professional.
```

**User message** (template):

```text
Here is the resume (markdown):

<resume>
{resume_markdown}
</resume>

Return your analysis as JSON conforming to the supplied schema.
```

**Structured output schema** (the `response_format.json_schema.schema` sent to
the model, and the same schema Pydantic re-validates against). This is the
schema for `ResumeAnalysis` only - the `meta` field is added server-side after
LLM validation, not part of the LLM contract:

```json
{
  "type": "object",
  "name": "resume_recommendations",
  "strict": true,
  "additionalProperties": false,
  "required": ["summary", "top_skills", "jobs", "education_materials"],
  "properties": {
    "summary": { "type": "string" },
    "top_skills": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 20 },
    "jobs": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["title", "fit_score", "seniority_level", "rationale", "key_skills"],
        "properties": {
          "title": { "type": "string" },
          "fit_score": { "type": "number", "minimum": 0, "maximum": 1 },
          "seniority_level": { "type": "string", "enum": ["intern", "junior", "mid", "senior", "staff", "principal", "executive"] },
          "rationale": { "type": "string" },
          "key_skills": { "type": "array", "items": { "type": "string" }, "maxItems": 20 }
        }
      }
    },
    "education_materials": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["topic", "kind", "title", "rationale"],
        "properties": {
          "topic": { "type": "string" },
          "kind": { "type": "string", "enum": ["course", "book", "certification", "tutorial", "project"] },
          "title": { "type": "string" },
          "provider": { "type": ["string", "null"] },
          "url": { "type": ["string", "null"], "format": "uri" },
          "rationale": { "type": "string" }
        }
      }
    }
  }
}
```

> `provider` and `url` on `education_materials[]` are optional: declared
> `str | None = None` / `HttpUrl | None = None` in Pydantic, so they are omitted
> from `required` and an absent key validates to `null` at response time.

> Implementation note: the Pydantic models in
> `schemas/recommendation.py` are the canonical definition; this JSON Schema is
> generated via `ResumeAnalysis.model_json_schema()` (Spec ID-003 / ID-007),
> not hand-maintained in two places. The `meta` field on
> `RecommendationResponse` is runtime-only (model name, markdown length) and is
> NOT part of the LLM schema.
