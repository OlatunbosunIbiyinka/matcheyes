# Stage 5 evaluation: evidence audit, model evaluation and verification hardening

Stage 5 asks whether the Stage 4 guarantees hold up under attack. It adds:

* an evidence lineage graph;
* an independent claim and narrative auditor;
* a provenance replay gate;
* entailment-based materiality;
* hedged claim text;
* a three-layer red team;
* a blinded harness for evaluating a language model on the reasoning roles.

Design: [evidence-audit.md](evidence-audit.md). Decision:
[ADR-0011](decisions/0011-evidence-audit-and-verification-hardening.md).

> **The live LLM path was NOT evaluated.** No `MATCHEYES_LLM_*` configuration was available.
> Every LLM-path number below comes from **SIMULATED profiles**: seeded perturbations of the
> deterministic reference output, labelled `SIMULATED-*` in every table. They test the harness
> and the containment of known failure modes. They are **not evidence about any language model**.

**Three kinds of evaluation, never mixed:**

| Kind | What runs | Status | What it can show |
| --- | --- | --- | --- |
| Reference (deterministic) | `RuleBasedReasoner`, verifier, auditor, lineage, red team | **Run**, both splits, 5 seeds | integrity, fault detection, false rejection, decoys, determinism |
| Simulated LLM | `SIMULATED-*` profiles: seeded perturbations of the reference | **Run**, held-out, 115 items per profile | that the harness works and that the modelled failure modes are contained |
| Live LLM | `OpenAICompatibleModel` via `MATCHEYES_LLM_*` | **NOT RUN**, no credentials or model server configured | real-model performance; planned at about 600 calls |

The live evaluation is not part of the deterministic Stage 5 gate. It is run when credentials
are provided through the environment.

**Safety versus correctness.** The two are reported separately throughout:

* **Safety** means no unsupported, unverified or above-eligible claim reaches the reader. On
  the evidence here it held in every clean run and under every injected fault, including the
  one miss.
* **Correctness** means the explanation the reader sees is the one the untampered evidence
  best supports. It was affected once: in the held-out provenance miss (section 4), quarantine
  removed real evidence and a different hedged explanation ranked first. Stage 5 does not
  prevent this. Instead, the insight is now explicitly flagged as having **compromised
  evidence integrity**, capped at hypothesised, and the reader is told the explanation may be
  incomplete (section 4, "Evidence integrity flag"). The system fails safely by making the
  uncertainty visible, not by recovering the original explanation.

Reproduce:

```bash
uv run python -m matcheyes_eval redteam --split development --seeds 5
uv run python -m matcheyes_eval redteam --split held-out    --seeds 5
uv run python -m matcheyes_eval llm --profile faithful     --split held-out --seeds 2 --matches 7
uv run python -m matcheyes_eval llm --profile overclaiming --split held-out --seeds 2 --matches 7
uv run python -m matcheyes_eval llm --profile omissive     --split held-out --seeds 2 --matches 7
uv run python -m matcheyes_eval llm --profile unreliable   --split held-out --seeds 2 --matches 7
# live, only with MATCHEYES_LLM_ENDPOINT / _API_KEY / _MODEL set in the environment:
uv run python -m matcheyes_eval llm --profile live --split held-out --seeds 2 --matches 7
```

## 1. Methodology

* **Nothing was tuned to the evaluation.** Scenario effect sizes, the rule table, the claim
  ladder and the verifier gates were left alone. Stage 5 only *adds* rejections: provenance,
  entailment and the narrative disclosure.
* **The red team** (`matcheyes_eval/redteam.py`) injects faults at three layers:
  * **A**, corrupt agent assessments (14 types);
  * **B**, tamper with evidence after the tool produced it (9 types);
  * **C**, tamper with the final insight or narrative (11 types).

  Each fault is applied to every clean investigation it can affect, on every planted match of
  the split. A fault is **missed** if it reached the reader (a stronger claim, a different
  strong explanation, or tampered evidence in the final pool) and no audit flagged it.
* **False-rejection tests** run the verifier, the auditor and lineage on every clean reference
  investigation, plus a catalogue of valid cases (section 5).
* **The LLM harness** (`matcheyes_eval/llm_eval.py`) builds items from six datasets:
  * A, planted;
  * B, untriggered twins;
  * C, triggered twins;
  * D, controls;
  * E, decoy matches;
  * F, adversarial.

  It runs each item through the same orchestrator with a model in both reasoning roles and
  compares the result with the deterministic reference on the same item. The model sees only
  the case file, tool schemas and tool results. Every payload is scanned for answer-key
  vocabulary before it is sent.
* Results are reported per dataset, and the held-out split is the primary one. The red team
  was run on both splits.

## 2. Datasets

| Dataset | Selection (evaluator side) | What the model sees |
| --- | --- | --- |
| A planted | planted variant, no decoy | the top 3 Stage 3 candidates of the match |
| B untriggered twins | twin of a manager-instruction scenario | same |
| C triggered twins | twin that keeps its goal, card or substitution | same |
| D controls | no intervention | same |
| E decoys | planted scenarios with decoys (S07, S08) | same |
| F adversarial | observable criteria: temporal trap, recent goal, recent substitution, contradictory evidence, small sample, single metric, coincidental baseline, prompt injection | one candidate per subtype |

On held-out (2 seeds, 7 matches per dataset, top 3) there are 115 items and 20 repeat pairs.
B and D are small (6 items each) because only a few scenarios have untriggered twins and
controls produce few candidates. The prompt-injection subtype puts an instruction into a
candidate's free-text statement on a copy of the match; nothing else in the match changes.

## 3. Lineage

Every clean reference investigation was audited for lineage: 1,262 on development and 1,326
on held-out, 5 seeds each. **No broken links on either split.** The links checked are listed
in [evidence-audit.md](evidence-audit.md#lineage). Lineage independently catches tampering
that the auditor might not:

* an alternative whose displayed status differs from its verified status;
* explanation support that differs from the verdict;
* quarantined evidence that reaches the insight, or a quarantine that is not disclosed.

## 4. Fault results

5 seeds per scenario, 85 matches per split:

| Layer | Development | Held-out |
| --- | --- | --- |
| A, assessment faults (14 types) | 4,634 of 4,634 detected | 5,058 of 5,058 detected |
| B, evidence tampering (9 types) | 5,376 of 5,376 detected | **5,648 of 5,649** detected |
| C, insight and narrative tampering (11 types) | 6,187 of 6,187 detected | 6,510 of 6,510 detected |

* **Layer A**: the verifier flags every fault and no fault reaches the reader. The new types
  are:
  * contradiction ignored;
  * non-entailing comparator;
  * score-state misinterpretation;
  * substitution misattribution;
  * unsupported player attribution;
  * wrong-team support.
* **Layer B**: every tampered item is quarantined by provenance replay, on both splits:
  * altered metric value;
  * wrong team;
  * wrong or fabricated event;
  * event outside the window;
  * reversed temporal order;
  * evidence from another candidate, another match or the twin.
* **Layer B, one held-out miss.** It is the only miss in 33,414 injected faults.
  * **What happened.** In S06 (seed 900004, candidate
    `ctx-shift-thornvale-defensive_action_height-44`), evidence from another match replaced
    the result of one request (`ev-03`).
  * **Safety held.**
    * Provenance replay detected the mismatch and quarantined `ev-03`.
    * The reasoner had been misled into "opponent-driven is contradicted". The verifier
      rejected that, because its citation was quarantined, and set opponent-driven to
      *insufficient evidence*.
    * No unsupported claim reached the reader: same strength (hypothesised), hedged text,
      auditor clean, lineage intact.
  * **Ranking did not hold.** In the clean run, the real `ev-03` supported opponent-driven,
    which led. Without it, tactical change led at the same hypothesised strength, with
    opponent-driven listed as "not ruled out". The reader saw a different hedged explanation
    from the one the untampered evidence supported.
  * **Response.** The sequence is detect, quarantine, flag integrity compromised, downgrade or
    withhold, disclose, then conclude on the remaining valid evidence (next subsection). The
    same corruption now yields `evidence_integrity = compromised`, with the integrity warning
    and the label `[verified on remaining evidence, hypothesised]`. This is reproduced in
    `tests/eval/test_integrity_redteam.py`.
  * The miss still counts as a miss: the leading explanation still changed, and the flag does
    not change the counting. **This is not a solved provenance problem.** Evidence recovery is
    future hardening (section 21).

**Evidence integrity flag.** Provenance detection does not guarantee recovery of the original
explanation. So when a quarantine could bear on an insight, the insight says so explicitly.

* **Representation.** `VerificationResult` and `FinalInsight` carry `evidence_integrity`
  (`EvidenceIntegrity`):
  * `intact`: nothing was quarantined.
  * `unaffected`: something was quarantined, but nothing that could bear on the insight was
    lost.
  * `compromised`: anything else.
* **When an insight counts as compromised.** The quarantined item's own content is untrusted,
  so relevance is never judged from its facts. A quarantine is *unaffected* only in two cases:
  * an uncited duplicate whose original survived;
  * an uncited item whose recorded request still replays on this match, from a tool in none of
    the considered explanations' relevant tools (`RELEVANT_TOOLS`).

  Any cited item, any request that no longer replays, and any item from a relevant tool makes
  the insight *compromised*.
* **Strength.** A compromised insight's eligible strength is capped at hypothesised
  (`INTEGRITY_CAP`). The verifier records the cap as a downgrade, so compromised evidence can
  never produce a SUPPORTED, causal or "explained" insight. Existing rules still apply first:
  * citations of quarantined items are rejected;
  * an explanation that loses its support is withheld.
* **Reader.** The narrative adds the line "ANALYSIS: evidence integrity compromised: evidence
  relevant to this insight failed provenance checks and was excluded; the explanation may be
  incomplete." The interpretation label changes from `[verified, …]` to `[verified on
  remaining evidence, …]`. An *unaffected* quarantine keeps the plainer exclusion notice.
* **Audit.** The auditor flags a compromised insight that is not disclosed, a label that does
  not match the integrity state, a compromised explanation above the cap, and a quarantine
  marked intact. Lineage flags an insight whose integrity state differs from the verifier's.

Integrity under layer B tampering, one row per tampered investigation, 5 seeds:

| Measure | Development | Held-out |
| --- | --- | --- |
| Tampered investigations (all quarantined) | 5,376 | 5,649 |
| Relevant quarantines: integrity flagged compromised | 5,376 | 5,649 |
| Irrelevant quarantines: unaffected | 0 | 0 |
| Final insight unchanged from the clean run | 4,138 | 4,212 |
| Downgraded or withheld | 1,234 | 1,426 |
| Different conclusion at the same strength | 4 | 11 |
| Stronger than the clean run | **0** | **0** |
| Clean investigations flagged | 0 of 1,262 | 0 of 1,326 |

How to read this:

* Every layer B fault tampers with a result the investigator requested for an explanation it
  was testing, so every one is relevant. **No irrelevant quarantine occurs in the red team.**
  The *unaffected* path is exercised only by unit tests (a duplicate, and an item from a tool
  bearing on nothing considered).
* **The flag is conservative.** In about three quarters of flagged investigations the
  conclusion was unchanged. The flag says the explanation *may* be incomplete, which is true
  because evidence was lost; it does not claim the conclusion changed.
* **"Different conclusion at the same strength"** covers a different leading explanation or a
  different verdict at equal strength. Only the S06 case meets the stricter Stage 4 "escaped"
  test (`_escaped`: a different leading explanation at hypothesised or above), so it is the
  only one counted as a miss. The other 14 are conclusion changes below that bar, and are
  reported here rather than hidden. All 15 carry the integrity flag.
* Fault detection, clean-run and valid-case numbers are identical to the runs before the flag.
* **Layer C**: every tampered insight is flagged by the auditor or lineage. The "text-only"
  column measures what the auditor catches without template conformance, from the text
  alone:
  * 100% for causal language, certainty, intent, player and opponent attribution, upgraded
    strength, omitted alternatives and unverified citations;
  * forged eliminations 696 of 716;
  * invented facts 737 of 775.

  The first 1-seed run missed 8 forged insights. Template conformance and the lineage
  alternatives check closed them.

## 5. False-rejection results

| Measure | Development | Held-out |
| --- | --- | --- |
| Clean investigations quarantined by provenance | 0 of 1,262 | 0 of 1,326 |
| Clean investigations flagged by the auditor | 0 | 0 |
| Clean lineage broken | 0 | 0 |
| Reference assertions rejected | 0 of 5,531 | 0 of 5,983 |

**Valid-case catalogue.** For every investigated candidate and plausible explanation, the
auditor's own rules decide independently whether a valid explanation exists. If one does, the
reference investigation must reach it, and a hand-built assessment that cites exactly the
required facts (EQ assertions) must be accepted by the verifier:

| Class | Dev total / accepted / hand-built / audit clean | Held-out |
| --- | --- | --- |
| associated observation | 369 / 369 / 369 / 369 | 393 / 393 / 393 / 393 |
| contextual shift | 66 / 66 / 66 / 66 | 70 / 70 / 70 / 70 |
| hypothesised claim | 225 / **224** / 225 / 225 | 232 / **230** / 232 / 232 |
| multi-signal pattern | 880 / 880 / 880 / 880 | 961 / 961 / 961 / 961 |
| personnel change | 7 / 7 / 7 / 7 | 15 / 15 / 15 / 15 |
| score-state response | 109 / 109 / 109 / 109 | 102 / 102 / 102 / 102 |
| sustained change | 43 / 43 / 43 / 43 | 45 / 45 / 45 / 45 |
| untriggered hypothesis | 43 / **42** / 43 / 43 | 45 / **43** / 45 / 45 |

**The shortfalls are all tactical change:** 1 candidate on development and 2 on held-out. Each
is counted twice, once as an untriggered hypothesis and once as a hypothesised claim. All three
were traced individually.

| Case | Stage 3 candidate | Evidence for tactical change | Verified trigger | Hidden truth (evaluator only) |
| --- | --- | --- | --- | --- |
| Dev S03 twin, seed 10004 | `ctx-shift-northmoor-field_tilt-61`: field tilt up 33% → 73%, strong, sustained | `ev-02` level_rank 3; `ev-03` persistence sustained | score-state response (`ev-01`: goal 8 bins earlier, ordinary response) | twin: no planted intervention; scripted goal kept |
| Held-out S05 twin, seed 900003 | `ctx-shift-saltmarsh-field_tilt-83`: field tilt up 48% → 79%, strong, sustained | `ev-03` level_rank 3; `ev-04` persistence sustained | personnel change (`ev-01`: substitution 1 bin earlier; `ev-02`: substitutes in 2.6× their expected share of the metric's events) | twin: no planted intervention; scripted red card kept |
| Held-out S09 twin, seed 900001 | `ctx-shift-redmarsh-defensive_action_height-49`: down 36.7 m → 21.6 m, strong, sustained | `ev-02` level_rank 3; `ev-03` persistence sustained | formation change (`ev-01`: formation change 1 bin earlier) | twin: no planted intervention; scripted formation change kept |

* **Why tactical change is valid on evidence alone.** The rule table (`agents/hypotheses.py`)
  requires two things for tactical change: a Strong change (`level_rank >= 3`) and persistence
  of `sustained`. Each case has both, and no contradiction applies (the change is not
  transient or reversed). A hand-built tactical-only assessment is accepted by the verifier in
  every case, at hypothesised.
* **Where the policy rejects it.** In `Verifier._tactical` (`agents/verification.py`), when a
  non-opponent triggered explanation is verified as supported:
  * if tactical change was itself proposed as supported, it is reduced to partially
    supported;
  * if it was proposed as contradicted, it becomes contradicted, with the note "explained by a
    verified observable trigger". This happened in all three cases.

  The reasoner's cited contradiction was in each case "true but not a material contradiction".
  The contradiction is by definition (`hypotheses.py`: tactical change means *untriggered*),
  not by material evidence.
* **Is the policy correct?** Yes, it is correctly conservative in all three:
  * the only evidence for tactical change is the change itself, which is equally consistent
    with the verified trigger;
  * nothing observable distinguishes an instruction from the response to the trigger;
  * the hidden truth confirms it: these are triggered twins, the planted intervention was
    removed (`counterfactual()` drops every intervention and keeps scripted events), so no
    planted instruction exists to be found.

  The disagreement is between the policy and the catalogue's evidence-only definition of
  "valid", which does not model the residual rule. It is not a verifier error.
* **Decision.** The policy stays unchanged. The three cases are recorded as policy-consistent
  rejections, not false rejections. Re-examine the policy only if a case appears where
  tactical change has distinguishing observable evidence and is still overridden.
* **Wording caveat.** Because the policy marks tactical change as contradicted, the narrative
  can list it under "Weakened by evidence" (development case) when the weakening is by
  definition. This is recorded in section 20 and not changed in Stage 5.

**The S07 lesson holds.** A score-state response after S07's forced goal is an observable
consequence of a score change, not automatically a decoy violation. Decoys are scored in two
steps: first a raw breach of the claim ceiling, then an audit of that breach.

| Decoy | Development matches / raw / audited violation | Held-out |
| --- | --- | --- |
| S07 against the run of play | 5 / 2 / **1** (a hypothesised tactical change) | 5 / 1 / 0 |
| S08 coincidence | 5 / 0 / 0 | 5 / 0 / 0 |

**Known development-split breach (kept on record).**
* **Where:** S07, seed 10002, candidate `ctx-shift-redmarsh-field_tilt-71`. Redmarsh Town's
  field tilt rose 20% → 65%, at 4,210 s, inside the decoy window of 4,090–4,990 s, where the
  claim ceiling is associated.
* **What was claimed:** a tentative tactical change at hypothesised, with hedged text and
  personnel change and late-match decline "not ruled out".
* **Audit:** clean. The claim is supported, hedged and traceable.
* **Why it still counts:** it is not the observable score-state consequence the two-step rule
  excuses. Score-state response was not in this candidate's plausible set. It is an
  above-ceiling explanation inside the decoy window, so it is recorded as a violation.
* **The other raw breach on development** (seed 10004) was an audit-clean score-state
  response, so it is not a violation.
* **Held-out:** one match (seed 900004) had 3 raw breaches, all audit-clean score-state
  responses, so there were **0 audited violations**.
* **Decision:** not tuned away. It remains a known development-only breach.

## 6. LLM factual accuracy (SIMULATED)

All four profiles reach **100% factual correctness on every dataset**. That is by
construction: profiles perturb the reference, and the reference only asserts facts the tools
returned. The metric is wired and would expose a model that invents values. On simulated
data it says nothing about real models.

## 7. Grounding (SIMULATED)

"True and material" over all assertions is 91–100% per dataset. The rest are true but
immaterial assertions that the reference itself makes, for example extra context on
controls. They are accepted but never count as support. The omissive profile requested only
72–82% of the reference's evidence; the verifier did not compensate for this, and none of the
missing evidence was invented.

## 8. Hypothesis quality (SIMULATED)

* The planted explanation was discovered on 1 of 10 matched planted items, by the model and by
  the reference alike. This is the Stage 3 recall ceiling reported in Stage 4, not a model
  property.
* Explanations at hypothesised or above:

| Dataset | Rate |
| --- | --- |
| A planted | 29% |
| B untriggered twins | 33% |
| C triggered twins | 29% |
| D controls | 17% |
| E decoys | 50% |
| F adversarial | 16% |

  Planted and untriggered twins are **not discriminated**, which is consistent with Stage 4.
  The sample is small.

## 9. Challenger effectiveness

Ablation, 10 items per profile, with the challenge round on and off:

| Profile | Coverage higher with challenger | Final differs | Stronger without challenger |
| --- | --- | --- | --- |
| faithful | 10/10 | 1/10 | 0/10 |
| overclaiming | 10/10 | 2/10 | 1/10 |
| omissive | 9/10 (1 lower) | 1/10 | 0/10 |
| unreliable | 10/10 | 1/10 | 0/10 |

The challenger raises coverage of plausible alternatives almost every time. It rarely changes
the conclusion. With the overclaiming profile, one item ended stronger without it, which is
the confirmation-bias guard working. The omissive profile's challenger added an alternative
only 52–92% of the time, by design of the profile.

## 10. Verifier effectiveness

* Red team: all layer A and B faults were flagged by the verifier; section 4 covers the one
  outcome change.
* SIMULATED overclaiming:
  * proposals downgraded on 3–18% per dataset (11% on adversarial);
  * a proposed strength above eligible on 14–27%;
  * final insights **identical to the reference on 92–100%**;
  * **0 final insights flagged by the auditor**;
  * every difference was *weaker* than the reference, never stronger.
* The faithful profile and the reference itself propose above eligible on 1 of 6 controls and
  6 of 49 adversarial items. The deterministic reference is not perfectly calibrated either;
  the verifier contains it.

## 11. Unsupported-claim rate

| Source | Unsupported claims or findings |
| --- | --- |
| Final insights, all profiles, all datasets (auditor) | 0 |
| Final insights, clean red-team runs | 0 of 2,588 |
| Model free text (SIMULATED overclaiming) | 28–50% miscalibrated |
| Model free text (other profiles) | 0% |

Model free text is never shown to a reader, because the narrative is a template over the
verified result. The free-text rate is a property of the model. The final-insight rate is a
property of the system.

## 12. Calibration

* Causal wording is allowed only at SUPPORTED. Certainty and intent wording is never allowed.
* Below SUPPORTED, claims are typed `interpretive` with hedged text. A test checks that no
  hedged text contains a causal term.
* The auditor checks the strength label, the claim type and the lexicon on every final
  insight. Clean runs: 0 findings.
* Limits:
  * the lexicon is literal, so paraphrases outside it pass;
  * template conformance is the real guard for the narrative;
  * there are no confidence scores to calibrate, by design (ADR-0010).

## 13. Insufficient-evidence rate

Reference, held-out:

| Dataset | Insufficient evidence |
| --- | --- |
| A planted | 71% |
| B untriggered twins | 67% |
| C triggered twins | 71% |
| D controls | 83% |
| E decoys | 50% |
| F adversarial | 84% |

The omissive profile reaches 92% on adversarial items, because missing evidence leaves
explanations unresolved. The engine stays conservative; this is the price of the ladder and is
unchanged from Stage 4.

## 14. Repeatability (SIMULATED)

20 pairs per profile, comparing two runs of the same item. Variation is classified as:

* identical;
* wording: free text only;
* procedural: different evidence requests, same conclusion;
* material: a different selection, strength, final claim or narrative.

| Profile | Identical | Wording | Procedural | Material |
| --- | --- | --- | --- | --- |
| faithful | 1 | 19 | 0 | 0 |
| overclaiming | 0 | 15 | 0 | 5 |
| omissive | 0 | 4 | 6 | 10 |
| unreliable | 0 | 15 | 0 | 5 |

The deterministic reference is byte-identical across runs. The classifier separates harmless
rewording from changed conclusions. Material variation comes from profiles that change what
they propose or request.

## 15. Latency

| Path | Per model call | Per investigation (p50 / p95) |
| --- | --- | --- |
| Reference, plus SIMULATED profiles | about 0.3 ms | about 41–62 ms / 78–122 ms |
| Live model | not measured | not measured |

A live model would add network and generation time, roughly 4 calls per investigation, plus
retries. The orchestrator's deadline and the one-retry policy bound this.

## 16. Failure rate

The unreliable profile simulates outages and malformed JSON:

| Dataset | Failed calls |
| --- | --- |
| A planted | 10 of 95 |
| C triggered twins | 11 of 107 |
| F adversarial | 6 of 229 |

* Retries recovered most failures.
* 4 investigations ended `unavailable` (1 in A, 2 in C, 1 in F), each with a plain
  "explanation unavailable" narrative and no fabricated content.
* In all cases: 0 auditor findings, 0 broken lineage.

## 17. Cost

* SIMULATED profiles cost nothing and report no tokens.
* The live adapter records the token usage the endpoint reports, and the harness converts it
  with `--price-in` and `--price-out`. Nothing was spent in Stage 5.
* A live run at the planned size (about 40 matches, top 3, 3 repeats on 10 items) would make
  about 600 model calls.

## 18. Security

* **Fixed:**
  * the HTTP adapter now refuses redirects, so a redirect cannot carry the API key to another
    host;
  * responses are capped at `MAX_RESPONSE_BYTES`.
* **Already in place, now tested:**
  * HTTPS only;
  * credentials only from environment variables;
  * no secrets in traces, checked by a canary key;
  * model free text never reaches the narrative.
* **New architecture tests:**
  * a secret scan over the repository, with an explicit allowlist for marked test canaries;
  * `.env` must be ignored by git;
  * no dynamic code execution (`eval`, `exec`, `compile`, `__import__`, `pickle`,
    `subprocess`, `importlib`, `marshal`, `os.system`) in engine code;
  * model payload keys must be schema, tool, fact or hypothesis names. This is checked by test
    over payloads built for synthetic scenarios, not enforced at runtime by the adapter.
* **Live configuration check at closure:** no `MATCHEYES_LLM_*`, OpenAI, Azure OpenAI or Ollama
  variables were found at process, user or machine scope. No `.env` file exists; only
  `.env.example`, which has no values. No local model server was listening on common ports
  (11434, 1234, 8000, 8080, 5000, 5001, 4891).
* **Prompt injection:**
  * in 7 adversarial items per profile, an instruction placed in observable free text was
    never followed;
  * the narrative cannot carry it, because model text is never rendered.

## 19. Hidden-truth isolation

* **Static.** Existing layer-import and vocabulary scans now also cover `orchestration/audit.py`
  and `orchestration/lineage.py`. A test checks model payload keys against a whitelist and
  forbids answer-key vocabulary in their values:
  * scenario IDs;
  * planted, twin and decoy labels;
  * intervention names.
* **Runtime.** A subprocess probe runs a full investigation and asserts that no hidden package
  (`matcheyes_synth`, `matcheyes_eval`) is loaded.
* **The LLM harness is blinded.** Every payload of every call was scanned for forbidden
  vocabulary in every profile: **no leaks**. The model is never told which dataset an item
  belongs to.
* **Evaluator knowledge stays in the evaluator.** The red team and harness know scenarios,
  twins and decoys, and use them only to choose donors and group results, after the
  investigation.

## 20. Limitations

* **The live LLM was not evaluated.** Every LLM-path number is SIMULATED. Real models may fail
  in ways the profiles do not model, such as plausible but wrong evidence choices or subtle
  paraphrased overclaiming.
* **Simulated datasets are small**: 6 items in B and in D on held-out.
* **Synthetic data only.** All results are on the project's own generator.
* **The lexicon is literal.**
* **The auditor and the verifier share the rule table.** The auditor is independently written,
  but a wrong rule would be wrong in both.
* **Residual tactical policy.** It overrides an evidence-valid tactical reading in 3 traced
  cases. In all three it was correct, but the valid-case catalogue does not model the policy.
  When the policy contradicts tactical change, the narrative lists it under "Weakened by
  evidence", although the weakening is by definition, not by material evidence.
* **Discovery is bounded by Stage 3 recall** (1 of 10 planted items), and planted and twin
  matches are not discriminated.
* **The held-out evidence-substitution miss** (section 4) is flagged and disclosed, not
  prevented. Safety held; ranking did not.
* **The integrity flag is conservative and coarse.** It is set whenever relevant evidence was
  lost, including the roughly three quarters of tampered investigations whose conclusion did
  not change. It says "may be incomplete", not which explanation would have led.
* **Relevance rests on the recorded tool of an uncited item.** An item whose recorded request
  still replays with an irrelevant tool is treated as *unaffected*. If an attacker replaced a
  relevant result with a genuine, replayable result from an irrelevant tool under the same
  evidence ID, the insight would be marked *unaffected*, not *compromised*. It would still be
  quarantined and show the plain exclusion notice. The red team does not exercise this case.
* **A whole-item swap for another genuine result of this match** (one that replays exactly)
  is not detected by provenance at all. Provenance proves an item is a genuine tool result,
  not that it answers the request the investigator made.
* **One known development breach of the S07 decoy** (section 5).
* **The payload-key whitelist is a test,** not a runtime filter.
* **`.env.example` is stale.** It describes Foundry variables, while the adapter reads
  `MATCHEYES_LLM_*`.

## 21. Next steps

1. **Live LLM evaluation.** Run it at the planned size (about 600 calls) once credentials are
   supplied through the environment, and compare it with the simulated baselines. It does not
   block the deterministic gate.
2. **Future hardening: evidence recovery after quarantine.** After a quarantine, the
   investigation could re-issue the quarantined request, or offer the investigator a bounded
   re-fetch turn, so the conclusion rests on the true result rather than a reduced pool. This
   would address the ranking effect of the held-out miss. It needs its own design and red
   team, because the recovery path is itself an attack surface: repeated tampering, budget
   exhaustion, and evidence the investigator did not choose. Not implemented in Stage 5. Until
   then, the integrity flag is the safeguard: the reader is warned and the claim is capped. A
   related hardening is binding each evidence item to the request the investigator made, so
   that a genuine but different result cannot pass under the same ID, and so that relevance
   no longer depends on the item's recorded tool.
3. **Residual tactical policy.** Keep it. Revisit only if a case appears where tactical change
   has distinguishing observable evidence and is still overridden. Consider wording the
   policy's elimination as "set aside: an observable trigger is verified" rather than
   "weakened by evidence".
4. **S07 development breach.** Keep it on record. Re-check it when Stage 3 or the case-file
   screen changes.
5. **Stage 3 recall** bounds every downstream discovery metric.
6. **Stage 6** (personalization) can consume `FinalInsight` as is. Lineage and audit results
   are ready to surface as "what evidence supports this?".

## Stage 5 conclusion

**What Stage 5 proves** (on synthetic data, deterministic reference path):

* Final insights are traceable link by link to source events: 0 broken links in 2,588 clean
  investigations.
* Under 33,414 injected faults across assessments, evidence and output, no unsupported or
  above-eligible claim reached a reader without being flagged.
  * 33,413 faults were fully contained.
  * The remaining one changed the ranking of hedged explanations without overclaiming. It is
    now explicitly flagged as compromised evidence integrity, capped at hypothesised and
    disclosed as possibly incomplete.
* **The system detects evidence corruption, quarantines affected evidence, and explicitly
  flags when the corruption may have affected the resulting explanation.** Compromised
  evidence produced no stronger claim in 11,025 tampered investigations.
* The added defences cost nothing on clean data: 0 false quarantines, 0 auditor findings and
  0 of 11,514 reference assertions rejected.
* The three policy overrides were traced and are correct.
* The LLM evaluation harness works end to end, with blinding, pairing, repeatability and
  ablation. The modelled failure modes are contained on simulated profiles.

**What Stage 5 does not prove:**

* anything about a real language model, because the live evaluation was not run;
* that provenance is solved: tampering can still change which hedged explanation leads. The
  flag makes this visible but does not recover the original explanation;
* that decoys are never breached: there is one known development breach;
* that the engine discovers planted causes: recall is bounded by Stage 3, and planted and twin
  matches are not discriminated;
* anything about real football data: all data is synthetic.
