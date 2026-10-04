# Architecture

Status: **skeleton (Stage 0)**. Component choices beyond the layering are deliberately open
until the synthetic data has been inspected (Stage 1) and the core engine proven (Stage 3).

## Principle

The system separates six concerns. Each has an owner in the codebase and a rule about what it
may and may not do.

| Concern | Owner | Rule |
| --- | --- | --- |
| FACTS | `domain`, `ingestion` | Directly derived from event data. Validated, typed, immutable. |
| ANALYTICS | `analytics` | Deterministic calculations. Never calls an LLM. Unit-tested. |
| AI REASONING | `agents` | Interprets structured evidence. Structured output only. May not invent facts. |
| EVIDENCE | `analytics` (objects) + verification (Stage 5) | Every claim links to metrics and source events. |
| NARRATIVE | `agents` (narrative role) | Renders *verified* findings; labels fact vs interpretation. |
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
| INVESTIGATE | agentic | specialist hypotheses over evidence (Stage 4) |
| VERIFY | agentic + deterministic checks | accept / downgrade / reject claims (Stage 5) |
| EXPLAIN | agentic | narrative from verified findings only |
| PERSONALIZE | agentic or templated | Fan / Broadcaster / Analyst (Stage 6) |

## Real-time and insight lifecycle (to be designed in Stages 3–5, hardened in Stage 9)

Insights are versioned, not final: they can be created, updated, gain/lose confidence, become
obsolete, or be superseded by later evidence. Concerns to address: event ordering, idempotency,
duplicate and late events, latency, retries, correlation IDs.

## Azure (to be decided in Stage 8)

Candidate services are evaluated by "what problem does this solve?", not by availability.
Microsoft Foundry (models, tracing, evaluation) and Microsoft Agent Framework (orchestration)
are the expected AI platform; hosting (Container Apps vs AKS), data store and eventing are open.
Decisions will be recorded as ADRs.
