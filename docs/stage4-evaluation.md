# Stage 4 evaluation

Investigation 0.1.0 with the deterministic reference reasoner (`rule-based-reference-v1`).
There were 20 seeds per scenario, with 9 planted scenarios and 7 twins. Design decisions used
development seeds only. The held-out split was run **once**, on frozen code, after every
design decision.

Reproduce:

```bash
uv run python -m matcheyes_eval stage4 --split development --seeds 20 --fault-seeds 5
uv run python -m matcheyes_eval stage4 --split held-out    --seeds 20 --fault-seeds 5
```

## Method

* Every Stage 3 candidate at Weak or above is investigated. The answer key is joined only after
  all investigations of a match have finished, in `matcheyes_eval/stage4.py`.
* `EXPECTED_EXPLANATIONS` maps each planted intervention to the explanation a correct
  investigation should reach. This is evaluator knowledge; the system never sees it. The
  mapping is:
  * press surge → tactical change;
  * deep block → score-state response;
  * fatigue decay → late-match decline;
  * red card → numerical or formation change;
  * impact substitution → personnel change;
  * formation shift → formation change.
* An insight is scored only on investigations whose candidate matches the planted team, metric
  mechanism and window, using the Stage 3 matcher. The measures are:
  * investigated;
  * considered (the expected explanation was assessed);
  * **discovered** (it leads, at hypothesised or above);
  * supported;
  * any explanation (any explanation at hypothesised or above).
* **Twins are grouped by trigger type**, because the twin means something different in each
  group:
  * untriggered (manager instruction): the twin has no cause, so the same claim there is a
    false attribution;
  * observable trigger: the twin keeps the goal, card or substitution and the generator's
    background response to it, so the same explanation is not wrong there;
  * fatigue: present in every match.
* **Integrity counters** should all be zero:
  * a final claim whose evidence IDs are not in the pool, or a causal claim not verified as
    supported;
  * a final strength above the eligible strength;
  * a triggered explanation that fails the temporal gate;
  * a claim above SUPPORTED;
  * event IDs that are not in the match.
* **Determinism**: the first 10 matches are re-investigated and the final insights compared
  byte for byte.
* **Verifier fault injection**: a `FaultyReasoner` applies one corruption to every reference
  assessment it can affect, on 5 seeds of each planted scenario. A corruption is *caught* if
  the final insight is neither stronger nor attributed to a different explanation than the
  clean run.

## Results

### Integrity, verification and determinism

| Measure | Development | Held-out |
| --- | --- | --- |
| Investigations (candidates at Weak or above) | 5,055 | 5,329 |
| **Unsupported-claim rate** (final claims) | 0 of 6,533 (0.0%) | 0 of 6,849 (0.0%) |
| Untraceable / temporal violations / above ceiling | 0 / 0 / 0 | 0 / 0 / 0 |
| **Insufficient-evidence rate** (investigations) | 70.8% | 71.5% |
| Proposed statuses downgraded by the verifier | 594 of 18,877 (3.1%) | 685 of 20,025 (3.4%) |
| Proposed strength reduced | 6.5% | 6.8% |
| Cited assertions rejected (reference reasoner) | 0 of 22,772 | 0 of 24,066 |
| Determinism (re-runs differing) | 0 of 10 | 0 of 10 |

### Verifier fault injection (caught / applied)

| Corruption | Development | Held-out |
| --- | --- | --- |
| Fabricated evidence ID | 253 / 253 | 292 / 292 |
| False assertion | 253 / 253 | 292 / 292 |
| Unsupported promotion | 738 / 738 | 773 / 773 |
| Inflated strength | 253 / 253 | 292 / 292 |
| Dropped alternatives | 253 / 253 | 292 / 292 |
| Hidden contradiction | 676 / 676 | 714 / 714 |
| Immaterial support | 145 / 145 | 161 / 161 |
| Forged eliminations | 253 / 253 | 292 / 292 |

All eight corruptions were caught in 100% of cases on both splits. Every one of the 5,932
corruptions (2,824 development, 3,108 held-out) was kept out of the final insight.

### Planted versus twin (discovered = expected explanation leads at hypothesised or above)

| Insight | Twin type | Dev discovered, planted / twin | Held-out discovered, planted / twin | Held-out any explanation, planted / twin |
| --- | --- | --- | --- | --- |
| S02 press surge | untriggered | 5% / 5% | 0% / 5% | 10% / 10% |
| S10 comeback (tactical) | untriggered | 10% / 0% | 10% / 0% | 10% / 5% |
| S03 deep block (score state) | observable trigger | 10% / 15% | 10% / 5% | 15% / 10% |
| S05 red card | observable trigger | 10% / 10% | 15% / 0% | 15% / 0% |
| S06 impact substitution | observable trigger | 0% / 0% | 0% / 0% | 0% / 0% |
| S09 formation shift | observable trigger | 30% / 10% | 10% / 10% | 15% / 20% |
| S04 fatigue decay | fatigue | 0% / 0% | 0% / 0% | 0% / 0% |

| Totals | Development | Held-out |
| --- | --- | --- |
| Untriggered, discovered | 8% (3/40) vs 2% (1/40) | 5% (2/40) vs 2% (1/40) |
| Untriggered, any explanation | 20% (8/40) vs 2% (1/40) | 10% (4/40) vs 8% (3/40) |
| Observable trigger, discovered | 12% (10/80) vs 9% (7/80) | 9% (7/80) vs 4% (3/80) |
| Fatigue, discovered | 0% vs 0% | 0% vs 0% |
| Supported (any group) | 0% planted, 1 twin | 0% vs 0% |

### Controls and claim density

| Hypothesised-or-above claims per match | Development | Held-out |
| --- | --- | --- |
| Control (S01) | 1.90 | 2.50 |
| Planted | 2.46 | 2.54 |
| Twin | 2.38 | 2.36 |
| Supported per match (control / planted / twin) | 0.00 / 0.03 / 0.04 | 0.00 / 0.01 / 0.00 |

The held-out control claims break down as follows (20 matches):

* score-state response: 20;
* opponent-driven: 15;
* tactical change: 9;
* numerical change: 4;
* late-match decline: 2.

The generator applies background game-state responses in every match, so a score-state claim
after a goal in a control is consistent with how the data was generated. Tactical and
opponent-driven claims there have no planted cause.

### Decoys (share of matches with a claim above the decoy ceiling, team and window)

| Decoy | Development | Held-out | Held-out claims above the ceiling |
| --- | --- | --- | --- |
| S07 against the run of play (ceiling: associated) | 30% | 30% | score-state response 9, personnel change 1 |
| S08 coincidence (ceiling: associated) | 5% | 10% | score-state response 1, tactical change 1 |
| S08 substitution attributed (personnel at hypothesised+) | 0% | 0% | |

## Interpretation

1. **The verification layer works as designed.** It allowed zero unsupported, untraceable,
   temporally invalid or above-ceiling claims across about 13,400 final claims. It caught every
   injected corruption, and investigations are fully deterministic. That holds on both
   splits.
2. **The system is calibrated to say "insufficient evidence".** About 71% of investigations
   end there. SUPPORTED (explained) is almost never reached: 0–3 per 100 matches, none in
   controls. It requires a Strong change with every plausible alternative contradicted by
   evidence, and Stage 3 rarely provides that.
3. **No reliable planted-versus-twin discrimination.** On development seeds, untriggered
   planted changes gained an explanation 20% of the time against 2% for twins. This did not
   replicate on held-out seeds (10% against 8%). Observable-trigger discovery is 9% against 4%
   held-out, but twins keep their triggers there, so this is not a false-positive measure.
   Samples are small (40–80 per group), and the intervals overlap.
4. **Stage 4 inherits Stage 3's recall ceiling.** The expected explanation is *considered* in
   only 15–35% of planted insights, because only that share of planted changes reaches a Weak
   candidate in the right window at all. Investigation cannot explain a change Stage 3 does not
   surface.
5. **Hypothesised claims are frequent in controls (2.5 per match held-out).** Most are
   score-state responses, which are consistent with the generator's background behaviour. The
   rest are opponent-driven or tactical readings of Strong changes, which controls also
   produce. HYPOTHESISED means "best remaining reading, alternatives open", and the narrative
   says so. It is still not a discriminating signal.
6. **The S07 decoy is exceeded in 30% of matches**, almost entirely by score-state responses
   after the forced goal. The generator's background game-state dynamics do produce such
   responses, so these may be correct explanations of the *post-goal* change, not false causal
   claims about the goal. The decoy ceiling was written before any stage offered explanations.
   This needs a decision (see ADR-0010).
7. **Late-match decline and personnel change are almost never discovered.** Late-match decline
   needs a Strong decline-type change late on, and personnel change needs substitute
   involvement at twice the expected share. Planted effects rarely meet either bar.

## Limitations

* The reference reasoner is a fixed policy. An LLM investigator was built and tested with a
  fake transport, but has not been evaluated: there are no credentials, and its outputs are
  non-deterministic. The verifier bounds what any model can claim, not how well it
  investigates.
* The rules were set on development data, partly to bring control claim density down. Held-out
  control density (2.5) is higher than development (1.9).
* Twins of observable-trigger scenarios measure background rates, not false positives. Only
  the untriggered group gives a clean false-attribution comparison, and it has n = 40 per arm.
* Strong, sustained changes occur in controls, so "Strong" is an imperfect stand-in for "beyond
  natural variation".
