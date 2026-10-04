# ADR-0007: Generator calibration and acceptance

- Status: Accepted (2026-10-04, with Stage 1b approval)
- Date: 2026-10-03
- Stage: 1b

## Context

The seeded generator ([synthetic-data.md](../synthetic-data.md)) must produce coherent
football, pass every invariant, sit inside the provisional realism bands, and plant causes that
leave an observable trace. All of this must hold without tuning the generator to flatter future
MatchEyes detectors, and without fitting it to the seeds later used for reported metrics.

## Decision

1. **Calibrate mechanisms, not numbers.** Every change during calibration is a football
   mechanism with a stated reason (for example: defenders foul less inside their own box;
   a pressed player recycles possession while an unpressed one looks forward; a deep block does
   not press beyond its line of engagement). We do not add per-metric correction factors.
2. **Calibrate on development seeds only.** Held-out seeds (`900000+`) are generated only to
   confirm the result. The realism report shows both splits side by side.
3. **Acceptance per match** is: domain validation, `validate_match`, and every scripted event
   produced within its tolerance. Failure raises `GenerationError`. Nothing is repaired.
   Realism bands and effect sizes are checked over many seeds, never per match. Whether an
   engine detects anything is never an acceptance rule.
4. **Detectable in principle** is measured against a *counterfactual twin*: the same scenario
   and seed with the planted interventions removed. Each scenario with a planted cause has one
   **primary** observable proxy. Its paired difference over 20 development seeds must have the
   expected sign with paired t > 1. **Secondary** proxies are measured and reported only.
5. **Scripted-event tolerances:** goal 120 s, red card 180 s, substitution 300 s (needs a dead
   ball), formation change 60 s (a bench instruction, emitted in open play). The design said
   ±60 s for goals; that proved too tight when a dead ball (such as a goal celebration) falls
   just after the scheduled minute, so we widened it rather than force unrealistic play.
6. **Event-triggered causes start at the emitted trigger.** If an intervention is triggered by
   an observable or game-state event, its onset is the later of its scheduled start and the
   emitted trigger event, recorded as `GroundTruth.intervention_onsets`. A cause therefore never
   precedes its trigger.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Tune each metric directly to the band midpoint | Optimises numbers rather than football; the user asked us not to. |
| Calibrate on all seeds | Hides over-fitting; held-out results would no longer mean anything. |
| Require every listed mechanism to be detectable | Some mechanisms (such as a 1-point change in opponent pass completion over 15 minutes) are genuinely below match noise. Forcing them would need exaggerated, unrealistic effects. |
| Strengthen scenario deltas until all proxies pass | Would bend the hidden world to fit a detector-like test (circularity). |

## Consequences

- All seven realism bands pass on 200 development and 200 held-out matches
  ([realism-report.md](../realism-report.md)).
- All seven primary effects are detectable against their twins. Three (S03, S05, S09:
  defensive height) are only moderately separated (t ≈ 1.5–1.7) because the crude proxy mixes
  action types. Stage 2 metrics should do better.
- S02's `opponent_pass_completion_down` mechanism is weakly supported by the generator
  (t ≈ −0.3).

## Approval decision (2026-10-04)

- Keep: mechanism-based calibration, the development/held-out seed split, counterfactual twins,
  generator versioning, and the current bands as provisional distributional checks.
- Scenarios are never tuned artificially to satisfy metrics.
- **S02:** `opponent_pass_completion_down` is no longer a scored mechanism. It moves to
  `ExpectedInsight.supporting_mechanisms`: recorded as a plausible side effect, never scored.
  MatchEyes is not penalised for missing it unless future evidence shows it is reliably
  detectable from observable data. S02 is not redesigned to make it significant.
- Changing any probability changes outputs for every seed, so `GENERATOR_VERSION` must be bumped
  whenever generator behaviour changes.
