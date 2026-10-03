# Evaluation

Status: **strategy only (Stage 0)**. "It looked good in the demo" is not evidence.

| Layer | Method | Introduced |
| --- | --- | --- |
| Architecture | Static import-boundary tests | Stage 0 |
| Ingestion | Schema validation against real data + fixtures | Stage 1 |
| Analytics | Deterministic unit tests with hand-computed expectations | Stage 2 |
| Single insight | Golden scenarios: detected moment -> expected evidence -> grounded explanation | Stage 3 |
| Agents | Structured-output validity, routing, failure handling | Stage 4 |
| Verification | Seeded unsupported claims must be rejected or downgraded | Stage 5 |
| Personalization | Fact consistency across Fan / Broadcaster / Analyst | Stage 6 |
| Narrative quality | Rubric-based evaluation (Foundry evaluators where verified) | Stages 5–10 |

Test categories:

- **Unit**: fast, deterministic, no network. Always run in CI.
- **Integration** (`@pytest.mark.integration`): real model / Azure calls. Opt-in.
- **Evaluation**: scenario suites scoring factual correctness, grounding, unsupported claims,
  consistency and narrative quality.
