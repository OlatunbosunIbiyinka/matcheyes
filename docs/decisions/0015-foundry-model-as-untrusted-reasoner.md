# ADR-0015: A Microsoft Foundry model as an untrusted reasoner behind the existing boundary

- Status: Proposed (Stage 9 implementation awaiting review)
- Date: 2026-10-09
- Stage: 9

## Context

Stages 4–5 ([ADR-0010](0010-agentic-investigation.md), [ADR-0011](0011-evidence-audit-and-verification-hardening.md))
defined the reasoning boundary: an Investigator and a Challenger produce typed proposals
(`InvestigationPlan`, `Assessment`, `Challenge`) through a `ReasoningModel` protocol, typed tools
answer evidence requests, and a deterministic verifier, claim auditor and lineage audit decide
what may be claimed. The only implementation so far was the deterministic `RuleBasedReasoner`;
an OpenAI-compatible adapter existed but no hosted model had been evaluated. Stages 7–8
([ADR-0013](0013-snapshot-anchored-insight-lifecycle.md), [ADR-0014](0014-broadcast-cue-contract-and-live-presentation-surface.md))
built the lifecycle and the broadcast surface on top of that boundary.

The Stage 9 discovery chose option B: put a real Microsoft Foundry model behind the existing
boundary, keep the rule-based reasoner as the reference oracle and default, and never let public
web traffic call the model.

## Decision

**The hosted model is an untrusted proposal generator.** It is one more `ReasoningModel`. Every
reply passes the same contracts, tools, verifier, claim auditor and lineage audit as the
reference; nothing it writes becomes a `FinalInsight`, screen text or truth. Stages 2–8 semantics
(metrics, candidates, verification rules, evidence integrity, lifecycle, cues) are unchanged.

### Model access (`agents/wire.py`, `agents/llm.py`, `agents/entra.py`)

* **Strict wire schemas.** Hosted structured outputs (`strict: true`) accept a JSON Schema subset
  the Stage 4 contracts deliberately exceed (patterns, lengths, free-key maps). Each model-produced
  contract has a wire model carrying the same information in that subset; `to_contract` rebuilds
  the contract, so every contract limit is still enforced. The contracts remain the authority.
* **Adapter.** Chat Completions against the Foundry / Azure OpenAI v1 endpoint
  (`https://<resource>.openai.azure.com/openai/v1/`), `store: false`, bounded response size,
  redirects refused, 429/5xx retried with `Retry-After` (three attempts), everything else fails
  at once. The deployment name and the served model version (`gpt-5-mini-2025-08-07`) are recorded.
* **Authentication.** Microsoft Entra ID through `DefaultAzureCredential` (role "Cognitive
  Services OpenAI User"); keys are supported for other endpoints but the Foundry resource has
  local (key) auth disabled. `azure-identity` is an optional extra imported only by
  `agents/entra.py`; tokens are cached and refreshed near expiry under a lock. Credentials come
  only from the environment.
* **Deadline.** Model-backed paths use `MODEL_CONFIG` (deadline 3,600 s): a hosted call takes
  ~30 s, and the reference's 60 s deadline would skip the challenge round.

### Role split (configuration B′) — adopted

Two configurations were evaluated on the held-out split ([stage9-evaluation.md](../stage9-evaluation.md)):

* **B** — the hosted model as Investigator and Challenger;
* **B′** — the `RuleBasedReasoner` as Investigator, the hosted model as Challenger
  (`agents/split.py`, `RoleSplitModel`).

B′ is adopted because the measured model Investigator is materially inadequate: in B it never
reached an explanation at hypothesised or above (0% in every dataset, reference 16–50%), found
0/10 planted explanations (reference 1/10), grounded 15–18% of its assertions, and left two
hard invariants non-zero (hidden-truth leakage 2, unresolved; final auditor finding 1). In B′
every hard invariant is zero, verified results agree with the reference in 92–100% of
investigations, and the Challenger adds an alternative in 98–100%. The switch is explicit in
transcript metadata (`roles`) and in the reasoner's name; it is not silent.

### Recordings, not live calls, on the public surface (`agents/recorded.py`, `lifecycle/recording.py`)

* `RecordingModel` records one content-addressed entry per distinct request (`matcheyes.transcript/1`:
  canonical request = namespace + the observable `AgentTask`; sha256 key; response and its
  sha256). `RecordedModel` answers only from a transcript: no endpoint, no credentials, no
  fallback.
* The transcript's identity is the sha256 of its canonical JSON; the deployment pins it
  (`deploy/recordings/pins.json`). A pin mismatch makes the whole transcript unusable; a
  tampered entry is dropped. Every miss raises `ModelUnavailableError`, so the roles fail safe
  (`unavailable`), the lifecycle records it, and the broadcast layer retracts and reports status.
  Nothing is fabricated and nothing falls back to the reference silently.
* `python -m matcheyes record` records a whole match lifecycle once (only the canonical delivery;
  Stage 7 transport perturbations create unrecorded snapshots and therefore fail closed).
  `serve --recordings` replays it. `serve` never constructs a live model, even with
  `MATCHEYES_LLM_*` set (architecture test).

### Show your work (`lifecycle/explain.py`, `GET /matches/{id}/storylines/{sid}/revisions/{n}`)

* A read-only endpoint returns, for a **published** revision only, a structured explanation:
  lifecycle facts, the verified result and its gates, the explanations considered (each verified
  status followed by the proposal it came from, marked "proposed"), the evidence requested and
  returned, and the reasoner identity. The page renders server-built rows generically.
* It is rebuilt by re-running the recorded model and the reference on that revision's snapshot
  and checking that the fingerprint reproduces the published insight. Model free text (purposes,
  statements, summaries, objections, rejected values, unknown fact names) never appears: only
  enumerated kinds, tool names, accepted values and fixed phrases. The verifier's rows come first
  and are styled as authoritative; proposals are muted and labelled as not conclusions.

### Evaluation and leakage attribution (`matcheyes_eval/stage9.py`, `matcheyes_eval/leakage.py`)

* Held-out datasets A–F plus Stage 5 tampering on model investigations, scored offline from the
  transcript; replays must equal the live run. No quality targets were set; hard invariants must
  be zero.
* Leakage is reported two ways. The combined invariant counts any forbidden term in a prompt,
  whoever wrote it. Field-attributed findings separate engine-authored content (must be zero:
  a confirmed exposure) from text the model wrote and had echoed back (`assessment`, `feedback`),
  traced to the reply that produced it. A model-originated hit is "lexical" only when that reply
  exists, no engine field of the investigation carried the term, and the term does not name the
  item's hidden truth; otherwise it stays unresolved. Full prompt captures are kept for review.

### Hosting (prepared, not deployed)

A two-stage `Dockerfile` (python:3.13-slim, hash-pinned requirements, pydantic only, non-root
user 10001, an allow-listed build context) serves the bundled observable match and its pinned
recording. It carries no Foundry credentials, API keys, endpoint settings or cloud SDK. The
planned target is Azure Container Apps with one replica; the deployment has not been performed
(see Consequences).

## Alternatives considered

| Option | Why not |
| --- | --- |
| Replace `RuleBasedReasoner` with the model | The reference is the evaluation oracle and the measured model Investigator is inadequate |
| Live model calls behind HTTP/SSE or per lifecycle snapshot | Unbounded cost and latency (~30 s per call), non-reproducible output, credentials on the public surface |
| Foundry Agent Service / Microsoft Agent Framework orchestration | Orchestration is already explicit and bounded (ADR-0010); a hosted agent runtime adds no capability the verifier needs |
| Send the Stage 4 contracts as the strict schema | Rejected by strict structured outputs; weakening the contracts would weaken validation |
| Let the model write card text or `FinalInsight` | Unverifiable free text would become screen truth |
| Fall back to the reference on a transcript miss | A silent switch of reasoner; a miss must be visible as `unavailable` |
| Configuration B (model as both roles) | Materially inadequate Investigator; non-zero hard invariants (measured) |

## Consequences

* The public demo shows a **recorded** real-model run (B′: the hosted model as Challenger),
  replayed deterministically. It is not production real-time model inference.
* Recorded and replayed lifecycles are identical, and every Stage 7 and Stage 8 property that
  applies to a recording holds on the recorded S05 match.
* Costs are paid once at recording time (held-out B $12.72, B′ $2.29, S05 recording ≈ $3.23 at
  $0.25 / $2.00 per million tokens).
* Configuration B's unresolved findings remain documented, not relabelled: two
  model-originated lexical hits that name the item's hidden-truth vocabulary (not traced to any
  engine input) and one contained auditor finding.
* Azure Container Apps deployment is blocked by the approval rule that every hard security
  invariant must be resolved first.

## Explicit non-goals

No Foundry Agent Service, Microsoft Agent Framework, AKS, Event Hubs, Cosmos DB, Redis,
Functions, API Management or Key Vault. No model calls from public traffic, no model text on
screen, no hidden synthetic truth in any model input, no changes to Stage 2–8 semantics.
