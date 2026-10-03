# ADR-0002: Separate deterministic analytics from AI reasoning

- Status: Accepted
- Date: 2026-10-03
- Stage: 0

## Context

The product's value is trustworthy explanation ("why it matters"), not just generated text.
LLMs are unreliable at arithmetic and counting and can invent plausible match facts. Judges and
users must be able to ask "what evidence supports this?" and get a traceable answer.

## Decision

- Football facts and statistics are computed by deterministic, unit-tested code in
  `domain`, `ingestion` and `analytics`. These layers never import AI or cloud SDKs.
- Agents consume structured evidence objects (metrics + source event references), not raw
  event dumps, and return structured outputs.
- Outputs are labelled as FACT, ANALYSIS, AI INTERPRETATION or NARRATIVE.
- Layer dependencies are enforced by an automated architecture test in CI.

## Alternatives considered

| Option | Why not |
| --- | --- |
| LLM reads raw events and computes stats | Non-deterministic, untestable, hallucination risk, higher token cost. |
| Rely on convention only | Boundaries erode under hackathon time pressure. |

## Consequences

- The analytics engine is testable and demoable without any model.
- Agents become cheaper and more reliable (smaller, structured context).
- Requires a well-designed evidence object model (Stage 2) before agents are useful.
