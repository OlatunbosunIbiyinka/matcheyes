# Architecture

Status: **deterministic engine (Stages 0–3) plus agentic investigation (Stage 4)**. Azure and
presentation choices remain open.

## Principle

The system separates six concerns. Each has an owner in the codebase and a rule about what it
may and may not do.

| Concern | Owner | Rule |
| --- | --- | --- |
| FACTS | `domain`, `ingestion` | Directly derived from event data. Validated, typed, immutable. |
| ANALYTICS | `analytics` | Deterministic calculations. Never calls an LLM. Unit-tested. |
| AI REASONING | `agents` (investigator, challenger) | Chooses explanations and evidence requests; assesses. Structured output only. May not invent facts. |
| EVIDENCE | `analytics` (objects), `agents` (typed tools, deterministic verifier) | Every claim links to tool facts and source events; the verifier can only keep or downgrade. |
| NARRATIVE | `agents/narrative.py` (templated code) | Renders *verified* findings; labels fact vs interpretation. |
| PRESENTATION | `api`, `web` | Personalizes detail and tone, never the underlying facts. |

## Dependency rules

```
domain  <-  ingestion
domain  <-  analytics
domain, analytics  <-  agents
domain, ingestion, analytics, agents  <-  orchestration
orchestration (and below)  <-  api
```

`domain`, `ingestion` and `analytics` must not import any AI or cloud SDK. These rules are
enforced by `tests/architecture/test_layer_boundaries.py` and run in CI.

## Analytics (Stage 2)

The analytics layer turns observable events into traceable evidence that is capped at
ASSOCIATED. The steps are possessions and a minute timeline, then per-team metric series, then
shifts, then evidence objects (FACT or ANALYSIS), then candidate moments. Definitions are in
[metrics.md](metrics.md), the design is in
[ADR-0008](decisions/0008-deterministic-analytics-design.md) and the results are in
[stage2-evaluation.md](stage2-evaluation.md). The entry point is
`matcheyes.analytics.analysis.analyse_match`. The root CLI (`python -m matcheyes analyse`)
composes ingestion and analytics.

## Contextual evidence (Stage 3)

Stage 3 re-assesses every Stage 2 metric shift; it detects nothing new and leaves Stage 2
output unchanged. Each step is a small, separately tested module in `matcheyes.analytics`:

```
Stage 2 shift ─► baselines   (same game-state regime? coincident goal/red card? ordinary response?)
              ─► patterns    (do related metric families move with it?)
              ─► persistence (sustained, transient, reversed?)
              ─► strength    (evidence level, claim strength <= ASSOCIATED, rank)
context.py / workload.py ─► context notes attached to each candidate (never evidence)
```

The entry point is `matcheyes.analytics.contextual.analyse_contextual`, which reuses the Stage 2
analysis. The method is in [contextual-evidence.md](contextual-evidence.md), the design in
[ADR-0009](decisions/0009-contextual-evidence.md) and the results in
[stage3-evaluation.md](stage3-evaluation.md).

## Agentic investigation (Stage 4)

Every Stage 3 candidate at Weak or above is investigated by an explicit, bounded flow in
`matcheyes.orchestration.investigation`:

```
case file (code) ─► Investigator plan ─► typed tools (code) ─► Investigator assess
                 ─► Challenger (at most one round) ─► Verifier (code, 8 gates) ─► narrative (code)
```

Two reasoning roles sit behind the `ReasoningModel` protocol. They are filled by a
deterministic reference reasoner, or optionally by an OpenAI-compatible model configured
through the environment. Facts come only from tools, and the verifier checks every assertion.
The method is in [agentic-investigation.md](agentic-investigation.md), the topology rationale
in [ADR-0010](decisions/0010-agentic-investigation.md) and the results in
[stage4-evaluation.md](stage4-evaluation.md). Network access exists only in `agents/llm.py`.

## Observable world vs hidden world

```
matcheyes_synth  ──►  matcheyes.domain / ingestion        (generator emits engine-valid data)
matcheyes_eval   ──►  matcheyes + matcheyes_synth         (only place truth meets output)
matcheyes        ──►  (neither)                           (engine is blind to ground truth)
```

Only `matcheyes` ships in the deployable wheel. See
[synthetic-data.md](synthetic-data.md) and [ADR-0005](decisions/0005-observable-and-hidden-worlds.md).

## Pipeline (target)

```
EVENT -> DETECT -> INVESTIGATE -> VERIFY -> EXPLAIN -> PERSONALIZE
```

| Step | Kind | Notes |
| --- | --- | --- |
| EVENT | deterministic | ingest, validate, order, de-duplicate |
| DETECT | deterministic | momentum shifts, key moments; produces evidence objects |
| INVESTIGATE | agentic | investigator and challenger over typed tool evidence (Stage 4) |
| VERIFY | deterministic | keep / downgrade claims through 9 gates, including provenance replay; audited independently ([evidence-audit.md](evidence-audit.md)) |
| EXPLAIN | templated | narrative from verified findings only |
| PERSONALIZE | agentic or templated | Fan / Broadcaster / Analyst (Stage 6) |

## Real-time and insight lifecycle (to be designed in Stages 3–5, hardened in Stage 9)

Insights are versioned, not final: they can be created, updated, gain/lose confidence, become
obsolete, or be superseded by later evidence. Concerns to address: event ordering, idempotency,
duplicate and late events, latency, retries, correlation IDs.

## Azure (to be decided in Stage 8)

Candidate services are evaluated by "what problem does this solve?", not by availability.
Microsoft Foundry (models, tracing, evaluation) is the expected model platform; the Stage 4 LLM
adapter already speaks to OpenAI-compatible and Azure OpenAI endpoints. Microsoft Agent
Framework was evaluated in Stage 4 and not adopted for the current fixed-sequence topology
([ADR-0010](decisions/0010-agentic-investigation.md)). Hosting (Container Apps vs AKS), data
store and eventing are open. Decisions will be recorded as ADRs.
