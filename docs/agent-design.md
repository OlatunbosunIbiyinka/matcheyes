# Agent design

Status: **Stage 4 agents implemented; Stage 6 adds no agent**. The full method is in
[agentic-investigation.md](agentic-investigation.md) and the rationale in
[ADR-0010](decisions/0010-agentic-investigation.md). This page defines the bar every agent
must clear, and records the agents that cleared it.

## Admission rule

An agent exists only if a deterministic function cannot do the job reliably and the agent
adds measurable value (interpretation, investigation, synthesis, specialization, or
contextual reasoning). Agents are not added to increase the count.

## Required specification for every agent

| Field | Description |
| --- | --- |
| Responsibility | One sentence. |
| Reason to exist | Why a function is insufficient. |
| Inputs | Typed schema (evidence objects, not raw event dumps). |
| Outputs | Typed, structured schema. |
| Tools | Deterministic analytics functions it may call. |
| Boundaries | What it must never do (e.g. compute statistics, invent events). |
| Failure behaviour | Timeout, invalid output, low confidence: what happens. |
| Evaluation | How its value is measured (see `evaluation.md`). |

## Stage 4 agents

| Field | Investigator | Challenger |
| --- | --- | --- |
| Responsibility | Choose which explanations to test and which evidence to fetch; assess each against the evidence; propose a leading explanation and strength | Name plausible alternatives that were not tested or not eliminated, and request evidence to test them |
| Reason to exist | Weighing competing explanations against mixed evidence is judgement | A separate adversarial turn counters confirmation bias |
| Inputs | `AgentTask`: case file, evidence items, tested explanations | `AgentTask` plus the assessment |
| Outputs | `InvestigationPlan`, `Assessment` | `Challenge` |
| Tools | Requests only: eight typed tools run by the orchestrator | Requests only, same tools |
| Boundaries | Cites facts only via `FactAssertion`; never computes statistics, invents events or sees hidden truth | Cannot assert facts or change statuses |
| Failure behaviour | Retry once; then the explanation is `unavailable` and the Stage 3 candidate is kept | Retry once; then the assessment is verified as it stands |
| Evaluation | Planted / twin / control / decoy claim rates; downgrade rate | Alternatives gate; fault injection (dropped alternatives) |

Rejected candidate agents: Match Analyst, Narrative, Verifier (all code); one specialist per
explanation; Tactical and Performance Analysts (subsumed by typed tools and the shared rule
table).

## Stage 6: personalization is not an agent

A Personalization agent was considered and rejected
([ADR-0012](decisions/0012-personalization-presentation-layer.md),
[personalization.md](personalization.md)). Every step fails the admission rule:

| Step | Deterministic implementation |
| --- | --- |
| Is the favourite club / player / metric involved? | lookup in the match and the insight's cited events |
| How relevant is the insight? | weighted sum with a recorded basis |
| Which sections does this audience see? | fixed per-audience table |
| In which order? | sort by relevance, time, candidate ID |
| What does the view say? | templates over the verified fields |

A model would add an injection surface and non-reproducible relevance, and could drop a
caveat or add causal wording. If a model renderer is added later, it may only rephrase
sections the policy has already locked, its output must pass `audit_view`, and it falls back
to the deterministic text.
