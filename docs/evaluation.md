# Evaluation

Status: **strategy + ground-truth design (Stage 1)**. "It looked good in the demo" is not
evidence.

| Layer | Method | Introduced |
| --- | --- | --- |
| Architecture | Import-boundary tests; observable/hidden separation (source, runtime, wheel, schema, ingestion) | Stages 0–1 |
| Ingestion | Schema validation + match-level invariants on fixtures and every generated match | Stage 1 |
| Generator | Determinism (hash), invariants, realism bands, planted effect size above seed noise | Stage 1b |
| Analytics | Deterministic unit tests with hand-computed expectations; mechanism-level scoring against planted truth, twins, controls and decoys ([report](stage2-evaluation.md)) | Stage 2 |
| Insights | Planted-truth scoring (below) | Stage 3 onwards |
| Agents | Structured-output validity, routing, failure handling | Stage 4 |
| Verification | Decoy and control false-claim rates; seeded unsupported claims rejected or downgraded | Stage 5 |
| Personalization | Same verified claim set across Fan / Broadcaster / Analyst and across languages | Stage 6 |
| Narrative quality | Rubric-based evaluation (Foundry evaluators where verified) | Stages 5–10 |

## Planted-truth metrics

`matcheyes_eval` joins engine output with the answer key (it is the only code allowed to).
The engine never sees the key ([ADR-0005](decisions/0005-observable-and-hidden-worlds.md)).

| Metric | Definition |
| --- | --- |
| Detection recall | Share of expected insights detected inside their window plus the allowed latency |
| Detection latency | Time from the planted cause's start to the engine's first matching insight |
| Cause attribution accuracy | Share of detected insights whose primary cause matches the planted intervention |
| Cause ranking | For multi-cause scenarios (S10), primary ranked above secondaries |
| Mechanism coverage | Share of expected *scored* mechanism signals cited as evidence (supporting mechanisms are never scored) |
| Twin background rate | The same scoring on the counterfactual twin (same seed, no interventions): how often the evidence appears anyway |
| Supported-claim precision | Share of `supported` claims that match a planted cause or a recorded background effect |
| Decoy violation rate | Claims stronger than a decoy's cap, for that team and window |
| Control false-positive rate | `supported` claims in S01 that match nothing in the answer key |

Reporting rules: many seeds per scenario (target 20), held-out test seeds only, full
distributions with intervals, and failures shown alongside successes. See
[synthetic-data.md](synthetic-data.md#evaluation-hygiene-so-ground-truth-measures-rather-than-flatters-matcheyes).

## Test categories

- **Unit**: fast, deterministic, no network. Always run in CI.
- **Integration** (`@pytest.mark.integration`): real model / Azure calls. Opt-in.
- **Evaluation**: scenario suites over generated matches, scored against the answer key.
