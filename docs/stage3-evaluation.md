# Stage 3 evaluation: contextual evidence against the frozen Stage 2 baseline

Reproduce with:

```
uv run python -m matcheyes_eval stage3 --split development --seeds 20
uv run python -m matcheyes_eval stage3 --split held-out --seeds 20    # run once, frozen code
```

Setup:

* Generator 0.1.0, Stage 2 analytics 0.1.0 (frozen), contextual analytics 0.2.0.
* 20 seeds per scenario per split.
* Stage 2 and Stage 3 are scored on the **same matches in one pass**.

Method: [contextual-evidence.md](contextual-evidence.md). Design: [ADR-0009](decisions/0009-contextual-evidence.md).

## How scoring works

Scoring follows [stage2-evaluation.md](stage2-evaluation.md), with the same mechanism-to-metric
mapping, timing tolerance and twins, with one difference: **only metric mechanisms are scored,
for both stages**. Stage 3 re-grades metric shifts and leaves player-involvement and
run-of-play evidence untouched, so including them would blur the comparison. This is why the
Stage 2 numbers below are a little lower than in the Stage 2 report: S06 involvement and S07
are excluded.

| Measure | Meaning |
| --- | --- |
| Stage 2 evidence | A matching Stage 2 shift exists |
| Stage 2 moment | A matching shift reached a Stage 2 candidate moment |
| Stage 3 weak+ | A matching contextual candidate is at least Weak (it survives context) |
| Stage 3 moment | A matching candidate is at least Moderate (the moment level) |
| Stage 3 strong | A matching candidate is Strong |

Twins keep scripted goals, red cards and substitutions, and the generator applies background
game-state and late-match dynamics to every match. A twin hit is therefore often a real football
change without the planted cause: the twin rate is a **background rate**, not a pure
false-positive rate.

## Development and design decisions

Design decisions used development seeds only. There were two development runs:

1. **First design (rejected):** Moderate required independent support *or* sustained
   persistence. Moment recall was 19% planted against 7% twin, but **control moment density
   rose to 6.6 per match**, against Stage 2's 5.5. The candidate profile showed why:
   * "sustained without support" was the largest group in controls (41% of candidates) and
     smaller among planted matches (22%);
   * independent multi-family support was more common in planted matches than in controls
     (about 50% against 28%).
2. **Final design:** Moderate requires independent support, and persistence is a gate
   (transient or reversed shifts are capped at Weak). No thresholds were searched, and no further
   iteration followed. The held-out split was then run once.

## Results

Each cell is planted / twin over 20 matches. Development first, then held-out.

| Measure | Development | Held-out |
| --- | ---: | ---: |
| Stage 2 evidence | 34% / 19% (gap +16) | 30% / 24% (gap +6) |
| Stage 2 moment | 27% / 14% (gap +13) | 26% / 19% (gap +7) |
| **Stage 3 weak+** | 30% / 15% (gap +15) | 26% / 21% (gap +6) |
| **Stage 3 moment** | 11% / 5% (gap +6) | 7% / 7% (gap 0) |
| Stage 3 strong | 5% / 4% | 3% / 1% |

Per insight (planted / twin):

| Insight | Stage 2 evidence, dev | Stage 3 weak+, dev | Stage 3 moment, dev | Stage 2 evidence, held-out | Stage 3 weak+, held-out | Stage 3 moment, held-out |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S02 press surge | 40 / 5 | 30 / 5 | 15 / 5 | 30 / 25 | 30 / 25 | 10 / 10 |
| S03 deep block after a goal | 35 / 40 | 35 / 35 | 5 / 20 | 30 / 20 | 25 / 15 | 0 / 5 |
| S04 press decay | 10 / 0 | 10 / 0 | 0 / 0 | 15 / 5 | 15 / 5 | 0 / 0 |
| S05 red-card reorganisation | 20 / 20 | 20 / 20 | 0 / 0 | 20 / 10 | 20 / 5 | 0 / 5 |
| S06 impact substitution (wide share) | 20 / 10 | 20 / 5 | 5 / 0 | 30 / 25 | 25 / 15 | 0 / 0 |
| S09 half-time formation shift | 50 / 45 | 45 / 35 | 20 / 10 | 40 / 45 | 35 / 45 | 15 / 20 |
| S10 comeback, multiple causes | 65 / 10 | 50 / 5 | 30 / 0 | 45 / 40 | 35 / 35 | 25 / 10 |

| Controls, decoys, safety | Stage 2, dev | Stage 3, dev | Stage 2, held-out | Stage 3, held-out |
| --- | ---: | ---: | ---: | ---: |
| Moments per S01 control match | 5.5 | **3.0** | 5.7 | **2.5** |
| Evidence items per control match (shifts / weak+) | 18.4 | 15.7 | 19.3 | 15.3 |
| Moments per planted match / per twin | 5.6 / 5.4 | 2.0 / 2.1 | 5.9 / 5.7 | 2.2 / 2.2 |
| S07 decoy: a moment for Redmarsh in 65'–80' | 45% | **10%** | 50% | **10%** |
| S08 decoy: a moment for Brightwater in 58'–66' | 25% | **5%** | 35% | **15%** |
| Claims above a decoy ceiling | 0 | 0 | 0 | 0 |
| Claims above ASSOCIATED | – | 0 | – | 0 |
| Untraceable candidates or moments | – | 0 of 6,160 | – | 0 of 6,377 |

Candidate profile on held-out seeds (baseline, then support, then persistence):

| Group | n | Removed | Aligned | Support and sustained | No support, sustained | Transient (any) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Control, all candidates | 386 | 21% | 11% | 16% | 38% | 9% |
| Planted, candidates matching the mechanism | 51 | 14% | 22% | 20% | 37% | 8% |
| Twin, candidates matching the mechanism | 36 | 17% | 9% | 25% | 28% | 14% |

## Reading the results

* **Stage 3 makes the shortlist much quieter, and that holds on held-out seeds.**
  * Control moments fall by more than half (5.7 to 2.5 per match).
  * Decoy-window moments fall from 50% to 10% (S07) and from 35% to 15% (S08).
  * About a fifth of control shifts disappear once compared within one game state, and a
    further tenth are the ordinary response to a goal or red card.
* **Stage 3 does not widen the planted-versus-twin gap.** On held-out seeds the gap at Weak or
  above stays at about 6 points, and at moment level it disappears (7% against 7%). The
  development gap at moment level (+6) came mostly from S10 and did not generalise.
* **Why:** what survives context is real, multi-family, sustained change, and the twins contain
  plenty of it, from goals, red cards and late-match dynamics. Matching twin candidates are as
  often supported and sustained as planted ones (25% against 20%). Stage 3 separates change from
  noise; it cannot separate a planted cause from a natural one. That is a question about causes,
  which is Stage 4's investigation and verification work.
* **Recall at moment level is low.** Requiring independent support costs most planted
  detections, because single-match planted effects are small (Stage 2 report). Only 3–5% of
  planted windows reach Strong.
* **S03 and S05 behave as context-aligned, by design.** Their planted changes coincide with a
  goal or a red card and point the ordinary way, so Stage 3 caps them at Weak. This is the
  intended reading ("consistent with the usual response to the goal") and it is also why their
  moment recall is near zero. Distinguishing a deliberate reorganisation from the ordinary
  response is left to Stage 4.
* **Nothing exceeds ASSOCIATED, and everything is traceable.** All 12,537 candidates across
  both splits list existing event IDs.

## What this means for Stage 4

Stage 3 hands Stage 4 a short, ranked, explained list: about 2–3 moments per match, each with
its baseline type, supporting and contradicting signals, persistence, context and workload. The
evidence is cleaner but **not more diagnostic of planted causes** than Stage 2 evidence.
Competing explanations, especially "this is the ordinary score effect" and "this is late-match
drift", must be tested explicitly rather than assumed away.
