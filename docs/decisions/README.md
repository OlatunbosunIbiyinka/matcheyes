# Architecture decision records

Format: lightweight ADRs ([template](0000-template.md)). Numbered, never deleted; superseded
records link to their replacement.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-separate-deterministic-analytics-from-ai.md) | Separate deterministic analytics from AI reasoning | Accepted |
| [0003](0003-python-toolchain.md) | Python toolchain: uv, ruff, mypy, pytest | Accepted |
| [0004](0004-generate-synthetic-data-with-planted-ground-truth.md) | Generate our own synthetic data with planted ground truth | Accepted |
| [0005](0005-observable-and-hidden-worlds.md) | Separate observable match data from hidden ground truth | Accepted |
| [0006](0006-pydantic-for-boundary-models.md) | Pydantic v2 for boundary models | Accepted |
| [0007](0007-generator-calibration.md) | Generator calibration and acceptance | Accepted |
| [0008](0008-deterministic-analytics-design.md) | Deterministic analytics design | Accepted |
| [0009](0009-contextual-evidence.md) | Contextual evidence and multi-signal reasoning | Proposed |
| [0010](0010-agentic-investigation.md) | Agentic investigation and verification topology (orchestration pattern, MAF not adopted in Stage 4, verification and claim model) | Proposed |
| [0011](0011-evidence-audit-and-verification-hardening.md) | Evidence audit and verification hardening (provenance replay, entailment, independent auditor and lineage, blinded LLM evaluation with simulated fallback) | Accepted |
| [0012](0012-personalization-presentation-layer.md) | Personalization is a derived presentation layer over verified insights (source held by reference and fingerprinted, deterministic policy, audit before and after, preferences relevance-only, LLM excluded from relevance) | Accepted |
| [0013](0013-snapshot-anchored-insight-lifecycle.md) | Snapshot-anchored insight lifecycle (idempotent event log with contiguous watermark, one canonical snapshot per closed minute, fresh evaluation per snapshot, deterministic storyline identity, append-only chained revisions, independent lifecycle audit; corrections deferred) | Accepted |
| [0014](0014-broadcast-cue-contract-and-live-presentation-surface.md) | Broadcast cue contract and live presentation surface (content-addressed cues compiled from the lifecycle record, moments at the next snapshot close, explicit retractions, deterministic selection, one server-owned replay per match over stdlib SSE, read-only API, CSP static surface; truth pipeline unchanged) | Accepted |

## Planned (decided at the stage where evidence exists)

| Topic | Stage |
| --- | --- |
| Microsoft Foundry (model hosting, tracing, evaluation) | 6–8 |
| Web frontend stack | 7 |
| Azure Container Apps vs AKS | 8 |
| Data store selection | 8 |
| Real-time event transport | 8–9 |
