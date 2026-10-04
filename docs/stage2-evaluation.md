# Stage 2 evaluation: deterministic analytics against planted truth

Reproduce with:

```
uv run python -m matcheyes_eval tune --seeds 20                             # development only
uv run python -m matcheyes_eval stage2 --split development --seeds 20
uv run python -m matcheyes_eval stage2 --split held-out --seeds 20          # run once, frozen config
```

Generator 0.1.0 and analytics 0.1.0. There are 20 seeds per scenario per split. Seven planted
scenarios also have counterfactual twins: the same seed and scripted events, but no
interventions.

## How scoring works

Scoring lives in `matcheyes_eval` only. The engine never sees truth.

* **Mechanism found:** the engine produced a metric shift with the expected metric, side and
  direction. It must start between 2 minutes before the planted window and
  `max_detection_latency_s` after its start. The evaluator's mechanism-to-metric mapping
  (`matcheyes_eval/scoring.py`):

  | Mechanism | Evidence that counts |
  | --- | --- |
  | High recoveries up | `high_regains` up for the team |
  | Territory shift | `field_tilt` up for either team (lenient: no direction) |
  | Possession share shift | `on_ball_share` up for either team (lenient: no direction) |
  | Defensive line deeper | `defensive_action_height` down for the team |
  | Press success down | `pressure_regain_rate` down for the team |
  | Opponent progressions up | `progressive_actions` up for the opponent |
  | Wide progressions up | `wide_share` up for the team |
  | Shot volume up | `shots` up for the team |
  | Player involvement up | The substitute's share of team actions is at least 1.25 times that of the player replaced |

  **Against the run of play** counts when the goal's run-of-play evidence is flagged.
* **Recall:** the share of matches in which at least one scored mechanism was found.
  * *Evidence recall* counts any matching evidence.
  * *Moment recall* requires that evidence to have reached a candidate moment.
* **Twin rate:** the same scoring applied to the counterfactual twin. Twins keep scripted goals,
  red cards and substitutions, so twin hits include real game-state swings. The twin rate is a
  **background rate**, not purely a false-positive rate.
* **Coverage:** the share of an insight's *scored* mechanisms that were found. S02's
  `opponent_pass_completion_down` is a supporting mechanism and is never scored (ADR-0007
  approval decision).
* **Decoys:** the share of matches with a metric-shift moment for the decoy team in the decoy
  window, plus any claim above the decoy's ceiling.

## Tuning (development seeds only)

| Window | Baseline | z | Recall | Twin | Coverage | Shift moments per control match |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 10 | 2.0 | 53% | 45% | 27% | 11.6 |
| 10 | 10 | 2.5 | 34% | 24% | 16% | 6.2 |
| 10 | 10 | 3.0 | 21% | 17% | 10% | 2.8 |
| 10 | 20 | 2.0 | 46% | 39% | 24% | 11.9 |
| 10 | 20 | 2.5 | 29% | 24% | 14% | 6.5 |
| 10 | 20 | 3.0 | 21% | 16% | 10% | 3.2 |
| 15 | 15 | 2.0 | 41% | 28% | 20% | 10.4 |
| 15 | 15 | 2.5 | 24% | 19% | 12% | 5.0 |
| 15 | 15 | 3.0 | 18% | 14% | 9% | 1.9 |
| **15** | **30** | **2.0** | **41%** | **24%** | **20%** | 8.7 |
| 15 | 30 | 2.5 | 26% | 17% | 13% | 4.5 |
| 15 | 30 | 3.0 | 17% | 11% | 8% | 1.8 |

Rule: choose the largest planted-minus-twin gap (window 15, baseline 30, z 2.0). That setting
left about 10 moments per match once the baseline was allowed to shrink near kick-off, which is
too dense to read. Moments were therefore restricted to clusters with a shift reaching z ≥ 3 or
two or more co-occurring metric families. Evidence keeps z ≥ 2.0. The configuration was frozen
before the held-out run.

## Results

Each cell is evidence recall / moment recall, over 20 matches per cell.

| Insight | Development, planted | Development, twin | Held-out, planted | Held-out, twin |
| --- | ---: | ---: | ---: | ---: |
| S02 press surge | 40 / 35% | 5 / 5% | 30 / 30% | 25 / 25% |
| S03 deep block after a goal | 35 / 20% | 40 / 25% | 30 / 30% | 20 / 15% |
| S04 fatigue press decay | 10 / 5% | 0 / 0% | 15 / 15% | 5 / 5% |
| S05 red-card reorganisation | 20 / 20% | 20 / 20% | 20 / 20% | 10 / 5% |
| S06 impact substitution | 65 / 65% | 45 / 45% | 60 / 60% | 55 / 55% |
| S07 against the run of play | 50 / 50% | n/a | 55 / 55% | n/a |
| S09 half-time formation shift | 50 / 40% | 45 / 35% | 40 / 30% | 45 / 35% |
| S10 comeback, multiple causes | 65 / 55% | 10 / 10% | 45 / 40% | 40 / 25% |
| **All, excluding S07 (evidence)** | **41%** | **24%** | **34%** | **29%** |
| **All, excluding S07 (moment)** | **34%** | **20%** | **32%** | **24%** |
| Mean mechanism coverage | 20% | | 18% | |

Per-mechanism detail (held-out, planted versus twin, evidence level):

| Scenario | Mechanism | Planted | Twin |
| --- | --- | ---: | ---: |
| S02 | High recoveries up | 15% | 0% |
| S02 | Territory shift | 20% | 25% |
| S04 | Press success down | 15% | 0% |
| S06 | Player involvement up | 50% | 40% |
| S06 | Wide progressions up | 30% | 25% |
| S09 | Territory shift | 35% | 40% |
| S10 | Territory shift | 45% | 30% |

Mechanisms tied directly to how a team wins the ball (high regains, press success) are almost
never seen in the twins, but they are found in only 15% of planted matches. Territory and
possession swings are found often in both, because goals, red cards and fatigue move them too.

| Controls and decoys | Development | Held-out |
| --- | ---: | ---: |
| Shift moments per match, S01 control | 5.5 (max 9) | 5.7 (max 10) |
| Shift moments per match, all scenarios | 5.0–6.2 | 5.0–6.5 |
| S07 decoy: a Redmarsh moment in 65'–80' | 45% | 50% |
| S08 decoy: a Brightwater moment in 58'–66' | 25% | 35% |
| Claims above any decoy ceiling | 0 | 0 |

Reading the results:

* **The analytics describe; they do not yet discriminate well.** On held-out seeds, planted
  windows produce matching evidence only slightly more often than twins (34% against 29%).
  Development showed a larger gap (41% against 24%) that did not fully generalise. Part of the
  development gap in S02 and S10 was seed luck, which is exactly what the held-out split exists
  to reveal.
* **The planted effects are subtle within a single match.** Stage 1b measured them as
  significant across 20 paired seeds (t of roughly 1.5 to 5.7). Per match, the change is
  comparable to ordinary minute-to-minute variation. A pre-tuning diagnostic showed most
  mechanisms raising the window statistic around onset by only about 0.5 SD over the twin.
* **Decoys behave as required on claims, though not on salience.** No claim exceeds ASSOCIATED.
  The S08 turnover cluster is visible as an observation, as it should be, and the coincident
  substitution is only ever listed as context. But moment density means a decoy window often
  contains some moment.
* **Against the run of play** is flagged for about half the S07 goals. The heuristic (field tilt
  ≤ 1/3 and fewer shots in the previous 10 minutes) is conservative.

## Implications for Stage 3

Stage 2 evidence is a set of noisy, traceable observations. A credible explanation layer has to:

* combine several pieces of evidence and compare them against game-state baselines;
* state uncertainty;
* never promote one shift to a cause.

Options for raising single-match power, which are user decisions and not taken here:

* possession-level models that adjust for score and fatigue;
* opponent-adjusted baselines;
* revisiting scenario effect sizes, which ADR-0007 says must not be tuned merely to satisfy
  metrics.
