# API Contract Deltas: Security Hardening (SH)

All changes relative to the contract in `docs/02-extraction-fidelity/api-contract.md`.
Nothing here changes the `analysis` response shape.

## Authentication

### Request

`POST /api/v1/recommendations` accepts an optional credential:

```
Authorization: Bearer <api-key>
```

- Valid key -> authenticated identity (`kind="key"`), higher rate-limit tier.
- No header -> anonymous identity (allowed while `ANONYMOUS_ENABLED=true`).
- Invalid key or non-Bearer scheme -> `401 unauthorized` (below).

`GET /healthz` and `GET /readyz` remain unauthenticated.

### New error: 401 `unauthorized`

```json
{ "error": { "code": "unauthorized", "message": "A valid API key is required." } }
```

Response also carries `WWW-Authenticate: Bearer`.

Triggers:
- Malformed `Authorization` header (not `Bearer <key>`).
- Unknown/revoked key.
- `ANONYMOUS_ENABLED=false` (or `AUTH_REQUIRED=true`) and no key supplied.

The 401 is raised before body parsing, conversion, or any LLM call, and
consumes no rate-limit budget.

## Rate Limiting (Phase 1)

### Response headers (recommendations endpoint, success and 429)

| Header | Meaning |
|--------|---------|
| `X-RateLimit-Limit` | Requests allowed per window for this identity tier. |
| `X-RateLimit-Remaining` | Requests left in the current window. |
| `X-RateLimit-Reset` | Unix epoch seconds when the window resets. |

### New error: 429 `rate_limited`

```json
{ "error": { "code": "rate_limited", "message": "Rate limit exceeded. Retry after 3600 seconds." } }
```

Response additionally carries `Retry-After: <seconds>`.

Defaults (configurable):

| Tier | Default budget |
|------|----------------|
| Authenticated (`kind="key"`) | 60 requests / 60 s per key |
| Anonymous | 5 requests / 3600 s per client IP |

The limiter check runs after identity resolution and before body parsing;
denied requests never reach conversion or LLM stages.

## New error: 422 `document_too_complex`

```json
{ "error": { "code": "document_too_complex", "message": "The document exceeds the maximum allowed number of pages (50)." } }
```

Triggers (checked before any image decoding or OCR work):
- PDF declares more than `MAX_PDF_PAGES` pages (default 50).
- Any page exceeds `MAX_PAGE_INCHES` (default 30) on either edge.
- (Non-fatal) a page embeds more than `MAX_IMAGES_PER_PAGE` (default 20)
  images: excess images are skipped, the request continues.

Distinct from:
- `document_too_large` (413) - byte-size overflow, unchanged.
- `conversion_failed` (422) - unreadable/timeout, unchanged.

## Changed responses (intentional tightenings)

### 422 `validation_error` loses `detail`

Before:

```json
{ "error": { "code": "validation_error", "message": "Request validation failed.", "detail": [ ... pydantic errors ... ] } }
```

After:

```json
{ "error": { "code": "validation_error", "message": "Request validation failed." } }
```

Full error detail remains in server logs.

### `/readyz` body variance

- Development mode or valid key: `{ "status": "ready", "model": "openai/gpt-4o-mini" }` (unchanged).
- Production, anonymous: `{ "status": "ready" }` (model omitted).
- Unready: unchanged (`503` with reason).

### `meta` visibility

`?include_meta=true` is honored only for:
- Authenticated requests (valid `Authorization: Bearer`), or
- `ENVIRONMENT=development`.

Anonymous production requests passing `?include_meta=true` receive the
default response (meta omitted) - not an error.

### Response headers (all endpoints)

New middleware adds:

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `X-Frame-Options` | `DENY` |

`Strict-Transport-Security` is a reverse-proxy responsibility (the app does
not terminate TLS).

### `X-Request-ID` validation

A supplied `X-Request-ID` is honored only if it matches
`^[A-Za-z0-9_-]{1,64}$`; otherwise a fresh generated ID is returned. The
header is never rejected outright.

## CORS

`CORS_ORIGINS` (comma-separated) replaces the previous `allow_origins=["*"]`:

- Unset (default): no CORS middleware at all (pure server-to-server API).
- Set: only the listed origins receive CORS headers; methods limited to
  `POST, GET, OPTIONS`; headers limited to `Authorization, Content-Type,
  X-Request-ID`; credentials never allowed. Configuring `*` fails startup.

## Schema tightening

### `education_materials[].url` is https-only

```json
{ "url": "https://example.com/course" }
```

`http://` and other schemes fail response validation (triggering the
existing corrective LLM retry). Clients can rely on every returned URL
being `https://`.

## Interactive docs availability

| Environment | `/docs` | `/openapi.json` | `/redoc` |
|-------------|---------|-----------------|----------|
| `development` | served | served | disabled |
| `production` (default) | 404 | 404 | 404 |
| production with `DOCS_ENABLED=true` | served | served | disabled |

## New configuration surface

| Variable | Default | Phase | Description |
|----------|---------|-------|-------------|
| `API_KEYS` | *(empty)* | 0 | Comma-separated valid API keys. |
| `API_KEYS_FILE` | *(unset)* | 0 | File with one key per line (`#` comments). |
| `AUTH_REQUIRED` | `false` | 0 | When true, anonymous requests are rejected. |
| `ANONYMOUS_ENABLED` | `true` | 0 | When false, keyless requests are rejected. |
| `RATE_LIMIT_ENABLED` | `true` | 1 | Master switch for rate limiting. |
| `RATE_LIMIT_AUTH_REQUESTS` | `60` | 1 | Authenticated requests per window. |
| `RATE_LIMIT_AUTH_WINDOW_SECONDS` | `60` | 1 | Authenticated window length. |
| `RATE_LIMIT_ANON_REQUESTS` | `5` | 1 | Anonymous requests per window. |
| `RATE_LIMIT_ANON_WINDOW_SECONDS` | `3600` | 1 | Anonymous window length. |
| `RATE_LIMIT_MAX_TRACKED_IDENTITIES` | `10000` | 1 | LRU bound on limiter state. |
| `CONVERT_CONCURRENCY` | `4` | 1 | Max concurrent document conversions. |
| `MAX_PDF_PAGES` | `50` | 2 | Structural page cap. |
| `MAX_IMAGES_PER_PAGE` | `20` | 2 | Embedded images decoded per page. |
| `MAX_PAGE_INCHES` | `30` | 2 | Max page edge length in inches. |
| `MAX_IMAGE_PIXELS` | `50000000` | 2 | Pillow decompression-bomb ceiling. |
| `MAX_IMAGE_DIMENSION` | `10000` | 2 | Max width/height (px) for uploaded images. |
| `CONVERT_DEADLINE_SECONDS` | `30` | 2 | Wall-clock cap on the conversion stage. |
| `CORS_ORIGINS` | *(empty)* | 3 | Comma-separated allowlist; empty = no CORS. |
| `DOCS_ENABLED` | `ENVIRONMENT=development` | 3 | Serve /docs + /openapi.json. |

## Unchanged

- Request shape (`multipart/form-data`, field `file`, supported content
  types, `MAX_UPLOAD_BYTES` -> 413 `document_too_large`).
- Success response shape (`analysis`, conditional `meta`, `X-Cache`).
- All other error codes and messages.
- `/healthz`.
