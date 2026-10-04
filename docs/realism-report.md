# Generator realism and effect-size report

Generator `0.1.0`. Reproduce with:

```bash
uv run python -m matcheyes_synth realism --split dev --count 20
uv run python -m matcheyes_synth realism --split held-out --count 20
uv run python -m matcheyes_synth effects --count 20
```

Every match below passed `validate_match` and produced its scripted events. No generation
failures occurred in 400 matches, nor in the 360 counterfactual-twin matches.

## Realism: all 10 scenarios × 20 seeds per split

Bands are **provisional** ([synthetic-data.md](synthetic-data.md#realism-acceptance)) and still
need checking against cited public benchmarks. A band "passes" when at least 90% of samples fall
inside it, or for goals when the mean does. Team metrics have two samples per match.

**Development seeds (10000–10019):** used for calibration.

| Metric | Band | n | Mean | p5 | p50 | p95 | Within band | Pass |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| events_per_match | 1400 - 2400 | 200 | 1.9e+03 | 1.76e+03 | 1.9e+03 | 2.08e+03 | 100% | yes |
| passes_per_team | 300 - 650 | 400 | 504 | 366 | 507 | 649 | 95% | yes |
| pass_completion_per_team | 0.7 - 0.9 | 400 | 0.835 | 0.778 | 0.837 | 0.884 | 100% | yes |
| shots_per_team | 4 - 25 | 400 | 14.3 | 5 | 14 | 26 | 94% | yes |
| goals_per_match | 2.3 - 3.3 (mean) | 200 | 3.08 | 0 | 3 | 6 | n/a (mean) | yes |
| fouls_per_match | 14 - 32 | 200 | 20 | 14 | 20 | 29 | 95% | yes |
| possession_share_per_team | 0.3 - 0.7 | 400 | 0.5 | 0.348 | 0.5 | 0.652 | 99% | yes |

**Held-out seeds (900000–900019):** never used for calibration.

| Metric | Band | n | Mean | p5 | p50 | p95 | Within band | Pass |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| events_per_match | 1400 - 2400 | 200 | 1.9e+03 | 1.72e+03 | 1.9e+03 | 2.07e+03 | 100% | yes |
| passes_per_team | 300 - 650 | 400 | 502 | 358 | 504 | 647 | 96% | yes |
| pass_completion_per_team | 0.7 - 0.9 | 400 | 0.832 | 0.771 | 0.834 | 0.879 | 100% | yes |
| shots_per_team | 4 - 25 | 400 | 14.8 | 7 | 14 | 27 | 93% | yes |
| goals_per_match | 2.3 - 3.3 (mean) | 200 | 3.1 | 0 | 3 | 6 | n/a (mean) | yes |
| fouls_per_match | 14 - 32 | 200 | 20 | 13 | 20 | 27 | 93% | yes |
| possession_share_per_team | 0.3 - 0.7 | 400 | 0.5 | 0.348 | 0.5 | 0.652 | 100% | yes |

The splits agree closely, so there is no sign of over-fitting to the calibration seeds.

**Caveats.**

- Goals per match includes the catalogue's forced goals (5 across 10 scenarios, about +0.5 per
  match). Control-style matches alone score less.
- Possession share is a proxy: the share of on-ball actions. Observable data has no possession
  IDs.
- Mean goals sit near the top of the band, and shots per team have a long upper tail (p95 ≈ 26).
  Both come from mismatches such as Kestrel Bay v Redmarsh.

## Calibration history (development seeds, 8 per scenario)

Each step changed a football mechanism, not a metric.

| Step | Mechanism change | Effect |
| --- | --- | --- |
| 1 | Initial model | 1,280 events, 71% completion, 6 shots/team, 11 fouls |
| 2 | Shorter action durations; more pressing; foul rates | Events and fouls up |
| 3 | Pass targets stay on the pitch; out-of-play only from failed passes | Completion 86% (it had been depressed by impossible passes) |
| 4 | Defenders foul far less inside their own box | Penalties fell from ~3 to a realistic rate per match; goals 5.2 → 3.3 |
| 5 | Attacks stop at the goal line instead of drifting onto it | Fewer point-blank shots |
| 6 | Pass durations and finishing slightly reduced | All bands inside |
| 7 | Line of engagement; press effectiveness depends on execution; pressed players recycle, free players progress | Planted effects became observable (below) |

## Planted effects: are they detectable in principle?

Each scenario is paired with its **counterfactual twin**: same seed, interventions removed.
The proxy is measured in the expected-insight window. The table shows the mean paired difference
over 20 development seeds and its paired t statistic. Proxies are crude generator-side checks,
not MatchEyes metrics.

| Scenario | Proxy | Role | Expected | Mean paired difference | Paired t | Sign |
| --- | --- | --- | --- | --- | --- | --- |
| S02_press_surge | pressures/min | secondary | + | +0.187 | +1.24 | ok |
| S02_press_surge | high regains/min | primary | + | +0.130 | +2.72 | ok |
| S02_press_surge | opponent completion | secondary | - | -0.004 | -0.32 | ok |
| S03_game_state_deep_block | mean defensive x | primary | - | -2.774 | -1.59 | ok |
| S04_fatigue_press_decay | press success | primary | - | -0.074 | -3.92 | ok |
| S05_red_card_reorganisation | mean defensive x | primary | - | -4.313 | -1.68 | ok |
| S06_impact_substitution | wide pass share | primary | + | +0.048 | +2.18 | ok |
| S09_halftime_formation_shift | mean defensive x | primary | + | +2.968 | +1.45 | ok |
| S10_comeback_multi_cause | high regains/min | primary | + | +0.250 | +5.66 | ok |

**Reading this honestly**

- All nine proxies move in the planted direction.
- Four are clearly separated from noise (|t| > 2): S02 high regains, S04 press success, S06 wide
  passes and S10 high regains.
- The three defensive-height effects (S03, S05, S09) are directional but only moderately
  separated. The proxy averages pressures (which happen in a team's own third whatever its line)
  with interceptions and recoveries. A Stage 2 line-height metric should separate them better.
- S02's `opponent_pass_completion_down` mechanism barely registers (−0.4 points). A 15-minute
  window holds about 100 opponent passes, so a ~1-point change is below match noise. This
  mechanism is weakly supported by the generator; see ADR-0007 for the open decision.
- Before the intervention, twins share their randomness. Afterwards they diverge entirely, so
  the paired differences behave almost like independent samples. These t values are therefore
  conservative.
