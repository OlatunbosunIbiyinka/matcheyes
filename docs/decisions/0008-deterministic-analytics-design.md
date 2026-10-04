# ADR-0008: Deterministic analytics design

- Status: Proposed
- Date: 2026-10-04
- Stage: 2

## Context

Stage 2 must turn observable events into structured, traceable evidence that later stages can
reason over. Three constraints shape the design:

* **Independence from the generator.** Analytics must make sense for any event provider. They
  may not use hidden state, scenario IDs, answer keys, generator parameters, or knowledge of how
  scenarios are produced.
* **No causal claims.** Analytics observe and co-locate changes. Causal language belongs to
  later stages.
* **Honest evaluation.** Tune on development seeds and evaluate held-out seeds once. Report
  against controls, decoys and counterfactual twins.

## Decision

1. **Data flow.** Observable events become possessions and a minute timeline. From these we
   build per-team, per-minute metric series and whole-match summaries, then shifts, evidence
   objects and candidate moments. Everything is pure and deterministic, and lives in
   `matcheyes.analytics`, which may import `matcheyes.domain` only.
2. **A small metric set.** Twelve per-minute metrics across control, territory, pressing,
   defending, progression, width, passing, attacking and ball security, plus summaries such as
   PPDA, regains by third, tempo and player involvement. Definitions use standard pitch zones
   and public analytics conventions ([metrics.md](../metrics.md)).
3. **Exact window pooling.** Series store a numerator, a denominator and a sum of squares per
   bin, plus the contributing event IDs. Any window can be pooled exactly and traced back to
   events.
4. **Shift detection.** Compare a baseline window with an after-window. The variance is the
   larger of the independent-sample variance and a cluster-robust variance with minutes as
   clusters, because actions within a possession are correlated. Each metric has a
   football-scale effect floor. For complementary metrics, only the gaining team reports.
   Non-maximum suppression keeps one peak per change.
5. **Two levels of output.**
   * **Evidence** is sensitive (z ≥ 2.0). Every shift is kept with its statistic so that later
     stages can test it.
   * **Candidate moments** are a shortlist: a cluster qualifies when a shift reaches z ≥ 3, or
     when two or more metric families co-occur.
   * Key events (goals, dismissals, substitutions, formation changes) are always moments.
6. **Labels and claim ceiling.** Key events are FACT; everything computed is ANALYSIS. Every
   evidence object and moment is validated at **at most ASSOCIATED**. Nearby key events are
   attached as `context_evidence_ids`, as temporal context only.
7. **Configuration chosen on development seeds only:**
   * after-window 15 minutes;
   * baseline 30 minutes, shrinking near kick-off but never below 15;
   * z ≥ 2.0 for evidence, z ≥ 3.0 for moments;
   * merge distance 5 minutes.

   Selection rule: the largest gap between planted and twin recall, with ties broken by fewer
   control moments, plus a readability constraint on moment density. The held-out split was
   evaluated once, after freezing.
8. **Scoring lives only in `matcheyes_eval`.** The mapping from mechanism to metric belongs to
   the evaluator, not the engine.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Binomial or Poisson tests only | They ignore within-possession clustering. Field tilt, for example, produced several spurious shifts per match. |
| Bayesian online change-point detection or a CUSUM over raw events | More machinery, harder to explain to a non-statistician, and no evidence yet that it would beat a transparent two-window test. Worth revisiting in Stage 2b. |
| Metrics that mirror the generator's levers (for example "press intensity") | Reverse-engineering the generator; it would not transfer to real providers. |
| One threshold for evidence and moments | At a sensitive threshold, moments became too dense (about 10 per match); at a strict one, recall collapsed. |
| xG or pass-difficulty models | Not in the observable data contract; that would mean fabricating fields. |

## Consequences

* The analytics are general football analytics. They work on any provider that can be mapped to
  the domain events.
* Every number links to event IDs, and every statement is templated from those numbers.
* **Single-match detection of subtle planted changes is weak** ([evaluation report](../stage2-evaluation.md)).
  * Held-out evidence recall: 34% planted against 29% for counterfactual twins.
  * Held-out moment recall: 32% against 24%.
  * Strong, abrupt changes are detected far more reliably than gradual ones.

  Stage 3 must therefore treat Stage 2 evidence as noisy observations, weigh several of them,
  and express uncertainty. It must never present one shift as proof.
* There are about 5–6 shift moments per match even in controls. Many reflect genuine
  game-state swings, but they are not planted causes. Ranking and explanation in later stages
  must filter them.
