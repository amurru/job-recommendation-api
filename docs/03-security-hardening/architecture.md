# Architecture: Security Hardening (SH)

Decisions (SH-ADR style), the updated data flow, and the reasoning behind
each pattern. Companion to [`PLAN.md`](./PLAN.md) and
[`api-contract.md`](./api-contract.md).

## Updated Data Flow (post-hardening)

```
POST /api/v1/recommendations
  |-> request-id middleware        (validated X-Request-ID, security headers)
  |-> identity resolution          (SH-001/002: Bearer key -> Identity, or
  |                                 anonymous; 401 before any parsing)
  |-> rate limiter                 (SH-004/005: per-identity sliding window;
  |                                 429 + X-RateLimit-* before any parsing)
  |-> size + content-type check    (existing)
  |-> conversion                   (SH-006 concurrency limiter + SH-008
  |                                 deadline; SH-007/008 structural caps:
  |                                 pages, page size, images, pixels)
  |-> injection guard              (existing FP-005)
  |-> profile extraction           (LLM, schema-validated)
  |-> prompt assembly              (SH-014: delimiter-escaped untrusted data)
  |-> recommendation LLM           (SH-015: https-only output URLs enforced
  |                                 at schema validation)
  |-> response                     (SH-013: meta gated by identity/env)
```

Failure ordering is deliberate: identity -> rate limit -> size/type ->
structure -> content. Cheapest checks fail first, and no expensive stage
(conversion, OCR, LLM) runs before every cheaper gate has passed.

## Decisions

### SH-ADR-001: Static API keys over accounts/JWT (accepted)

**Decision**: Authentication is static API keys supplied via
`API_KEYS` (comma-separated env) or `API_KEYS_FILE`, verified as SHA-256
digests with `hmac.compare_digest`. No user accounts, no JWT issuance, no
OAuth.

**Rationale**: The API has a single operation and a small, operator-managed
caller set. Static keys match the OpenAI-style `Authorization: Bearer`
convention clients already know, require zero session state, and are
revocable by config change + redeploy. Accounts/JWT add a credential
lifecycle (issuance, refresh, revocation, storage) that solves problems
this service does not have yet.

**Rejected alternatives**:
- *JWT with an auth server*: correct at multi-team scale, heavy here; adds a
  dependency on an external issuer for a single-endpoint API.
- *HTTP Basic*: transmits credentials every request with no advantage over
  Bearer and worse tooling support for multipart clients.
- *mTLS*: strongest, but operationally disproportionate and blocks browser/
  curl-based anonymous tier entirely.

**Consequences**: Key rotation is manual but downtime-free (add the new key,
redeploy, remove the old). Compromise response is "remove key from config".
If self-service signup ever becomes a requirement, this ADR is revisited.

### SH-ADR-002: Anonymous tier retained, gated by rate limits (accepted)

**Decision**: Anonymous (keyless) requests remain allowed by default
(`anonymous_enabled=true`) with a separate, much smaller budget enforced in
Phase 1 (5 req/hour per IP default). `anonymous_enabled=false` or
`auth_required=true` converts the API to key-only.

**Rationale**: Zero-friction try-it is a product property worth preserving;
the audit's H1 is about *unbounded* anonymous consumption, not anonymous
consumption itself. Identity (Phase 0) plus per-tier budgets (Phase 1)
together bound worst-case cost while keeping the front door open.

**Consequences**: Until Phase 1 merges, Phase 0 alone leaves anonymous users
unthrottled. Mitigation: deploy Phases 0+1 together, or flip
`anonymous_enabled=false` in the interim (one config change, recorded in
PLAN open question 1).

### SH-ADR-003: In-process sliding-window limiter with Protocol seam (accepted)

**Decision**: `SlidingWindowRateLimiter` keeps per-identity request
timestamps in memory, thread-safe, with LRU eviction of stale identities
(`rate_limit_max_tracked_identities`). Redis-backed limiter deferred.

**Rationale**: Mirrors the extraction-cache decision (FP-ADR): single-worker
correctness now, Protocol seam for a shared store later, no new
infrastructure. A sliding window avoids the token-bucket burst edge cases
and is trivial to reason about for "N per window".

**Rejected alternatives**:
- *Fixed window*: simple, but allows a 2x burst across the boundary
  (N at window end + N at next start).
- *Token bucket*: fine, but sliding window's eviction semantics are simpler
  to bound and test.
- *Reverse-proxy-only limiting (nginx/Cloudflare)*: recommended as defense
  in depth (documented in README deployment notes), but the app must not
  depend on infrastructure it does not control.

**Consequences**: Multi-worker deployments multiply effective limits by the
worker count (documented limitation). LRU eviction means a sufficiently
large botnet can evict each other's windows - bounded harm (one window's
budget) for bounded memory.

### SH-ADR-004: Fail-loud structural caps with a distinct 422 code (accepted)

**Decision**: Page-count, page-dimension, and images-per-page violations
raise `DocumentTooComplexError` -> 422 `document_too_complex`, distinct
from `conversion_failed` (unreadable) and `document_too_large` (byte size).

**Rationale**: Consistent with FP-ADR-006 (OCR budget fail-loud). The
distinct code keeps monitoring and client messaging honest: "your document
is structurally too big" is actionable ("split it") in a way
"conversion failed" is not. Caps are checked before decode/OCR work so the
rejection cost is O(metadata), not O(document).

**Consequences**: Legitimate >50-page documents are rejected with a clear
message; the cap is configurable for deployments with unusual document
profiles. Excess embedded images within a page are skipped (not fatal) to
avoid rejecting hybrid resumes for one decorative image too many.

### SH-ADR-005: Delimiter escaping at the prompt boundary (accepted)

**Decision**: `sanitize_untrusted()` rewrites exact delimiter sequences
(`<resume>`, `</resume>`, `<profile>`, `</profile>`) inside embedded
content, applied in one place (`prompts.py` builders), rather than
per-request random delimiters.

**Rationale**: Random per-request delimiters (the stronger alternative:
`<resume-{nonce}>`) defeat forgery completely but leak the nonce into the
system prompt only once per request, complicate prompt caching, and make
prompt snapshots harder to diff in logs. Exact-sequence escaping closes the
known forgery vector (the only delimiters the prompts define) with zero
runtime cost, and is deterministic - important because prompt content feeds
the cached-extraction path.

**Rejected alternatives**:
- *Random nonces*: strictly stronger; revisit if a prompt-injection finding
  survives escaping.
- *Base64-encoding the resume*: also strong, but degrades model reading
  fidelity on long resumes (measurable quality cost on extraction tasks).

**Consequences**: A resume containing the literal string `</resume>` has it
rendered as a look-alike; the fidelity checkpoint and injection guard are
unaffected (they run on pre-sanitization markdown).

### SH-ADR-006: https-only output URLs, schema-enforced (accepted)

**Decision**: `LearningResource.url` gains `pattern=r"^https://"`. The
constraint lives in the Pydantic schema (the same contract the LLM's
`json_schema` output is bound to), not in prompt text.

**Rationale**: Schema enforcement is deterministic and survives the
`json_object` fallback path (where the model never sees the JSON Schema and
prompt instructions are the only defense). A non-https emission fails
validation and triggers the existing corrective retry, so well-behaved
models are unaffected and hostile emissions cannot ship plaintext or
custom-scheme links to end users.

**Consequences**: A model that persistently emits `http://` links would 422
after retries. Accepted: dropping a link beats shipping an
attacker-steered one. If real-world false positives appear, the next step
is a provider allowlist, not loosening the scheme.

### SH-ADR-007: Diagnostics gated by identity, not removed (accepted)

**Decision**: `?include_meta=true` and the `/readyz` model field remain,
but are visible only to keyed identities or in development mode. Security
headers (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`)
are set in-app; HSTS is documented as a reverse-proxy concern.

**Rationale**: The meta block is operationally valuable (cache state,
truncation, fidelity drops) and was deliberately built in FP-007; gating by
identity preserves it for real integrators while blinding casual scanners.
In-app headers cover the cases that matter even on bare deployments; HSTS
without TLS termination in-app is meaningless, so it belongs at the proxy.

## Changelog

| Date | Change |
|------|--------|
| 2026-08-21 | Initial SH-ADR-001..007 drafted from the security audit findings (H1, H2, M1-M5, L1-L3, L5). |
| 2026-08-21 | SH-001..SH-017 implemented. Notable implementation notes: (1) `LearningResource.url` https constraint is a `field_validator` (`value.scheme != "https"`), not `Field(pattern=...)` - pydantic v2 cannot apply `pattern` to `HttpUrl`; same contract, same retry behavior. (2) Structural caps set a `too_complex_reason` flag on `PdfConverterWithOCR` (markitdown swallows converter exceptions), mirroring the existing `budget_exceeded` pattern; `MarkItDownConverter` raises `DocumentTooComplexError` after conversion. (3) The conversion deadline uses `anyio.move_on_after` + `run_sync(..., abandon_on_cancel=True)`: the request fails fast and the capacity-limiter token is released; the abandoned worker thread finishes in the background (threads cannot be killed). (4) Sliding-window entries are keyed `(timestamp, seq)` so same-instant requests never collapse; fully-expired windows release their tracked-identity slot on next access (lazy pruning per SH-ADR-003). |
