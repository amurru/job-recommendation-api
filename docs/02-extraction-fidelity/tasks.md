# Task Tracking Board - Extraction Fidelity (FP)

> Single source of truth for feature implementation progress. Check off items
> as they land. Keep status and notes current.

Legend: `[ ]` pending · `[~]` in progress · `[x]` complete · `[!]` blocked

## Phase 0 - Determinism & contracts

- [x] **FP-001** Settings + temperature pinning: `profile_model`, `ocr_temperature`, `llm_temperature`, `profile_fidelity`, `max_ocr_pages`, `extraction_cache_max_entries`, `extraction_cache_ttl_seconds`; forward temperature in OCR client payload and `send_async`.
  - Priority: `high` · Depends: - · Status: `done`
- [x] **FP-002** Harden `_DEFAULT_PROMPT` in `services/ocr/service.py`: "no add/fix/infer/complete; output garbled text as-is"; unit test asserts verbatim embedding.
  - Priority: `high` · Depends: - · Status: `done`

## Phase 1 - Profile stage

- [x] **FP-003** `schemas/profile.py` `ResumeProfile` + `PROFILE_SCHEMA`; `services/resume_profiler.py` `ProfileExtractor` Protocol + `LLMProfileExtractor` (async, temperature 0, Pydantic-validated); profile system/user prompts in `services/prompts.py`.
  - Priority: `high` · Depends: FP-001 · Status: `done`

## Phase 2 - Fidelity & safety

- [x] **FP-004** `check_fidelity(markdown, profile) -> FidelityReport` (normalized containment + token rule); lenient drops + `dropped_facts`; strict raises into the profiler's own bounded corrective retry (owned by `LLMProfileExtractor`, re-checks fidelity per retry).
  - Priority: `high` · Depends: FP-003 · Status: `done`
- [x] **FP-005** `services/injection_guard.py` `InjectionGuard.guard(text)` (multi-word phrase removal + counts; bare words like "override"/"disregard" deliberately not patterns); untrusted-data instruction in both system prompts; `<profile>` section delimiter.
  - Priority: `high` · Depends: FP-003 · Status: `done`

## Phase 3 - Cache & prompt

- [x] **FP-006** `services/extraction_cache.py`: `CachedExtraction` (incl. `dropped_facts`, `injection_lines_removed` for hit/miss meta parity), `ExtractionCache` Protocol, `InMemoryExtractionCache` (TTL + LRU + lock); `document_hash` + `EXTRACTION_VERSION` in converter; orchestration in `recommendation.py`; `meta.cache` + `X-Cache` header.
  - Priority: `high` · Depends: FP-003, FP-005 · Status: `done`
- [x] **FP-007** `build_user_prompt(markdown, profile)`; honest `markdown_length` (snapshot length) + `markdown_truncated`; additive `ResponseMeta` fields (`cache`, `markdown_truncated`, `dropped_facts`, `injection_lines_removed`).
  - Priority: `medium` · Depends: FP-003, FP-006 · Status: `done`

## Phase 4 - Cost guard

- [x] **FP-008** `max_ocr_pages` budget in `pdf_converter.py` (skip + `*[OCR skipped: page budget exceeded]*` marker + `budget_exceeded` flag; never raise inline - markitdown swallows converter exceptions); `MarkItDownConverter` raises `OcrBudgetExceededError` from the flag after conversion -> 422 `ocr_budget_exceeded`.
  - Priority: `medium` · Depends: FP-001 · Status: `done`

## Phase 5 - Verification & release

- [x] **FP-009** Test suites: `test_profile_schema`, `test_resume_profiler`, `test_fidelity_check`, `test_injection_guard`, `test_extraction_cache`, `test_ocr_prompt` (+ `test_ocr_budget`); update service/api/config/llm/ocr-client tests; integration covers `X-Cache`, `ocr_budget_exceeded` (422 with correct code), and hit/miss `meta` parity; tests construct `Settings` with `_env_file=None` so a local `.env` cannot leak; ruff + mypy --strict green.
  - Priority: `high` · Depends: FP-001..FP-008 · Status: `done`
- [x] **FP-010** `.env.example` new rows, `README.md` settings table + cache note, feature docs updated to final behavior (changelog entries).
  - Priority: `medium` · Depends: FP-009 · Status: `done`

## Blockers / Notes

- 2026-08-21: FP-009 - discovered markitdown calls `load_dotenv()` at import
  time, copying `.env` values into `os.environ` process-wide. `_env_file=None`
  alone does not isolate tests; an autouse fixture now strips all setting
  variables per test (`_isolate_settings_env` in `tests/conftest.py`).
- 2026-08-21: FP-009 - added `tests/__init__.py` so mypy resolves
  `tests.conftest` under a single module name (needed by the shared
  `make_settings` helper).

### Resolved

- none yet
