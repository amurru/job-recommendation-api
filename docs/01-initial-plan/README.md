# Job Recommendation API - Planning Docs

This directory is the single source of truth for planning and tracking the
implementation of the Job Recommendation API. All work flows from these docs.

## Document Index

| Document | Purpose |
|----------|---------|
| [`PLAN.md`](./PLAN.md) | The development blueprint: context, dependency map, architectural guardrails, atomic specs, execution sequence, risks. Read this first. |
| [`tasks.md`](./tasks.md) | Task tracking board. Checkboxes keyed to spec IDs with status, priority, and dependencies. **Update this as work progresses.** |
| [`architecture.md`](./architecture.md) | Architecture decisions (ADR-style), directory layout, data flow, and the reasoning behind each pattern. |
| [`api-contract.md`](./api-contract.md) | The HTTP contract: endpoint, request/response JSON schemas, error model, and the LLM prompt + structured-output schema. |

## How to use these docs

1. Start with `PLAN.md` to understand the full scope and guardrails.
2. Pick up the first pending task in `tasks.md`.
3. Reference `architecture.md` and `api-contract.md` for the specific
   contracts and decisions a task must honor.
4. Mark tasks `[x]` in `tasks.md` as they land, and update the status column.
5. When a decision changes, update the relevant doc and note it in the
   ADR/changelog section of `architecture.md`.

## Status convention

- `[ ]` pending
- `[~]` in progress
- `[x]` complete
- `[!]` blocked (note the blocker in the status column)

## Feature summary

A no-authentication FastAPI service that:

1. Accepts a PDF resume upload (`multipart/form-data`).
2. Converts the PDF to Markdown using `markitdown`.
3. Sends the Markdown to an LLM via the `openrouter` SDK with a structured-output
   schema.
4. Returns a validated JSON payload containing a resume summary, recommended
   jobs, and education materials.
