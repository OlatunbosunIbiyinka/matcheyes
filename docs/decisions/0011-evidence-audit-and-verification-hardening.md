# ADR-0011: Evidence audit and verification hardening

- Status: Accepted
- Date: 2026-10-05
- Stage: 5

## Context

Stage 4 ([ADR-0010](0010-agentic-investigation.md)) caught every corrupted *assessment* it
was tested with, but it trusted three things:

* **The evidence pool.** If an item was edited after the tool produced it, the verifier
  checked assertions against the edited facts.
* **Materiality by fact name.** A true assertion about a rule's fact counted as support even
  when it stated nothing useful, for example `level_rank >= 0`.
* **Its own output.** Nothing checked the final insight and narrative independently.

The LLM path was built but never evaluated, and Stage 5 had no model credentials.

## Decision

1. **Provenance replay is a verifier gate (gate 9, `provenance`).** Before checks run, every
   evidence item's request is replayed through the deterministic `ToolBox`. An item is
   quarantined if its ID is a duplicate, its request does not replay, or the replayed result
   differs. Citations of a quarantined item are rejected and it never reaches the insight.
   `FinalInsight.quarantined` records it, and the narrative discloses that evidence was
   excluded. The investigation concludes on the remaining valid evidence. Nothing is
   re-fetched.
2. **Materiality requires entailment.** An assertion is material only if its comparator and
   value entail a rule (`agents/hypotheses.entails`), not merely if its fact is named in one.
3. **Hedged wording below SUPPORTED.** Explanation claims below SUPPORTED are typed
   `interpretive` and use `HYPOTHESIS_HEDGED` text. Causal wording (`HYPOTHESIS_DESCRIPTIONS`,
   claim type `causal`) is reserved for SUPPORTED.
4. **The independent auditor and lineage graph are evaluation and test tooling, not a second
   runtime gate.**
   * `orchestration/audit.py` re-derives what each insight may say without calling the
     verifier. It applies twelve checks, a claim category, a calibration lexicon and exact
     template conformance.
   * `orchestration/lineage.py` re-derives every link from claim to source events.
   * Both are deterministic engine code with no new state or dependencies.
   * They are imported only by `matcheyes_eval` and the tests; the orchestrator does not call
     them. The verifier remains the single runtime gate.
5. **LLM evaluation is blinded and paired with the reference.**
   * Items come from six datasets (planted, untriggered twins, triggered twins, controls,
     decoys, adversarial).
   * Each item runs with the model in both reasoning roles and is compared with the
     deterministic reference on the same item.
   * Every payload is scanned for answer-key vocabulary, and the model is never told the
     dataset.
   * Repeat runs classify variation as wording, procedural or material. A challenger on/off
     ablation measures the challenge round.
   * All of this lives in `matcheyes_eval`; hidden knowledge is used only to build and group
     items and to score results afterwards.
6. **SIMULATED fallback.** Without `MATCHEYES_LLM_*` configuration, `--profile live` reports
   "NOT evaluated" and exits 2. Seeded SIMULATED profiles (faithful, overclaiming, omissive,
   unreliable) perturb the reference output to exercise the harness and containment. Every
   output is labelled `SIMULATED-*`, and these profiles are never reported as model results.
7. **Hardened LLM adapter** (`agents/llm.py`):
   * HTTPS only;
   * HTTP redirects are refused, so the API key cannot follow a redirect to another host;
   * responses are capped at `MAX_RESPONSE_BYTES`;
   * `Usage` records only the token counts the endpoint reports, never estimates.

   The **payload-key whitelist** is enforced by an architecture test
   (`tests/architecture/test_stage5_security.py`), not at runtime by the adapter. The test
   checks that every model payload built for synthetic scenarios contains only schema, tool,
   fact and hypothesis keys, and no answer-key values.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Sign evidence items (an HMAC) instead of replaying | Needs a key and key management; replay is exact because tools are deterministic, so it gives the same guarantee for free |
| Re-fetch the true result for a quarantined item (evidence recovery) | Deferred, not rejected. It would restore ranking after tampering, but the investigator would then be judged on evidence it did not choose, and the recovery path needs its own red team. Recorded as future hardening |
| Make the auditor a runtime gate | Two gates with overlapping rules give two sources of truth; the auditor's value is as an independent check on the verifier |
| Enforce the payload whitelist at runtime in the adapter | Payloads are already built only from typed contracts and tool results; a runtime filter would duplicate the contract. The test guards against drift |
| An LLM judge for narrative quality | Shares the failure modes being measured; the narrative is a template, so exact conformance is a stronger test |
| A larger causal lexicon or embedding similarity | More false positives and a dependency; template conformance already guards the narrative |
| Relax the residual tactical policy | The three cases where it overrides an evidence-valid tactical reading are all triggered twins with no planted instruction; the policy was correct in each (see the evaluation) |

## Consequences

Measured in [stage5-evaluation.md](../stage5-evaluation.md), 5 seeds per split:

* **Faults:** 16,197 of 16,197 detected on development and 17,216 of 17,217 on held-out.
* **The one miss: safety held, ranking did not.** A quarantined evidence substitution
  produced no unsupported claim, but it changed which hedged explanation ranked first.
  * The response is detect, quarantine, disclose, then conclude on the remaining valid
    evidence.
  * This is not a complete solution to provenance; evidence recovery is future work.
* **Clean runs:** 0 quarantines, 0 broken lineage and 0 auditor findings across 2,588
  investigations; 0 of 11,514 reference assertions rejected.
* **Valid-case catalogue:** 3 candidates where tactical change is valid on evidence alone but
  overridden by the residual policy. All three are triggered twins with no planted
  intervention, so the policy is correctly conservative. The catalogue's evidence-only
  definition does not model the policy.
* **Decoys:** 1 audited S07 violation on development (a hedged tactical change), kept as a
  known breach; none on held-out.
* **LLM path:** not evaluated live. SIMULATED profiles show the modelled overclaiming, omission
  and failure modes contained. They say nothing about a real model.
* **Determinism is now load-bearing.** Any future change to the tools must keep them
  deterministic, or provenance replay will quarantine valid evidence.

## Amendment: evidence integrity flag (2026-10-05)

Detection does not guarantee recovery of the correct explanation. To decision 1 is added:

* each verification result and final insight carries `evidence_integrity` (`intact`,
  `unaffected` or `compromised`);
* a quarantine that could bear on the insight makes it `compromised`. Relevance is judged from
  citation, survival of a duplicate's original, and the recorded request, never from the
  untrusted facts;
* a compromised insight is capped at HYPOTHESISED, so compromised evidence never produces a
  stronger claim;
* the narrative states that evidence relevant to the insight was excluded and that the
  explanation may be incomplete;
* the auditor and lineage check the state.

Re-fetching remains future work. Measured in
[stage5-evaluation.md](../stage5-evaluation.md#4-fault-results):

* 11,025 tampered investigations over both splits were all flagged, and none was stronger
  than its clean run;
* clean runs were never flagged.
