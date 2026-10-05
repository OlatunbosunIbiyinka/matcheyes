# ADR-0009: Contextual evidence and multi-signal reasoning

- Status: Proposed
- Date: 2026-10-04
- Stage: 3

## Context

Stage 2 ([ADR-0008](0008-deterministic-analytics-design.md)) produces noisy single-metric
shifts, with about 5–6 shift moments per match even in controls. Stage 3 must strengthen or
weaken that evidence using match context, while meeting four conditions:

* stay deterministic and observable-only, with no AI, hidden state or generator knowledge;
* leave Stage 2 output unchanged;
* never claim more than ASSOCIATED;
* keep every evidence object traceable to event IDs.

## Decision

1. **Re-assess rather than re-detect.** Every Stage 2 metric shift becomes one contextual
   candidate. Stage 3 adds no detector, so Stage 2 remains the frozen baseline. A regression
   test pins its output.
2. **Contextual match state** comes from observable events only: score and game state, players
   on the pitch, substitutions, period and phase, and time since goals and key events.
3. **Same-regime baselines.** A shift whose windows straddle a goal or dismissal is recomputed
   on windows cut back to one game-state regime, and is INSUFFICIENT if it does not survive. A
   transition within 5 minutes of the shift cannot be separated from it. In that case a shift
   in the ordinary response direction (the conceding or extra-player team pushes, the scoring or
   short-handed team protects) is *context-aligned* and capped at WEAK. Only directions are
   used, never magnitudes.
4. **Football patterns** (eight, each with a stated rationale) test related signals once, at the
   same boundary. Support requires z ≥ 1.5 and contradiction z ≤ −1.0. Strength counts
   **independent families**, which excludes the core's own family and mechanically linked
   metrics, and is capped at two.
5. **Persistence** uses three 5-minute sub-windows: sustained, transient, reversed or
   indeterminate. It is a gate: transient or reversed shifts are capped at WEAK.
6. **Evidence levels:**
   * Insufficient: removed by context.
   * Weak: unusual only, or capped.
   * Moderate: plus at least one independent family, and not a burst.
   * Strong: at least two families, sustained, uncontradicted.

   Each candidate carries its `level_basis`. Claim strength is ASSOCIATED with independent
   support, otherwise OBSERVED.
7. **Ranking** is lexicographic and explainable: level, persistence, families (capped), fewer
   contradictions, then the statistic. It never uses the number of metrics. **Moments** are
   candidates at Moderate or above, merged per team within 5 minutes.
8. **Workload** is reported as observable proxies (minutes on the pitch, recent pressures and
   actions per outfield player), as context only. It is never called fatigue and never changes
   a level.
9. **Evaluation** scores Stage 2 and Stage 3 on the same matches, using metric mechanisms only.
   Design decisions used development seeds; the held-out split was run once on frozen code.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Sustained persistence alone reaches Moderate | Tried first. On development seeds it raised control moments to 6.6 per match (Stage 2: 5.5), because about 60% of control shifts are sustained |
| Magnitude-adjusted score-effect baselines | No defensible single-match estimate; a calibrated assumption in disguise |
| Population baselines learnt from many matches | The engine sees one match; learnt from generated data, it would learn the generator |
| Counting supporting metrics | Correlated metrics are not independent witnesses |
| Suppressing context-aligned shifts | They are real observations; they stay visible at Weak |
| A fatigue index, or workload as a level input | Not measurable from events; no defensible dose-response |
| New detectors (CUSUM, Bayesian change points) | Out of scope: Stage 3 strengthens existing evidence and keeps Stage 2 frozen |

## Consequences

* **Much quieter shortlist** ([evaluation](../stage3-evaluation.md)), measured on held-out
  seeds:
  * control moments fall from 5.7 to 2.5 per match;
  * S07 decoy-window moments fall from 50% to 10%, and S08 from 35% to 15%;
  * no claim exceeds ASSOCIATED;
  * all 12,537 candidates across both splits are traceable.
* **No gain in planted-versus-twin separation.** On held-out seeds, Weak or above gives 26%
  planted against 21% twin (Stage 2: 30% against 24%). At moment level it is 7% against 7%. The
  twins' real game-state and late-match changes survive context just as planted changes do.
  Stage 3 separates change from noise, not planted causes from natural ones.
* **Low recall at moment level** (7–11% planted). Single-match planted effects are small, and
  requiring an independent family removes most of them.
* Changes that coincide with a goal or red card and point the ordinary way (as in S03 and S05)
  are capped at Weak. Telling a deliberate reorganisation apart from the ordinary response is
  explicitly Stage 4's job.
* Stage 4 receives a short, explained candidate list and must test competing explanations,
  especially the ordinary score effect and late-match drift.
