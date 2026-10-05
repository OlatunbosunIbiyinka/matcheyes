# Agentic investigation and verification (Stage 4)

Status: **implemented (Stage 4)**, investigation version 0.1.0. Design rationale:
[ADR-0010](decisions/0010-agentic-investigation.md). Results:
[stage4-evaluation.md](stage4-evaluation.md).

Stage 4 asks one question of each Stage 3 candidate:

> What best explains this observed change, and what evidence supports or weakens each
> explanation?

**Agents investigate. They do not supply facts.** Every fact comes from a deterministic tool
over the observable match and the Stage 2–3 analyses. The agents choose which explanations to
test and which evidence to fetch, then assess. Deterministic code checks each assertion before
anything reaches a reader.

## Architecture

```
Stage 3 candidate (Weak or above)
  │
  ▼
Match Analyst (code) ──► case file: observable facts + plausible explanations (screen)
  │
  ▼
Investigator (model) ── plan ──► ToolBox (code) ──► evidence items (facts + event IDs)
  │                                                    │
  ◄──────────────────────── assess ◄───────────────────┘
  │
  ▼
Challenger (model) ── untested / unresolved alternatives + requests ──► ToolBox
  │
  ▼
Investigator (model) ── reassess (once)
  │
  ▼
Verifier (code) ── 9 gates (provenance added in Stage 5), claim ladder, never upgrades
  │
  ▼
Narrative (code) ── templated FACT / ANALYSIS / AI INTERPRETATION from the verified result
```

| Module | Layer | Contents |
| --- | --- | --- |
| `agents/contracts.py` | agents | Every typed message exchanged: requests, evidence, assertions, plans, assessments, challenges, verification, final insight |
| `agents/tools.py` | agents | `MatchWorkspace` (observable match + analyses), eight typed tools, `ToolBox` (argument validation) |
| `agents/hypotheses.py` | agents | The explanation vocabulary, the plausibility screen, and the declarative support/contradiction rules shared by the reference reasoner and the verifier |
| `agents/casefile.py` | agents | The case file (Match Analyst) |
| `agents/reasoning.py` | agents | `ReasoningModel` protocol, `AgentTask`, `RuleBasedReasoner` (reference policy) |
| `agents/roles.py` | agents | Investigator, Challenger, the guarded model call (`run_step`) |
| `agents/verification.py` | agents | The verifier |
| `agents/narrative.py` | agents | Conclusion and templated narrative |
| `agents/llm.py` | agents | Optional OpenAI-compatible model adapter (off by default) |
| `orchestration/investigation.py` | orchestration | The explicit flow, budgets, failure handling |
| `orchestration/trace.py` | orchestration | Trace records |

CLI: `python -m matcheyes investigate <match_dir> [--json out.json] [--llm]`.

## Agent responsibilities

Only two roles need judgement. Everything else is a lookup, a rule or a template, so it is code.

| Component | Kind | Responsibility | Why this kind |
| --- | --- | --- | --- |
| Match Analyst | code | States the observable facts of one candidate and screens plausible explanations | Every field is a lookup; a model could only misstate a fact |
| **Investigator** | model role | Chooses which explanations to test and which evidence to fetch; assesses each against the evidence; proposes a leading explanation and a strength | Planning and weighing evidence is the judgement the stage exists for |
| **Challenger** | model role | Attacks the assessment: names plausible alternatives that were not tested or not eliminated, and requests the evidence to test them | A separate adversarial turn counters the investigator's confirmation bias; its output is a *request*, so it cannot inject facts |
| ToolBox | code | Runs one validated request and returns facts plus event IDs | Tools are the only source of facts |
| Verifier | code | Re-derives every status from tool results and Stage 3; downgrades, never upgrades | A model checking a model shares its failure modes |
| Narrative | code | Restates only what verification kept, at the same strength | Free text from agents is never shown, so it cannot carry an injected or unverified claim |

Both roles sit behind `ReasoningModel`, so they can be filled by:

* `RuleBasedReasoner`, the deterministic reference policy. Tests and evaluation run it, so
  results are reproducible and need no credentials. As Investigator it tests the explanations
  that the candidate and nearby key events suggest. As Challenger it insists on the
  alternatives the Investigator deferred (late-match decline, opponent-driven) and on a
  persistence check for any leading explanation.
* `OpenAICompatibleModel`, any chat-completions endpoint (including Azure OpenAI and Microsoft
  Foundry deployments). It is configured only through environment variables and uses the
  same contracts.

## Orchestration

The orchestrator is a plain, explicit sequence. There is no open-ended agent loop.

1. Build the case file.
2. Investigator plan, then fetch its requests.
3. Investigator assess.
4. Challenger challenge. If it names new alternatives or requests evidence: fetch, then
   Investigator reassess. **This happens at most once.**
5. Verify, then conclude.

Bounds: `InvestigationConfig(min_level=WEAK, model_attempts=2, max_tool_calls=30,
deadline_s=60)`. Candidates below Weak were already removed by Stage 3 context and are listed
as `not_investigated`. Request IDs are de-duplicated: a reused ID is not refetched, and a reused
ID with new arguments is recorded as such.

## Contracts

Agents exchange only typed Pydantic models with `extra="forbid"`.

* `EvidenceRequest`:
  * `request_id`, matching `^[a-z0-9][a-z0-9-]{0,39}$`;
  * `tool`, a closed enum of eight tools;
  * `arguments`, at most 10 names matching `^[a-z][a-z_]{0,29}$`, with primitive values and
    strings of at most 80 characters;
  * `hypothesis` and `purpose`.
* `InvestigationPlan`: at least one hypothesis and at most 20 requests.
* `Challenge`: alternatives, at most 10 objections and at most 20 requests. An empty
  challenge is valid.
* `Assessment`: one `ProposedHypothesis` per tested explanation, each with a `status` and with
  supporting and contradicting `FactAssertion`s (`evidence_id`, `fact`, comparator, value),
  plus `leading` and `proposed_strength`.
* `VerificationResult`: verified status per explanation, gate outcomes, eligible and final
  strength, and downgrades.
* `FinalInsight`: verdict, leading explanation, strength, claims (factual, interpretive or
  causal) with evidence IDs, alternatives with verified statuses, the full evidence pool,
  event IDs, downgrades, failure and narrative.

The schema alone cannot express some rules, so `roles.py` checks them: no duplicate hypotheses
or request IDs, and the leading explanation must have been assessed.

## Tools

Each tool answers one decision. Arguments are validated twice: once by the argument model, and
once against the match (teams, metrics, spans within the timeline and at most 45 bins,
candidates). An invalid request raises `ToolError`, which is recorded and never fatal, and the
error text never echoes the arguments.

| Tool | Decision it supports |
| --- | --- |
| `get_candidate_assessment` | How strong is the change, and does Stage 3 context already explain it? |
| `inspect_persistence` | Did the change hold across its window, or fade or reverse? |
| `check_game_state_response` | Was there a goal or dismissal just before, and is the change its ordinary response? |
| `get_key_events` | Which goals, dismissals, substitutions or formation changes fall in a span? |
| `compare_windows` | Did a metric change between two spans (supporting or contradicting signal)? |
| `get_substitute_involvement` | Were substitutes involved in a metric's events more than their minutes predict? |
| `get_workload` | How long have this team's outfield players been on, and how active were they? |
| `find_team_changes` | Did a team show a graded Stage 3 change in a span (for example the opponent, first)? |

Tools reuse Stage 2–3 calculations and restate them. They never estimate anything new beyond
counts, shares and window comparisons.

## Evidence model

An `EvidenceItem` holds:

* `evidence_id`, assigned by the orchestrator (`ev-01`, …);
* the request ID, tool and arguments;
* the team and the bin span it covers;
* `facts`, a flat dictionary of primitives, which is **the only authoritative content**;
* `event_ids`, observable events that back the facts;
* `summary`, templated text for readers.

An agent cites evidence only through `FactAssertion`s, which the verifier evaluates against
`facts`.

## Hypothesis model

There are eight explanations (`HypothesisKind`). They are explanations, not generator labels:

| Explanation | Kind | Plausible when |
| --- | --- | --- |
| `score_state_response` | triggered | a goal falls in the trigger window |
| `numerical_change` | triggered | a dismissal falls in the trigger window |
| `personnel_change` | triggered | the team made a substitution in the window |
| `formation_change` | triggered | the team announced a formation change in the window |
| `opponent_driven` | triggered (opponent's earlier Strong change) | always |
| `late_match_decline` | untriggered, with workload evidence | match minute ≥ 60 |
| `tactical_change` | **residual**: untriggered, no independent evidence | always |
| `natural_variation` | null explanation | always |

`hypotheses.py` holds one declarative table, used by both the reference reasoner and the
verifier:

* `SUPPORT_GROUPS`: SUPPORTED needs every group matched by a cited, true and *material*
  assertion.
* `CONTRADICTIONS`: the verifier also scans the whole evidence pool for these, not only the
  cited assertions.
* Absence rules ("no substitution happened") count only if the evidence span covers the
  explanation's window.

Two calibration facts from Stage 3 shape the rules:

* Control matches show about 2.5 Moderate-or-better moments per match. So only a **Strong**,
  sustained change contradicts natural variation, and the untriggered explanations (tactical,
  late-match decline, opponent-driven via the opponent's change) need a Strong change.
* A tactical change has no observable trigger, so its only evidence is the change itself.
  Eliminating every alternative leaves it the best remaining reading, not a supported cause. It
  is capped at HYPOTHESISED (`RESIDUAL`).

## Verification

The verifier is code. For every assessed or plausible explanation it checks each cited
assertion in turn. An assertion is rejected if:

* the evidence ID is unknown;
* the fact is missing;
* the value is false;
* the evidence is irrelevant to the explanation (wrong tool, wrong team, or outside the time
  window).

Otherwise the assertion is accepted, and is material only if a rule in the table holds on the
actual value. The verifier then scans the pool for uncited contradictions and assigns the
verified status.

| Gate | Passes when |
| --- | --- |
| `factual` | every cited assertion names a real item and fact, and is true |
| `traceable` | the leading explanation's support resolves to at least one event ID |
| `temporal` | the verifier looks up the cause itself (goal, card, substitution, formation change, the opponent's change) and finds it before the change |
| `relevant` | cited evidence comes from a tool, team, candidate and period that bear on the explanation |
| `contradictions` | no material contradiction for the leading explanation anywhere in the pool. Stage 3's own contradictions (transient, reversed, contradicted pattern) cap a causal explanation at PARTIALLY_SUPPORTED |
| `alternatives` | every plausible explanation was assessed |
| `strength_eligible` | the proposed strength is within the ladder below |
| `no_unsupported_assertions` | no cited assertion was rejected |

Status rules:

* **SUPPORTED** needs all of: every support group matched, no contradiction in the pool, no
  rejected assertion, the temporal gate, event-backed support, and no Stage 3 contradiction.
* **CONTRADICTED** needs a cited, material, accepted contradiction.
* An explanation nobody assessed is INSUFFICIENT_EVIDENCE.
* A verified trigger (goal, dismissal, substitution, formation change) contradicts
  `tactical_change` by definition.

The final strength is the minimum of the proposed strength, the eligible strength and the
ceiling (SUPPORTED). It is never upgraded, and every reduction is recorded in `downgrades`.

## Claim ladder

| Strength | Stage 4 meaning |
| --- | --- |
| `observed` / `associated` | Inherited from Stage 3: the change itself, with no explanation established. Natural variation as the leading explanation reports `observed` |
| `hypothesised` | A verified-supported explanation with temporal precedence, but at least one plausible alternative has not been weakened by evidence, or the change is not Strong, or the explanation is residual |
| `supported` | As hypothesised, on a **Strong** candidate, with **every plausible alternative contradicted** by verified evidence, and not residual |

High confidence is not SUPPORTED. SUPPORTED requires alternatives to fall, and each has to be
contradicted by evidence. Merely being unassessed does not count.

Verdicts:

* `explained` (supported);
* `tentative` (hypothesised);
* `natural_variation`;
* `insufficient_evidence`;
* `unavailable`, when the investigation failed.

## Failure behaviour

| Failure | Behaviour |
| --- | --- |
| Malformed or oversized model output (above 100,000 characters) | Retried once. The feedback gives field paths and messages only, never the model's own text |
| Second malformed reply, or model unavailable twice | `RoleFailureError` |
| Investigator plan or assessment fails | Verdict `unavailable`. The Stage 3 candidate and its factual claim are kept; the narrative says "Explanation unavailable" and makes no interpretation |
| Challenger or reassessment fails | The current assessment is verified as it stands. Untested alternatives then block any claim above HYPOTHESISED |
| Invalid tool request | Recorded as a failed tool call and skipped |
| Tool budget exhausted | Further requests are recorded as skipped |
| Deadline passed | Checked before each tool call and model step. Before assessment the verdict is `unavailable`; during the challenge the existing assessment is verified |

No narrative is ever written to cover a failure.

## Observability

Each investigation returns `trace: tuple[TraceRecord, ...]`. Each record holds:

* the investigation and candidate IDs, and a sequence number;
* the component and action;
* the status (ok, retry, failed, skipped or downgraded);
* the hypothesis, tool and validated inputs;
* the result reference (evidence ID);
* a detail of at most 600 characters, and the latency.

The trace records the model name and attempt number. It never records prompts, model output,
credentials or anything outside the observable match, so a trace can be stored or shown without
leaking secrets or answer-key information. Tests enforce this.

## Security

| Concern | Control |
| --- | --- |
| Prompt injection in match or event text | Match data reaches a model only as JSON inside a `<match_data>` fence, with `<` escaped so data cannot close the fence. The system prompt says to treat it as data. Agent free text never reaches the narrative |
| Tool argument validation | Closed tool enum; bounded argument contract; per-tool argument models with `extra="forbid"`; match-level checks (team, metric, span, bin, candidate) |
| Arbitrary tool execution | The model has no tools of its own. It can only *request* evidence, and the orchestrator runs requests against the closed `ToolBox`. Architecture tests forbid `eval`, `exec`, `__import__`, `os`, `subprocess` and `importlib` in agents and orchestration |
| Structured output validation | Strict parsing, consistency checks, size limit, one retry. The verifier checks every assertion |
| Least privilege | Tools read only the `MatchWorkspace`: the observable match and its analyses. Network access exists only in `llm.py`, and is tested |
| Secret isolation and authentication | The endpoint, key and model come from environment variables only; the key is held as a `SecretStr`. HTTPS is enforced, and bearer or `api-key` (Azure) authentication is supported. Errors report the exception type only. No credentials exist in the repository |
| Output validation | The verifier gates, then the templated narrative. The final strength can never exceed what the verifier allows |

## Hidden-truth isolation

The reasoning system has no path to the answer key:

* `agents` and `orchestration` may import only `domain`, `ingestion` (orchestration only),
  `analytics` and `agents` (`test_layer_boundaries`).
* No engine source mentions `matcheyes_synth` or `matcheyes_eval`
  (`test_truth_separation`). Analytics, agents and orchestration never name scenarios,
  interventions, clubs, answer keys, planted causes, twins, decoys or
  `supporting_mechanisms` (`test_no_scenario_decoding`).
* The tool schemas, the explanation vocabulary and everything a model is shown contain no
  hidden-state field names, scenario IDs or intervention IDs (`test_agent_isolation`).
* An investigation of a match written to disk and reloaded (which drops every truth object)
  is byte-identical to the original (`test_agent_isolation`).
* Only `matcheyes_eval/stage4.py` joins investigations with the answer key, after every
  investigation of a match has finished.

## Evaluation

See [stage4-evaluation.md](stage4-evaluation.md). The protocol has five parts:

* planted against twin, by twin type (untriggered, observable trigger, fatigue);
* claim density in controls;
* decoy ceilings;
* integrity counters (unsupported, untraceable, temporal, above ceiling) and determinism;
* verifier fault injection.

## Rejected designs

| Design | Why not |
| --- | --- |
| Microsoft Agent Framework orchestration | The flow is a fixed five-step sequence with one bounded challenge round; an explicit function is clearer, deterministic and testable. MAF would add a dependency and a runtime without changing a decision ([ADR-0010](decisions/0010-agentic-investigation.md)) |
| LLM verifier | Shares the investigator's failure modes; verification must be reproducible |
| Separate Match Analyst and Narrative agents | Both are lookups or templates; a model adds only the risk of misstatement |
| One specialist agent per explanation | Eight agents would each run one rule; the shared rule table plus one investigator is simpler and auditable |
| Open-ended agent loop or debate | Unbounded cost and latency; one challenge round reached every plausible alternative on development data |
| A free-form `get_match_context` tool | Returned unstructured context that could not be verified fact by fact; replaced by the case file and typed tools |
| Agents computing statistics | Duplicates deterministic code and invites invented numbers |
