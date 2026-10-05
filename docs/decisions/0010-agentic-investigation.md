# ADR-0010: Agentic investigation and verification topology

- Status: Proposed
- Date: 2026-10-04
- Stage: 4

## Context

Stage 3 ([ADR-0009](0009-contextual-evidence.md)) produces a graded, traceable list of
changes, capped at ASSOCIATED. Stage 4 must go further and ask what best explains each change,
weighing competing explanations, without:

* letting a model invent facts, events or metrics;
* letting it compute statistics that deterministic code already provides;
* giving it any path to the hidden truth (answer key, scenario IDs, generator state);
* promoting a plausible narrative over the evidence.

Agent and tool count is not a goal. Each component must own a decision that code cannot make
reliably.

## Decision

1. **Two reasoning roles and nothing more.** The **Investigator** plans which explanations to
   test and which evidence to fetch, then assesses each one. The **Challenger** attacks the
   assessment by naming untested or unresolved alternatives and requesting evidence for them.
   These are the only steps that need judgement. The Challenger exists because a separate
   adversarial turn is the cheapest guard against confirmation bias. Its output is a request,
   never a fact.
2. **Everything else is code:**
   * the case file (Match Analyst), because every field is a lookup;
   * eight typed tools, the only source of facts, each answering one decision;
   * the verifier;
   * the narrative, a template over the verified result, so agent free text is never shown.
3. **Facts flow one way.** Agents cite evidence only through `FactAssertion`s
   (`evidence_id`, `fact`, comparator, value). The verifier checks each one against the tool
   result: it must exist, be true, be relevant and be material. It scans the whole evidence
   pool for uncited contradictions, and looks up causes itself for the temporal gate.
4. **One declarative rule table** (`agents/hypotheses.py`) defines support groups and
   contradictions per explanation. The reference reasoner and the verifier share it, so "what
   counts as support" is written once, reviewable and testable.
5. **The claim ladder needs alternatives to fall, not confidence to rise.** HYPOTHESISED means
   a verified-supported explanation with temporal precedence and open alternatives. SUPPORTED
   needs three things: a Strong candidate, every plausible alternative *contradicted by
   evidence*, and a non-residual explanation. A tactical change has no observable trigger, so
   its only evidence is the change itself; it is capped at HYPOTHESISED. The final strength
   is min(proposed, eligible, ceiling) and is never upgraded.
6. **Explicit, bounded orchestration.** The flow is plan, fetch, assess, then at most one
   challenge round (fetch and reassess), then verify and conclude. It is a plain function
   with tool budgets, a deadline, one retry per model call and safe degradation (`unavailable`,
   never a fabricated narrative).
7. **Pluggable model, deterministic reference.** Both roles sit behind `ReasoningModel`.
   `RuleBasedReasoner` is the reference policy that tests and evaluation run.
   `OpenAICompatibleModel` (standard-library HTTP, environment-configured, HTTPS only, off by
   default) can fill the same roles. No credentials live in the repository.
8. **Microsoft Agent Framework is not adopted in Stage 4.** The topology is a fixed sequence
   with one bounded loop and no concurrent agents, handoffs or long-lived state. MAF would add
   a dependency and a runtime without changing any decision or guarantee. If later stages need
   concurrent specialists, durable runs or Foundry tracing, the two roles and the toolbox can be
   wrapped as MAF agents and tools without changing the contracts.
9. **Hidden-truth isolation is enforced by tests**, not convention. The tests cover layer
   imports, textual scans for hidden packages and scenario vocabulary (now including agents and
   orchestration), hidden field names in tool schemas and model inputs, network and process
   imports, and byte-identical investigations after an observable-only round trip.

## Alternatives considered

| Option | Why not |
| --- | --- |
| MAF group chat or handoff orchestration | No concurrency, handoff or durable state is needed; an explicit function is deterministic, cheaper and easier to test |
| An LLM verifier | Shares the investigator's failure modes; verification must be reproducible and auditable |
| Separate Match Analyst and Narrative agents | Lookups and templates; a model adds only the risk of misstating facts or carrying injected text |
| One specialist agent per explanation (tactical, fatigue, personnel, ...) | Each would apply one rule; a shared table plus one investigator is simpler, and the challenger already forces coverage |
| Open-ended debate or loop until consensus | Unbounded cost and latency; one round covered every plausible alternative on development data |
| A free-form `get_match_context` tool | Unstructured output cannot be verified fact by fact; replaced by the case file and typed tools |
| Confidence scores as the route to SUPPORTED | "High confidence = supported causation" is the error the ladder exists to prevent |
| Letting Moderate changes eliminate natural variation | Stage 3 controls show about 2.5 Moderate-or-better moments per match; on development seeds this produced confident claims in controls |
| Allowing tactical change to reach SUPPORTED | Its only evidence is the change itself, so SUPPORTED would be circular; a Strong control change was "explained" this way on development seeds |

## Consequences

Measured in [stage4-evaluation.md](../stage4-evaluation.md), held-out:

* **Integrity holds:**
  * 0 unsupported, untraceable, temporally invalid or above-ceiling claims in 6,849 final
    claims;
  * all 3,108 injected verifier faults caught (8 corruption types);
  * fully deterministic.
* **Conservative:** 71.5% of investigations end in insufficient evidence, and SUPPORTED is
  almost never reached (0.01 per planted match, none in controls).
* **No reliable planted-versus-twin discrimination.** Untriggered "any explanation" is 10%
  planted against 8% twin (development: 20% against 2%, which did not replicate). Stage 4 is
  bounded by Stage 3 recall: the expected explanation is considered in only 15–35% of planted
  insights.
* **Hypothesised claims in controls:** 2.5 per match, mostly score-state responses, which are
  consistent with the generator's background behaviour.
* **S07 decoy exceeded in 30% of matches**, by score-state responses after the forced goal.
  This is a decision for review: is a hypothesised explanation of the post-goal change a
  violation of a decoy that forbids causal claims about the goal?
* The LLM path is built, secured and tested with a fake transport, but not evaluated.
  Evaluating it requires credentials and a non-deterministic protocol (repeated runs,
  variance). The verifier bounds what it can claim regardless.
