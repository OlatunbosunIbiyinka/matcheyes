# ADR-0006: Pydantic v2 for boundary models

- Status: Accepted
- Date: 2026-10-03
- Stage: 1

## Context

Every boundary (ingestion, ground truth, later agent inputs and outputs, API) needs typed,
validated, immutable models with JSON round-tripping and JSON Schema for structured model
outputs.

## Decision

Use Pydantic v2 (`frozen=True, extra="forbid"`) for all boundary models. Use plain dataclasses
for internal value objects that never cross a boundary (e.g. validation reports).

## Alternatives considered

| Option | Why not |
| --- | --- |
| Dataclasses + hand-written validation | Re-implements validation, JSON and schema generation. |
| attrs + cattrs | Fine, but no built-in JSON Schema. Pydantic is already used by Microsoft Agent Framework and FastAPI. |
| msgspec | Faster, but less ecosystem support for structured LLM outputs. |

## Consequences

One runtime dependency now. Validation cost is negligible at our volumes (about 2,000 events
per match). JSON Schema from the same models will drive structured agent outputs in Stage 3.
