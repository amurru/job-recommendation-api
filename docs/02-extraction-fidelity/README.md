# Job Recommendation API - Feature Plan: Extraction Fidelity (FP)

This directory plans and tracks the second feature iteration: hardening the
document extraction pipeline so stage 1 (extraction) is deterministic,
faithful, structured, cached, and injection-safe, before stage 2 (reasoning)
ever sees the content.

## Background

The initial plan (`docs/01-initial-plan/`) shipped a two-stage LLM pipeline:
LLM-vision OCR produces markdown, then a recommendation LLM produces the
analysis. The architecture is right, but a code review against the
"extract, then reason" principle surfaced these gaps:

1. **Non-deterministic extraction** - neither LLM call pins temperature, so the
   same resume produces different markdown/profile across runs.
2. **Text-only stage 1** - the OCR stage returns raw text only; the
   recommendation LLM re-derives facts (skills, years, education) on every
   request, non-deterministically.
3. **No fidelity checkpoint** - nothing verifies that extracted facts are
   actually supported by the source text. A hallucinated skill flows straight
   into recommendations.
4. **Prompt injection undefended** - resume content is untrusted data but is
   embedded in the user prompt with only delimiter separation.
5. **Per-page OCR cost multiplication** - a scanned 5-page resume costs 5
   vision calls + 1 recommendation call, and repeat uploads re-pay it.
6. **Silent truncation** - `meta.markdown_length` reports the full length while
   the model only sees the first 20k characters.
7. **Weak extraction contract** - the OCR prompt forbids commentary but does not
   forbid "fixing" garbled text, which invites fabrication.

This feature closes all seven, plus the hash-based extraction cache that makes
the added structured-extraction stage economically neutral on repeat uploads.

## Document Index

| Document | Purpose |
|----------|---------|
| [`PLAN.md`](./PLAN.md) | The feature blueprint: context, dependency map, guardrails, atomic specs (FP-001..FP-010), execution sequence, risks. Read this first. |
| [`tasks.md`](./tasks.md) | Task tracking board keyed to spec IDs. **Update this as work progresses.** |
| [`architecture.md`](./architecture.md) | Architecture decisions (FP-ADR style), updated data flow, and the reasoning behind each pattern. |
| [`api-contract.md`](./api-contract.md) | HTTP contract deltas: `X-Cache` header, new `meta` fields, profile schema, prompt changes, new error code. |

## How to use these docs

1. Start with `PLAN.md` for the full scope and guardrails.
2. Pick up the first pending task in `tasks.md`.
3. Reference `architecture.md` and `api-contract.md` for the contracts and
   decisions a task must honor.
4. Mark tasks `[x]` in `tasks.md` as they land, and update the status column.
5. When a decision changes, update the relevant doc and note it in the
   ADR/changelog section of `architecture.md`.

## Status convention

- `[ ]` pending
- `[~]` in progress
- `[x]` complete
- `[!]` blocked (note the blocker in the status column)

## Feature summary

1. Pin deterministic extraction: temperature 0 on both LLM stages.
2. Harden the OCR extraction contract: reproduce, never fix or infer.
3. Add a dedicated structured profile stage (markdown -> `ResumeProfile` JSON)
   so facts are extracted once, validated, and cached.
4. Add a deterministic fidelity checkpoint: every extracted fact must have
   textual support in the source markdown.
5. Add injection defense: delimiters, an untrusted-data instruction, and a
   pattern guard.
6. Add a hash-based extraction cache (document SHA-256 -> markdown + profile)
   with TTL and LRU bounds, behind a Protocol so a shared store can replace it
   later.
7. Make truncation metadata honest and embed the profile in the recommendation
   prompt.
8. Bound OCR cost with a per-document page budget.