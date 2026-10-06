# Evaluation

Status: **deterministic evidence evaluation (Stages 2–3), investigation evaluation (Stage 4),
verification hardening (Stage 5), personalization (Stage 6) and the insight lifecycle
(Stage 7)**. "It looked good in the demo"
is not evidence.

| Layer | Method | Introduced |
| --- | --- | --- |
| Architecture | Import-boundary tests; observable/hidden separation (source, runtime, wheel, schema, ingestion) | Stages 0–1 |
| Ingestion | Schema validation + match-level invariants on fixtures and every generated match | Stage 1 |
| Generator | Determinism (hash), invariants, realism bands, planted effect size above seed noise | Stage 1b |
| Analytics | Deterministic unit tests with hand-computed expectations; mechanism-level scoring against planted truth, twins, controls and decoys ([report](stage2-evaluation.md)) | Stage 2 |
| Contextual evidence | The same planted-truth protocol, reported side by side with the frozen Stage 2 baseline ([report](stage3-evaluation.md)) | Stage 3 |
| Agents and verification | Contract, tool, retry and failure tests; hidden-truth isolation tests; planted / twin (by trigger type) / control / decoy claim rates; unsupported-claim and insufficient-evidence rates; verifier fault injection; determinism ([report](stage4-evaluation.md)) | Stage 4 |
| Verification hardening | Evidence lineage; independent claim and narrative audit; three-layer red team (assessment, evidence tampering, insight tampering); false-rejection and valid-case tests; audited decoys; blinded LLM-path harness with repeatability and ablation ([report](stage5-evaluation.md)) | Stage 5 |
| Personalization | Construction invariants and an independent view audit on every view; same mandatory core across Fan / Broadcaster / Analyst; preference safety; feed placement; presentation red team (English only; no user study) ([report](stage6-evaluation.md)) | Stage 6 |
| Insight lifecycle | Replay under reorder, shuffle, duplicates, conflicts, gaps, late fill, failure and tampering; byte-identical determinism; independent lifecycle audit with reproduction; lifecycle red team; storylines against planted truth ([report](stage7-evaluation.md)) | Stage 7 |
| Narrative quality | Rubric-based evaluation (Foundry evaluators where verified) | Stages 5–10 |

## Planted-truth metrics

`matcheyes_eval` joins engine output with the answer key (it is the only code allowed to).
The engine never sees the key ([ADR-0005](decisions/0005-observable-and-hidden-worlds.md)).

| Metric | Definition |
| --- | --- |
| Detection recall | Share of expected insights detected inside their window plus the allowed latency |
| Detection latency | Time from the planted cause's start to the engine's first matching insight |
| Cause attribution accuracy | Share of detected insights whose primary cause matches the planted intervention |
| Cause ranking | For multi-cause scenarios (S10), primary ranked above secondaries |
| Mechanism coverage | Share of expected *scored* mechanism signals cited as evidence (supporting mechanisms are never scored) |
| Twin background rate | The same scoring on the counterfactual twin (same seed, no interventions): how often the evidence appears anyway |
| Supported-claim precision | Share of `supported` claims that match a planted cause or a recorded background effect |
| Decoy violation rate | Claims stronger than a decoy's cap, for that team and window |
| Control false-positive rate | `supported` claims in S01 that match nothing in the answer key |

Reporting rules: many seeds per scenario (target 20), held-out test seeds only, full
distributions with intervals, and failures shown alongside successes. See
[synthetic-data.md](synthetic-data.md#evaluation-hygiene-so-ground-truth-measures-rather-than-flatters-matcheyes).

## Development versus held-out protocol

* Development seeds (from 10000) are the only seeds used for design and tuning decisions. The
  tuning code refuses held-out seeds.
* Held-out seeds (from 900000) are evaluated once per stage, with a frozen configuration. Their
  results must not feed back into implementation decisions.
* Each evaluated stage reports development and held-out results side by side, including where
  held-out results are worse.

## Planted versus twin

Each planted scenario has a same-seed counterfactual twin with every intervention removed. The
twin keeps scripted goals, red cards and substitutions, so it still contains real game-state
swings and natural momentum changes. The twin rate is therefore a **background rate**: how
often the same evidence appears without the planted cause. It is not a pure false-positive
rate. The headline quantity is the **planted-versus-twin gap**: does the engine make planted
changes more distinguishable from ordinary match variation?

## Controls and decoys

* **Control (S01):** no interventions. We report the density of candidate moments per match.
  Many reflect real game-state swings; density matters because a crowded shortlist is not
  useful.
* **Decoys (S07, S08):** windows where something salient happens without a planted cause. We
  report how often a candidate moment for the decoy team falls in the decoy window, and
  whether any claim exceeds the decoy's ceiling. The required number of ceiling violations is
  zero.

## Stage 2 frozen baseline

Stage 2 (analytics 0.1.0, ADR-0008) is frozen. Its held-out results are the reference that
Stage 3 is compared against:

| Measure | Held-out |
| --- | --- |
| Evidence recall, planted / twin | 34% / 29% |
| Moment recall, planted / twin | 32% / 24% |
| Mean scored-mechanism coverage | 18% |
| High-regain and pressing-success evidence, planted / twin | 15% / 0% |
| Shift moments per control match | about 5.7 |
| S07 / S08 decoy-window moment rate | 50% / 35% |
| Claims above `associated` | 0 |

## Stage 3 objectives

Stage 3 is judged against the Stage 2 baseline on the same seeds, development and held-out:

1. Widen the planted-versus-twin evidence gap.
2. Reduce unnecessary moment density in controls.
3. Reduce decoy-window hits where possible.
4. Keep zero claims above `associated`.
5. Keep every evidence object traceable to observable event IDs.
6. Avoid materially increasing background (twin) evidence.

Success does not mean every number improves. Trade-offs are reported as trade-offs.

## Stage 4 objectives and results

Stage 4 is judged on integrity first, then discrimination
([stage4-evaluation.md](stage4-evaluation.md)). The table gives held-out results.

| Objective | Held-out |
| --- | --- |
| Zero unsupported, untraceable, temporally invalid or above-ceiling final claims | 0 of 6,849 claims |
| Verifier catches injected corruptions | 3,108 of 3,108 (8 corruption types) |
| Deterministic investigations | 0 of 10 re-runs differ |
| Insufficient-evidence rate | 71.5% |
| Untriggered planted vs twin, any explanation at hypothesised+ | 10% vs 8% (no reliable gap) |
| Hypothesised+ claims per control match / supported | 2.5 / 0 |
| S07 / S08 decoy ceiling exceeded | 30% / 10% (mostly post-goal score-state responses) |

Twins are reported by trigger type: only untriggered twins give a clean false-attribution
comparison, because observable-trigger twins keep the trigger and its background response.

## Stage 6 objectives and results

Stage 6 is judged on truthfulness first ([stage6-evaluation.md](stage6-evaluation.md)). It
uses no hidden truth: every measure compares a view with the verified insight it wraps. Only
the invariants have a target (zero); everything else is reported as measured. Held-out, 2
seeds, 1,686 insights (562 clean, 1,124 compromised by Stage 5 tampering), 40,464 views:

| Objective | Held-out |
| --- | --- |
| Views failing construction / fingerprint mismatches / flagged by the view audit | 0 / 0 / 0 |
| Insights changed by personalizing | 0 |
| Mandatory core differs across audiences | 0 of 1,686 |
| Preference changes a section (other than the involvement line) or a feed placement | 0 |
| Compromised insights hidden or moved out of the primary feed | 0 of 1,124 per audience |
| Presentation faults caught by `audit_view` | 88,565 of 88,565 (27 types) |
| Audience checklist coverage (fan / broadcaster / analyst) | 100% / 100% / 100% |

No user study has been run: usefulness to real audiences is unmeasured.

## Stage 7 objectives and results

Stage 7 is judged on lifecycle properties under realistic delivery
([stage7-evaluation.md](stage7-evaluation.md)): every match is replayed minute by minute in
sequence order, then reordered, shuffled, duplicated, with conflicts, a gap and a late fill, a
failed snapshot, tampered evidence and injected lifecycle faults. No targets were set. Held-out,
1 seed per scenario, 17 matches, 1,696 snapshots, 242 storylines, 593 revisions:

| Objective | Held-out |
| --- | --- |
| Canonical state independent of arrival order (bounded reorder, shuffle) | 17/17, 17/17 |
| Replay byte-identical (fresh / repeated) | 2/2 / 17/17 |
| Duplicates no-ops; conflicts, reused sequences and other-match events rejected | 17/17 each |
| Gap: `DATA_INCOMPLETE`, no snapshot beyond the watermark; late fill equals reference | 17/17 |
| No cited event outside its revision's snapshot; history unchanged by later events | 17/17 |
| Append-only history; independent lifecycle audit clean (incl. reproduction) | 17/17 |
| Pipeline failure: `FAILED`, feed `UNAVAILABLE`, no fallback | 17/17 |
| Stale views flagged / compromised views warned | 923/923 / 714/714 |
| Lifecycle faults caught by `audit_lifecycle` | 197 of 197 (14 types) |
| Current revisions at full time equal the batch investigation | 17/17 |
| Planted insights followed by a storyline; median first-detection latency | 3 of 7; 1,320 s |
| Produced insights lost by the lifecycle (attribution) | 0 of 9,328; all 4 misses are upstream (no matching Stage 2 shift) |

The latency is set by detection (confirmation after an onset), not by transport. Stage 7 is
deterministic local recomputation, not production streaming.

## Test categories

- **Unit**: fast, deterministic, no network. Always run in CI.
- **Integration** (`@pytest.mark.integration`): real model / Azure calls. Opt-in.
- **Evaluation**: scenario suites over generated matches, scored against the answer key.
