# Stage 7 evaluation: the snapshot-anchored insight lifecycle

Code: `src/matcheyes_eval/stage7.py` and `src/matcheyes_eval/transport.py`. Method:
[living-insights.md](living-insights.md). Decision:
[ADR-0013](decisions/0013-snapshot-anchored-insight-lifecycle.md).

```bash
uv run python -m matcheyes_eval stage7 --split development --seeds 1 --fresh-replays 2
uv run python -m matcheyes_eval stage7 --split held-out --seeds 1 --fresh-replays 2
```

## What is measured

Each match is replayed through `LifecycleEngine` event by event, in sequence order. This is the
**reference**. It is then replayed under transport perturbations, failures, tampering and
injected lifecycle faults. The lifecycle is judged on its own properties: ordering independence,
replay determinism, idempotency, gap handling, no future leakage, append-only history, identity,
independent audit, stale-view detection, failure semantics, integrity propagation and security.
No targets were set in advance, and everything is reported as measured.

Hidden truth is used only in `matcheyes_eval`, and only for the identity-versus-planted-truth
section and the leakage scan. The engine never sees it.

| # | Property | How |
| --- | --- | --- |
| 1 | ordering | bounded reorder (blocks of 25, seeded) and a full shuffle give the reference canonical state |
| 2 | replay | a fresh uncached replay and a repeated replay are byte-identical to the reference |
| 3 | duplicates | every 7th event re-delivered later: counted as no-ops, state unchanged |
| 4 | conflicts | a changed payload under an existing event ID is rejected, state unchanged |
| 5 | gap | a withheld event: `DATA_INCOMPLETE`, no snapshot beyond the watermark, state equals the prefix replay |
| 6 | late fill | the withheld event arrives last: the watermark advances and the state equals the reference |
| 7 | no leakage | history up to the gap is unchanged by later events; every cited event is in its revision's snapshot |
| 8 | identity | storylines against planted truth: fragmentation and purity (planted variants) |
| 9 | re-anchoring | storylines keep their ID; consecutive anchors within the suppression distance (audit) |
| 10 | opposite | opposite-direction withdrawals and linked successors correspond (audit) |
| 11 | reinstatement | reinstated revisions keep the storyline ID |
| 12 | append-only | every intermediate state's history is a prefix of every later one |
| 13 | own snapshot | the independent lifecycle audit, including reproduction of every snapshot |
| 14 | stale views | a view of a superseded revision is flagged stale; a view of a current one is accepted |
| 15 | Stage 6 | audience feeds show only current revisions; nothing is withheld |
| 16 | unavailable | a pipeline failure on one snapshot: `FAILED`, feed `UNAVAILABLE`, nothing anchored there, deterministic |
| 17 | integrity | evidence tampered on the latest snapshot: compromised current revisions, noticed and warned |
| 18 | corrections | a late changed payload and a reused sequence are rejected, state unchanged |
| 19 | security | no planted-truth vocabulary or scenario ID in any canonical state; an event of another match is rejected |

A further check compares the current revisions at full time with the batch Stage 4
investigation of the complete match.

Snapshot evaluations are cached by snapshot ID within a match. Snapshot IDs are content
addresses, so a cached evaluation is exactly the pipeline's output for that snapshot. Fresh,
uncached replays are used where determinism itself is measured.

## Data

One seed per scenario on each split gives 17 matches: each scenario's planted match plus, for
scenarios with interventions, its same-seed counterfactual twin. Every match is replayed in
full, one snapshot per closed minute.

| | Development | Held-out |
| --- | --- | --- |
| Matches | 17 | 17 |
| Snapshots (all evaluated; none invalid or failed in the reference) | 1,669 | 1,696 |
| Storylines | 286 | 242 |
| Revisions | 701 | 593 |
| Revisions with Stage 5 audit findings | 0 | 0 |
| Withdrawals: not detected / opposite direction | 22 / 2 | 5 / 2 |
| Reinstatements | 9 | 3 |
| Fresh (uncached) determinism replays | 2 | 2 |

Change kinds recorded, development / held-out: created 286 / 242, re-anchored 236 / 180,
evidence changed 387 / 342, explanation changed 119 / 112, verdict changed 51 / 52, strength
changed 45 / 39, integrity changed 0 / 0 (in the reference), withdrawn 24 / 7, reinstated 9 / 3.

## Results

Every lifecycle property held on every match of both splits.

| Property | Development | Held-out |
| --- | --- | --- |
| 1 ordering: bounded reorder / shuffle | 17/17 / 17/17 | 17/17 / 17/17 |
| 2 replay: fresh / repeated byte-identical | 2/2 / 17/17 | 2/2 / 17/17 |
| 3 duplicates: no-ops and state unchanged | 17/17 | 17/17 |
| 4 conflicting duplicate rejected, state unchanged | 17/17 | 17/17 |
| 5 gap: `DATA_INCOMPLETE`, no snapshot beyond watermark, equals prefix replay | 17/17 | 17/17 |
| 6 late fill equals reference | 17/17 | 17/17 |
| 7 no leakage: cited events / history unchanged by later events | 17/17 / 17/17 | 17/17 / 17/17 |
| 9 re-anchoring keeps the ID | 17/17 | 17/17 |
| 10 opposite withdrawals and links correspond | 17/17 | 17/17 |
| 11 reinstatement keeps the ID | 17/17 | 17/17 |
| 12 append-only history | 17/17 | 17/17 |
| 13 lifecycle audit clean (incl. reproduction) | 17/17 | 17/17 |
| 14 stale views flagged / current views accepted | 1,072/1,072 / 547/547 | 923/923 / 480/480 |
| 15 Stage 6 views of current revisions only | 102/102 feeds; 1,626 views, 0 withheld | 102/102 feeds; 1,428 views, 0 withheld |
| 16 failure: `FAILED`, `UNAVAILABLE`, nothing anchored, audit clean, deterministic | 17/17 each | 17/17 each |
| 17 integrity: compromised current revisions noticed / views warned | 271/271 / 813/813 | 238/238 / 714/714 |
| 18 corrections: changed payload / reused sequence rejected | 17/17 / 17/17 | 17/17 / 17/17 |
| 19 security: other match rejected / no truth vocabulary | 17/17 / 17/17 | 17/17 / 17/17 |
| Full time: current revisions equal batch Stage 4 | 17/17 | 17/17 |

The stale-view counts cover views of both the reference state and the tampered-run state.

### Lifecycle red team

Faults are injected into genuine states with `model_copy`, which skips validation, and each must
be caught by the expected audit check. A fault is injected only where it applies: for example,
`forged_reopening` needs a withdrawn storyline and `unlinked_opposite` needs an
opposite-direction link.

| Fault | Development (injected / caught / by expected check) | Held-out |
| --- | --- | --- |
| `stale_current` | 17 / 17 / 17 | 17 / 17 / 17 |
| `forged_storyline_id` | 17 / 17 / 17 | 17 / 17 / 17 |
| `rewritten_history` | 17 / 17 / 17 | 17 / 17 / 17 |
| `rewritten_history_rechained` | 17 / 17 / 17 | 17 / 17 / 17 |
| `reordered_numbering` | 17 / 17 / 17 | 17 / 17 / 17 |
| `removed_storyline` | 17 / 17 / 17 | 17 / 17 / 17 |
| `integrity_upgraded` | 17 / 17 / 17 | 17 / 17 / 17 |
| `future_evidence` | 17 / 17 / 17 | 17 / 17 / 17 |
| `forged_reopening` | 8 / 8 / 8 | 4 / 4 / 4 |
| `silent_withdrawal_removal` | 8 / 8 / 8 | 4 / 4 / 4 |
| `wrong_snapshot` | 17 / 17 / 17 | 17 / 17 / 17 |
| `forged_link` | 17 / 17 / 17 | 17 / 17 / 17 |
| `unlinked_opposite` | 1 / 1 / 1 | 2 / 2 / 2 |
| `forged_change_kinds` | 17 / 17 / 17 | 17 / 17 / 17 |
| **Total** | **204 / 204** | **197 / 197** |

### Storylines against planted truth (property 8)

This measures the whole engine over time, not only the lifecycle: a storyline can match a
planted insight only if Stages 2–4 detect it.

| | Development | Held-out |
| --- | --- | --- |
| Expected insights with a metric mechanism (planted variants) | 7 | 7 |
| Detected by at least one storyline | 3 | 3 |
| Storylines per detected insight | 1 (twice), 2 (once) | 1 (twice), 2 (once) |
| Storylines matching no expected insight / exactly one | 167 / 4 | 136 / 4 |
| First-detection latency after the planted window start, median / max | 1,080 s / 1,200 s | 1,320 s / 1,500 s |

These numbers come from 3 detected insights per split and are not statistically meaningful. The
attribution below shows that every miss is upstream of Stage 7.

* **Fragmentation.** On each split, one planted insight is matched by two storylines. A second
  storyline with the same key is created only when no existing one (open or withdrawn) is within
  the suppression distance. So the two detections lay further apart than Stage 2 itself merges,
  and the lifecycle treats them as two phenomena, as Stage 2 does.
* **Purity.** Most storylines match no planted insight. They follow Stage 3 candidates that
  reflect ordinary match variation, as documented for Stages 3 and 4 (twins and controls have
  candidates too). No storyline matches two planted insights.
* **Latency.** Insights appear 18–25 match minutes after the planted window starts. Detection
  needs enough play after an onset to confirm it, and the snapshot schedule adds at most one
  minute. This is a property of the detector, not of transport.

### Attribution: does Stage 7 lose a detectable insight?

```bash
uv run python -m matcheyes_eval stage7-attribution --split development --seeds 1
uv run python -m matcheyes_eval stage7-attribution --split held-out --seeds 1
```

`matcheyes_eval/stage7_attribution.py` separates upstream detection limits from lifecycle
failures. The recorded snapshot evaluations are folded through reconciliation one snapshot at a
time, and the fold must reproduce the engine's final state byte for byte.

**Preservation: every insight the pipeline produced, on the snapshot that produced it.** An
insight counts as held when exactly one storyline has the same key, is verified through that
snapshot, and its latest insight has the insight's fingerprint.

| | Development | Held-out |
| --- | --- | --- |
| Insights produced by Stages 2–5 (insight × snapshot) | 10,620 | 9,328 |
| Held by a storyline | 10,620 | 9,328 |
| Lost (Stage 7 lifecycle bug) | **0** | **0** |
| Held under a different key | 0 | 0 |
| One storyline per insight, on every evaluated snapshot | 1,669/1,669 | 1,696/1,696 |
| Fold reproduces the engine state | 17/17 | 17/17 |

A test confirms the measure itself works: a reconciliation that drops one insight per snapshot
is reported as lost.

**Attribution of each planted insight.** From the start of the detection window, each snapshot
is classified as F (a storyline that has held the insight is open), B (a matching insight was
produced and no storyline holds it), C (that storyline is withdrawn), D (nothing matching was
produced yet) or E (snapshot invalid or failed). An insight never produced on any snapshot is an
upstream limitation (A), labelled with the furthest the pipeline got.

| Scenario (expected insight E1) | Development: F / B / C / D / E, outcome | Held-out: F / B / C / D / E, outcome |
| --- | --- | --- |
| S02 press surge | 18 / 0 / 0 / 20 / 0, followed from 78' | 13 / 0 / 0 / 24 / 0, followed from 82' |
| S03 game-state deep block | 0 / 0 / 0 / 41 / 0, A: no matching Stage 2 shift | 0 / 0 / 0 / 44 / 0, A: no matching Stage 2 shift |
| S04 fatigue press decay | 0 / 0 / 0 / 29 / 0, A: no matching Stage 2 shift | 0 / 0 / 0 / 30 / 0, A: no matching Stage 2 shift |
| S05 red-card reorganisation | 45 / 0 / 0 / 16 / 0, followed from 50' | 0 / 0 / 0 / 64 / 0, A: no matching Stage 2 shift |
| S06 impact substitution | 0 / 0 / 0 / 37 / 0, A: no matching Stage 2 shift | 0 / 0 / 0 / 37 / 0, A: no matching Stage 2 shift |
| S09 half-time formation shift | 0 / 0 / 0 / 53 / 0, A: Stage 3 candidate below the investigation level | 28 / 0 / 0 / 27 / 0, followed from 70' |
| S10 comeback, multi-cause | 17 / 0 / 0 / 22 / 0, followed from 78' (2 storylines) | 27 / 0 / 0 / 15 / 0, followed from 71' (2 storylines) |

* Every planted insight that Stages 2–5 produced was held from the first snapshot that produced
  it (first produced = first held in all 6 cases), and was still open at full time. No snapshot
  is B (lost), C (withdrawn) or E (unavailable).
* The 8 misses (4 per split) are all upstream. In 7, no Stage 2 shift matched on any snapshot.
  In 1 (S09, development), a Stage 3 candidate matched but stayed below the investigation level
  (`WEAK`), so it was never an eligible input to Stage 7.
* "3 of 7" is therefore a Stage 2–3 detection limitation, not a Stage 7 loss.

**Revision churn.** Revisions that record no change kind: 0 of 701 (development) and 0 of 593
(held-out). Audit-only and wording-only revisions did not occur.

### Timing

Reference replay took a median of 34 s per match on development and 30–33 s on held-out (two
runs), about 0.2 s per snapshot. Both evaluations ran alongside the full quality gate on the same machine, so
these figures are pessimistic, but they are not a throughput benchmark.

## Limitations

* One seed per scenario per split (17 matches each). The property checks are per match and held
  everywhere; the planted-truth section rests on 3 detections.
* Integrity changes do not occur naturally in the reference runs. Property 17 covers them by
  tampering the latest snapshot's evidence.
* Failures (property 16) are injected on one snapshot per match. Real failures are not simulated
  across longer outages.
* Corrections are rejected, never applied (property 18 checks rejection only).
* No wall-clock latency or throughput claim: this is deterministic local recomputation, not
  production streaming.
