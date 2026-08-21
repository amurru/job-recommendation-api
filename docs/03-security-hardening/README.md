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
  right weight for a single-purpose API (see SH-ADR-001 in
  [`architecture.md`](./architecture.md)).
- A shared rate-limit store (Redis). The limiter is in-process, behind a
  Protocol, targeting single-worker correctness (same trade-off as the
  extraction cache in `docs/02-extraction-fidelity/`).
- WAF / reverse-proxy configuration. Documented as a deployment
  recommendation, not implemented in-app.
- Changing the `analysis` response shape. All HTTP changes are additive
  (new headers, new error codes, one schema constraint on `url`).

## Document Index

| Document | Purpose |
|----------|---------|
| [`PLAN.md`](./PLAN.md) | The feature blueprint: context, dependency map, guardrails, atomic specs (SH-001..SH-017), execution sequence, risks. Read this first. |
| [`tasks.md`](./tasks.md) | Task tracking board keyed to spec IDs. **Update this as work progresses.** |
| [`architecture.md`](./architecture.md) | Architecture decisions (SH-ADR style), updated data flow, and the reasoning behind each pattern. |
| [`api-contract.md`](./api-contract.md) | HTTP contract deltas: auth, 401/429, rate-limit headers, new error codes, tightened responses, new settings. |

## How to use these docs

1. Start with `PLAN.md` for the full scope and guardrails.
2. Pick up the first pending task in `tasks.md`.
3. Reference `architecture.md` and `api-contract.md` for the contracts and
   decisions a task must honor.
4. Mark tasks `[x]` in `tasks.md` as they land, and update the status column.
5. When a decision changes, update the relevant doc and note it in the
   changelog section of `architecture.md`.

## Status convention

- `[ ]` pending
- `[~]` in progress
- `[x]` complete
- `[!]` blocked (note the blocker in the status column)

## Phase summary

| Phase | Specs | Closes | Theme |
|-------|-------|--------|-------|
| 0 | SH-001..003 | H1 (identity half) | API-key auth + limited anonymous tier |
| 1 | SH-004..006 | H1 (budget half) | Configurable rate limiting + concurrency cap |
| 2 | SH-007..008 | H2 | PDF/image structural bounds + conversion deadline |
| 3 | SH-009..013 | M2, M3, M4, M5, L1, L2, L5 | HTTP surface hardening |
| 4 | SH-014..015 | M1, L3 | Prompt escaping, https-only URLs, OCR error hygiene |
| 5 | SH-016..017 | - | Verification matrix + release docs |

Deployment note: Phases 0 and 1 should ship together (or set
`ANONYMOUS_ENABLED=false` between them) so the anonymous tier is never live
without its budget - see PLAN open question 1.
