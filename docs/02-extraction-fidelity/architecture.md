# Architecture - Extraction Fidelity (FP)

## Directory layout (target, deltas from `docs/01-initial-plan/architecture.md`)

```
src/job_recommendation_api/
├── schemas/
│   ├── recommendation.py      # unchanged; ResponseMeta gains additive fields (FP-007)
│   └── profile.py             # NEW: ResumeProfile + PROFILE_SCHEMA export (FP-003)
├── services/
│   ├── document_converter.py  # + document_hash() + EXTRACTION_VERSION (FP-006)
│   ├── extraction_cache.py    # NEW: ExtractionCache Protocol + InMemoryExtractionCache (FP-006)
│   ├── resume_profiler.py     # NEW: ProfileExtractor Protocol + LLMProfileExtractor + fidelity check (FP-003/004)
│   ├── injection_guard.py     # NEW: InjectionGuard pattern guard (FP-005)
│   ├── prompts.py             # + PROFILE_SYSTEM_PROMPT, build_profile_prompt, untrusted-data note, profile-aware user prompt (FP-003/005/007)
│   ├── ocr/service.py         # hardened _DEFAULT_PROMPT (FP-002)
│   └── ocr/pdf_converter.py   # + page budget (FP-008)
├── llm/client.py              # + temperature pass-through (FP-001)
└── errors.py                  # + OcrBudgetExceededError (FP-008)
```

## Layer rules (unchanged, extended)

```
api (routes, deps, handlers)
  -> services (business orchestration)
       -> llm / schemas / errors (adapters + contracts)
       -> services.{document_converter, resume_profiler, injection_guard, extraction_cache}
```

- `api` never imports concrete third-party clients directly (unchanged).
- `services` never imports FastAPI/HTTP objects (unchanged; the `X-Cache`
  header is set by the route from `meta.cache`, not by the service).
- The two new infrastructure seams (`ProfileExtractor`, `ExtractionCache`) are
  `typing.Protocol`s, matching the existing `DocumentConverter` / `LLMClient`
  pattern: fakes in tests, swappable implementations in production.
- `document_converter.py` stays pure: it computes the hash and converts, but
  never touches the cache. The cache lives in the orchestration service.

## Data flow (target)

```
Client (multipart PDF / photo)
  |
  v
POST /api/v1/recommendations
  |  validate content-type + size (unchanged)
  v
RecommendationService.recommend(document_bytes, name)
  |
  |  1. key = cache_key(document_bytes)            # sha256(original bytes):v<EXTRACTION_VERSION>
  |  2. hit = extraction_cache.get(key)            # thread-safe, TTL + LRU
  |     |
  |     |-- HIT: markdown, profile, counts = cached
  |     |                                          # converter + OCR + profiler all skipped; meta identical to the populating MISS
  |     |
  |     |-- MISS:
  |     |    a. markdown = await run_in_threadpool(converter.convert, bytes)   # markitdown (+ OCR vision calls, page-budgeted)
  |     |    b. markdown = injection_guard.guard(markdown).cleaned_text       # FP-005
  |     |    c. profile = await profiler.extract(markdown)                    # profile LLM call, temperature 0, PROFILE_SCHEMA
  |     |    d. profile, dropped = check_fidelity(markdown, profile)          # deterministic, FP-004
  |     |    e. extraction_cache.set(key, CachedExtraction(markdown, profile,
  |     |       dropped_facts, injection_lines_removed, ...))
  |     |
  |  3. messages = [system (untrusted-data note), user (profile JSON + capped markdown)]
  |  4. data = await llm.complete(messages, schema=RECOMMENDATION_SCHEMA)    # recommendation LLM call, NEVER cached
  |  5. corrective retry on LLMInvalidOutputError (existing, unchanged)
  |  6. RecommendationResponse.model_validate(analysis + honest meta)
  v
200 JSON + X-Cache: HIT|MISS
```

Call-count summary per document:

| Input | Calls on cache miss | Calls on cache hit |
|-------|--------------------|--------------------|
| Digital PDF (text layer) | 2 (profile + recommendation) | 1 (recommendation) |
| Scanned PDF / photo | 2 + N (profile + recommendation + OCR pages, `N <= max_ocr_pages`) | 1 (recommendation) |

The recommendation call is deliberately per-query: the same document re-analyzed
against new job data must not serve stale output.

## Architecture decisions

### FP-ADR-001: Dedicated profile extraction stage (3rd call for scanned docs)
The recommendation stage previously re-derived every fact from markdown on each
request. This feature adds a dedicated `LLMProfileExtractor` call that turns
markdown into a validated `ResumeProfile`.

- **Rejected**: merge `ResumeProfile` into the recommendation call's output.
  It re-entangles extraction with reasoning (the exact anti-pattern being
  fixed), ties fact quality to recommendation quality, and makes the profile
  uncacheable without caching reasoning output.
- **Rejected**: have the OCR vision call return `{text, profile}`. Two
  extraction tasks in one vision call compete; the model optimizes for the
  structured part and skips faithful transcription, and digital PDFs (no OCR)
  would still need a separate profile path.
- **Accepted**: one extra call per new document, made nearly free on repeat
  uploads by the cache (FP-ADR-003) and bounded for scans by the page budget
  (FP-008).

### FP-ADR-002: Deterministic extraction (temperature 0)
Both extraction calls (OCR vision, profile) and the recommendation call run at
temperature 0 (`ocr_temperature`, `llm_temperature`, both default 0.0). The
cost function of extraction is fidelity, and temperature is the knob that
trades fidelity for variety. A non-deterministic extraction also poisons any
cache: the same document would randomly sample different cached values.
Recommendation is included because the output is schema-constrained JSON where
determinism aids support and regression testing; the setting is configurable
for product needs later.

### FP-ADR-003: Hash-based extraction cache behind a Protocol
Cache key is `sha256(original_uploaded_bytes):v<EXTRACTION_VERSION>` - hashing
the original bytes (before image->PDF wrapping) so identical uploads always
hit. The version component invalidates safely on any converter, prompt, schema,
or model change. Storage is an in-process, TTL-bounded, LRU-capped dict behind
the `ExtractionCache` Protocol.

- **Why not Redis now**: persistence is an explicit non-goal; the Protocol
  means the migration is one new class plus lifespan wiring, zero call-site
  changes.
- **What is never cached**: recommendation output. Cache key and value are
  extraction-scoped by design. The value also carries `dropped_facts` /
  `injection_lines_removed` so a HIT reports the same `meta` as the MISS that
  populated it.
- **Concurrency**: the cache is touched from the converter threadpool and the
  async profiler path; a single lock guards the dict. First-ever concurrent
  identical uploads may both miss (stampede) and duplicate extraction work;
  accepted and documented (rare, and correctness-neutral).

### FP-ADR-004: Fidelity checkpoint is deterministic, lenient by default
`check_fidelity` verifies discrete list facts (skills, education, languages,
certifications) against the source markdown with normalized substring /
significant-token containment. No extra LLM call.

- **Lenient default**: unsupported facts are dropped and reported in
  `meta.dropped_facts`. False negatives exist (paraphrased skills, OCR-garbled
  source), and dropping a fact is strictly safer than 422ing a valid resume
  over one. `strict` mode exists for environments that prefer fail-loud.
- This is the checkpoint that was missing between extraction and reasoning:
  hallucinated facts never reach the recommendation prompt.

### FP-ADR-005: Injection defense is layered
- **Delimiters**: resume/profile content is isolated in `<resume>` / `<profile>`
  tags (existing delimiter kept, profile section added).
- **Instruction**: both system prompts state the delimited content is untrusted
  data and instructions inside it must be ignored.
- **Guard**: a deterministic pattern guard strips known instruction-override
  lines before the content reaches either LLM stage, with counts surfaced in
  `meta.injection_lines_removed`. Patterns are multi-word phrases only; bare
  trigger words ("override", "disregard") are excluded so legitimate resume
  lines survive (a stripped line is silently absent from profiling and from
  the cache).
- The guard is a cheap layer, not a guarantee; the primary defense is
  instruction-level (the model is told the content is data, and the output is
  schema-constrained).

### FP-ADR-006: Fail-loud for budgets, fail-lenient for facts
Budget violations (OCR page cap, `OcrBudgetExceededError`) keep the existing
fail-loud contract: a 422 with a stable code, because an unbounded public
endpoint is a cost-amplification target. Per-fact fidelity violations are
fail-lenient (drop + telemetry) because the cost of a false positive (rejecting
a valid resume) outweighs the cost of dropping a suspect fact. This replaces
the previous all-or-nothing failure behavior for multi-page scans while
preserving it for true budget abuse.

Propagation detail: markitdown catches exceptions raised by registered
converters and falls through to the next one, so `PdfConverterWithOCR` never
raises the budget error inline. It records `budget_exceeded` on its instance
(fresh per conversion); `MarkItDownConverter.convert` checks the flag after
`convert_stream` returns and raises `OcrBudgetExceededError`, which the
generic `AppError` handler maps to the 422 envelope. The error can therefore
never surface as `conversion_failed`.

## Cross-cutting concerns

- **Logging**: unchanged (JSON, request-id via contextvars). New log points:
  cache hit/miss (with duration saved), dropped facts, injection lines removed.
  Never log the API key or raw resume content (existing rule extended to cached
  values).
- **Observability**: `X-Cache: HIT|MISS` header plus `meta.cache` per request
  gives hit-rate observability without a metrics exporter. `meta.dropped_facts`
  and `meta.injection_lines_removed` make fidelity and injection activity
  auditable per response.
- **PII in cache**: in-memory only, TTL (default 3600s) and LRU cap (default
  256) bound exposure; no persistence. Redaction before caching is a documented
  follow-up for production compliance (see PLAN risks).
- **Threading**: the cache lock is held only for dict operations (microseconds);
  the profile and recommendation calls remain async-native; markitdown remains
  in the threadpool.

## Changelog

| Date | Change |
|------|--------|
| 2026-08-20 | Feature planned: extraction fidelity. FP-ADR-001..006 recorded; data flow updated with cache, profiler, injection guard, fidelity checkpoint, OCR budget. |
| | FP-001 temperature pinning; FP-002 OCR prompt hardening; FP-003 profile stage; FP-004 fidelity checkpoint; FP-005 injection guard; FP-006 hash cache; FP-007 honest truncation + profile-aware prompt; FP-008 OCR budget. |
| | Initial implementation of 02-extraction-fidelity: status `pending` in `tasks.md`. |
| 2026-08-21 | Plan revision after code verification: FP-005 guard narrowed to multi-word phrases (false-positive risk on bare words); FP-006 caches fidelity/injection counts for hit/miss meta parity; FP-008 budget error propagates via converter flag raised by `document_converter` (markitdown swallows converter exceptions); FP-009 `_env_file=None` test isolation; profiler-owned corrective retry clarified; api-contract call numbering + schema wire-format note fixed. |
| 2026-08-21 | FP-001..FP-010 implemented. All specs landed: temperature pinned via config; OCR prompt hardened; `ResumeProfile` + `LLMProfileExtractor` with profiler-owned corrective retry (covers schema and strict-fidelity failures); `InjectionGuard` multi-word patterns; `InMemoryExtractionCache` (TTL + LRU, thread-safe) orchestrated in `RecommendationService` with `X-Cache` header; honest truncation meta; OCR page budget with `OcrBudgetExceededError` -> 422. Test suite grown 84 -> 140; ruff + mypy --strict green. Test-isolation discovery recorded in `tasks.md` (markitdown `load_dotenv()` at import time; autouse env-strip fixture added). `EXTRACTION_VERSION = "2"`. |