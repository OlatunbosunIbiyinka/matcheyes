# Architecture

Status: **deterministic engine (Stages 0–3), agentic investigation (Stage 4), evidence audit
(Stage 5), deterministic personalization (Stage 6), a snapshot-anchored insight lifecycle
(Stage 7) and a broadcast cue contract with a live, read-only presentation surface (Stage 8)**.
Azure choices remain open.

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
| PRESENTATION | `personalization`, `broadcast`, `api` (incl. its static web surface) | Personalizes emphasis, order, depth and wording of *verified, audited* insights, and times them on a broadcast surface; never the underlying facts, strength, evidence or integrity. |

## Dependency rules

```
domain  <-  ingestion
domain  <-  analytics
domain, analytics  <-  agents
domain, ingestion, analytics, agents  <-  orchestration
domain, analytics, agents, orchestration  <-  personalization
domain, ingestion, analytics, agents, orchestration, personalization  <-  lifecycle
lifecycle, personalization, orchestration (and below)  <-  broadcast
broadcast, lifecycle, personalization, orchestration (and below)  <-  api
```

`orchestration` and the layers below it must not import `personalization`: presentation sits
downstream of verified truth and cannot feed back into it. Nothing but `broadcast`, `api` (and
the CLI) may import `lifecycle`; nothing but `api` (and the CLI) may import `broadcast`.

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

## Personalization (Stage 6)

Personalization turns each verified, audited `FinalInsight` into a fan, broadcaster or analyst
view in `matcheyes.personalization`:

```
FinalInsight ─► audit_insight ─► profile ─► policy + render (code) ─► PersonalizedInsight
             ─► audit_view (independent) ─► build_feed ─► audience feed / CLI
```

A view holds its source insight by reference, with a SHA-256 fingerprint, and copies no truth
field. Construction enforces the truth invariants; `audit_view` re-checks the rendered text
against the source, the Stage 3 candidate and the match. Preferences (favourite club, player
and metric) change relevance and order only. There is no agent and no model call. The method
is in [personalization.md](personalization.md), the decision in
[ADR-0012](decisions/0012-personalization-presentation-layer.md) and the results in
[stage6-evaluation.md](stage6-evaluation.md).

## Insight lifecycle (Stage 7)

A live match is a growing event log. `matcheyes.lifecycle` turns it into living insights
without changing any earlier stage:

```
events (any order, duplicates, gaps) ─► EventLog (idempotent; conflicts rejected; contiguous watermark)
  ─► one canonical snapshot per closed minute (content-addressed)
  ─► unchanged Stages 2–5 on that snapshot only ─► verified FinalInsights + own-snapshot audit
  ─► storyline reconciliation (deterministic identity) ─► append-only, chained revisions
  ─► lifecycle feed (current / withdrawn / notices) ─► Stage 6 views of current revisions
```

The lifecycle decides identity and records history. Truth is still decided only by the
pipeline, on one snapshot. An independent lifecycle audit re-derives every snapshot from the log
and re-checks every revision against its own snapshot. The method is in
[living-insights.md](living-insights.md), the decision in
[ADR-0013](decisions/0013-snapshot-anchored-insight-lifecycle.md) and the results in
[stage7-evaluation.md](stage7-evaluation.md).

## Broadcast cues and the live surface (Stage 8)

`matcheyes.broadcast` compiles the lifecycle record and the observable events into one cue
timeline per surface (fan or broadcaster, with or without a favourite club). `matcheyes.api`
replays it live:

```
lifecycle state + event log ─► CueCompiler, per closed snapshot:
    moments (Stage 3 key-event facts + period markers) ─► moment cues at the snapshot close
    current revisions ─► Stage 6 primary feed of that snapshot ─► insight / revision cues
    no longer current, off the primary feed, or snapshot unavailable ─► retraction cues
    status changes ─► status cues
  ─► CueTimeline (content-addressed, ordered by match time)
LiveMatch (one per match, server-owned): replay clock ─► LifecycleEngine ─► 6 compilers
  ─► published cues ─► GET /matches/{id}/timeline, GET /matches/{id}/stream (SSE) ─► static page
```

Broadcast presents; it decides no truth. Cue text is the Stage 6 view, the lifecycle's notice,
a Stage 3 fact or a fixed template, and every cue names its source. The API is read-only,
allow-listed and standard-library only; the page renders cue sections under a strict CSP and
performs no football reasoning. The decision is in
[ADR-0014](decisions/0014-broadcast-cue-contract-and-live-presentation-surface.md) and the
results in [stage8-evaluation.md](stage8-evaluation.md).

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
| EVENT | deterministic | ingest, validate, order, de-duplicate; contiguous watermark, conflicts rejected (Stage 7) |
| DETECT | deterministic | momentum shifts, key moments; produces evidence objects |
| INVESTIGATE | agentic | investigator and challenger over typed tool evidence (Stage 4) |
| VERIFY | deterministic | keep / downgrade claims through 9 gates, including provenance replay; audited independently ([evidence-audit.md](evidence-audit.md)) |
| EXPLAIN | templated | narrative from verified findings only |
| PERSONALIZE | deterministic, templated | Fan / Broadcaster / Analyst views of audited insights; preferences affect relevance only (Stage 6) |
| LIFECYCLE | deterministic | per closed minute: snapshot, re-run the pipeline, reconcile storylines, append revisions (Stage 7) |
| BROADCAST | deterministic | per closed minute: moment, insight, revision, retraction and status cues; served live over SSE (Stage 8) |

## Real-time and insight lifecycle (Stage 7; live replay in Stage 8; infrastructure later)

Stage 7 implements the lifecycle semantics as local, deterministic recomputation
([ADR-0013](decisions/0013-snapshot-anchored-insight-lifecycle.md)):

* ordering, idempotency, duplicates, late events and gaps are handled by the event log;
* insights are versioned as storylines with append-only revisions (OPEN or WITHDRAWN, with
  created, re-anchored, verdict, strength, explanation, integrity, evidence, withdrawn and
  reinstated changes);
* latency is bounded below by detection: at least about 15 match minutes after an onset (median
  18–22 minutes from the planted window start in the evaluation).

It is not production real-time streaming. Stage 8 replays the lifecycle live from a single,
in-memory process over Server-Sent Events on a deterministic replay clock
([ADR-0014](decisions/0014-broadcast-cue-contract-and-live-presentation-surface.md)). Transport,
storage, retries across processes and correlation IDs remain later infrastructure decisions.
Corrections to already-accepted events are rejected, not applied.

## Azure (deferred from Stage 8)

Candidate services are evaluated by "what problem does this solve?", not by availability.
Microsoft Foundry (models, tracing, evaluation) is the expected model platform; the Stage 4 LLM
adapter already speaks to OpenAI-compatible and Azure OpenAI endpoints. Microsoft Agent
Framework was evaluated in Stage 4 and not adopted for the current fixed-sequence topology
([ADR-0010](decisions/0010-agentic-investigation.md)). Hosting (Container Apps vs AKS), data
store and eventing are open. Decisions will be recorded as ADRs.
