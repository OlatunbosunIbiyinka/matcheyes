# Deterministic metrics (Stage 2)

Everything here is computed by `matcheyes.analytics` from **observable events only**: the event
vocabulary in [data-model.md](data-model.md). The analytics never import, read or reason about
hidden team state, planted causes, scenario IDs, answer keys or generator parameters. They are
written as if the events came from an independent football data provider. The import rules in
`tests/architecture/` enforce this.

Pipeline:

```
OBSERVABLE EVENTS -> possessions, minute timeline        (possessions.py, timeline.py)
                  -> per-team per-minute metric series   (metrics.py)
                  -> whole-match summaries               (summary.py)
                  -> shifts between adjacent windows     (changepoints.py)
                  -> evidence objects                    (evidence.py, moments.py)
                  -> candidate moments                   (moments.py)
```

Entry point: `matcheyes.analytics.analysis.analyse_match(match) -> MatchAnalysis`.
CLI: `uv run python -m matcheyes analyse <observable_match_dir> [--json out.json]`.

## Conventions

* **Frame.** Every location is in the acting team's attacking frame on a 105 x 68 m pitch:
  x = 0 is the team's own goal line, and the team attacks towards x = 105. Defensive actions
  and pressures are therefore in the defending team's frame, so "high" always means "near the
  opponent's goal".
* **Zones** (`geometry.py`) follow pitch markings and common analytics convention:
  * thirds at x = 35 and x = 70;
  * the penalty box is x ≥ 88.5 and |y − 34| ≤ 20.16;
  * wide channels are the strips outside the box width, |y − 34| > 20.16;
  * the high-regain zone is x ≥ 65, i.e. within 40 m of the opponent's goal line.
* **Progressive action:** a completed pass or a carry that reduces the distance to the goal
  centre by:
  * at least 30 m if both points are in the own half;
  * at least 15 m if it crosses halfway;
  * at least 10 m if both points are in the opponent's half.

  This is a widely used public definition.
* **Timeline.** One-minute bins: first half (including stoppage), then second half. A bin
  exists for every minute in which the period had any event.
* **Series storage.** Each bin stores a numerator and a denominator. Mean metrics also store a
  sum of squares. Any window can therefore be pooled exactly. Each bin also keeps the event IDs
  counted in its numerator, so every number traces back to events.
* **Labels.** Key events are **FACT** (read straight from the event stream). Everything computed
  is **ANALYSIS**. Analytics never produce AI INTERPRETATION or NARRATIVE.

## Possession sequences

| | |
|---|---|
| Definition | A maximal run of events during which one team controls the ball. |
| Input events | Control events: on-ball actions (pass, carry, take-on, shot) and ball-winning actions (interception, ball recovery, won tackle). All other events (pressure, clearance, block, foul, card, substitution, formation change) are attached to the possession in progress. |
| Calculation | A new possession starts when a control event belongs to a different team than the current possession, or at any kick-off. A period end closes the current possession. **Start type**: kick-off, set piece (any other restart), regain (interception, recovery or won tackle), or open play (a loose ball collected). **End reason**: goal; shot; offside; out of play (the next event is an opponent throw-in, goal kick or corner); free kick conceded (an opponent free kick or penalty); lost in play; period end; or stoppage (own goal or another restart). **Recorded per possession**: on-ball actions, passes and completed passes, shots, goals, maximum x, whether it reached the attacking third or the box, start and end location, and duration. |
| Assumptions | Events arrive in canonical order. A won tackle gives the tackler's team the ball. A failed take-on or an incomplete pass does not end a possession by itself; the opponent's next control event does. |
| Limitations | No tracking data, so possession is inferred from events. A loose ball that is cleared and recovered by the same team stays one possession. Duration is measured from the first to the last event in the run, so the closing pressure counts towards the possession that lost the ball. |
| Why it matters | Possessions are the unit of attacking play. Turnovers, regains, sequence reach and tempo all derive from them. |
| Tests | `tests/analytics/test_possessions.py`: the six possessions of the hand-built match, with boundaries, event membership, counts and reach. `test_analysis_generated.py`: every on-ball event belongs to exactly one possession, and every kick-off starts one. |

## Per-minute team metrics

Detection floors (`min_effect`, `min_samples`) are football-scale thresholds below which a
change is not worth reporting. They were set before evaluation and were not tuned on held-out
data.

### on_ball_share (control, proportion, complementary)

| | |
|---|---|
| Definition | The team's share of all on-ball actions. A possession-share proxy. |
| Input events | Pass, Carry, TakeOn, Shot. |
| Calculation | Per bin: team on-ball actions / both teams' on-ball actions. |
| Assumptions | Counting actions approximates time on the ball. |
| Limitations | It is not clock-time possession. Many short passes inflate it relative to direct play. |
| Why it matters | A swing in control often accompanies tactical changes, game-state effects and dismissals. |
| Tests | Hand-computed first minute (2/5 versus 3/5). Complementarity holds in every bin. |

Detection: min effect 8 percentage points; at least 60 actions per window.

### field_tilt (territory, proportion, complementary)

| | |
|---|---|
| Definition | The team's share of on-ball actions that start in the attacking third. |
| Input events | On-ball actions with location x ≥ 70. |
| Calculation | Per bin: team attacking-third actions / both teams' attacking-third actions. |
| Assumptions | Where a team plays reflects its territorial control. |
| Limitations | Strongly clustered: one long attack contributes many correlated actions. The shift detector uses cluster-robust variance for this reason. |
| Why it matters | It is the standard measure of territorial dominance, and the basis of the run-of-play check. |
| Tests | Hand-computed totals (away 2/3, home 1/3). Complementarity. |

Detection: min effect 12 percentage points; at least 20 actions per window.

### high_regains (pressing, count)

| | |
|---|---|
| Definition | Ball-winning actions within 40 m of the opponent's goal line. |
| Input events | Interception, BallRecovery, Tackle (won), with x ≥ 65 in the team's frame. |
| Calculation | Count per minute. |
| Assumptions | A high regain implies the team was defending high or pressing. |
| Limitations | It does not distinguish a press-forced turnover from a loose ball. |
| Why it matters | High turnovers create short-field chances and are the clearest observable trace of a press. |
| Tests | Hand-built stream: a recovery at x = 66 counts; the regains at x = 44 and x = 25 do not. |

Detection: min effect 40% relative.

### pressures (pressing, count)

| | |
|---|---|
| Definition | Pressure events made by the team. |
| Input events | Pressure. |
| Calculation | Count per minute. |
| Assumptions | The provider records pressures consistently. |
| Limitations | Effort, not effectiveness. Pressure counts rise when the opponent has more of the ball. |
| Why it matters | Separates "pressing more" from "pressing better" (see pressure_regain_rate). |
| Tests | Hand-computed count in the minimal match. |

Detection: min effect 30% relative.

### pressure_regain_rate (pressing, proportion)

| | |
|---|---|
| Definition | The share of the team's pressures after which the team itself is next to control the ball, within 5 seconds. |
| Input events | Pressure, then the next control event of either team. |
| Calculation | Per bin of the pressure: successes / pressures. |
| Assumptions | A regain within 5 s of a pressure is plausibly linked to it. This is a common analytics window. |
| Limitations | Association, not attribution: a teammate's interception counts too. Small samples per minute. |
| Why it matters | Pressing effectiveness. A fall with steady pressure counts is the observable signature of a press losing its bite. |
| Tests | Hand-built stream: a regain after 4 s counts and one after 5.5 s does not. Minimal match gives 1/1. |

Detection: min effect 10 percentage points; at least 15 pressures per window.

### defensive_action_height (defending, mean)

| | |
|---|---|
| Definition | The mean x of the team's defensive actions, in its own frame (metres from its own goal). |
| Input events | Tackle, Interception, BallRecovery, Block, Clearance, Foul. Pressures are excluded. |
| Calculation | Pooled mean per window. |
| Assumptions | Where a team defends tracks how high its block sits. |
| Limitations | It also depends on where the opponent attacks. No tracking data, so the true line height is unknown. |
| Why it matters | A standard proxy for block height: deep blocks, red-card reshapes and high presses. |
| Tests | Hand-computed mean (44 + 55 + 25) / 3. |

Detection: min effect 4 m; at least 10 actions per window.

### progressive_actions (progression, count)

| | |
|---|---|
| Definition | Completed passes and carries that are progressive (see Conventions). |
| Input events | Pass (complete), Carry. |
| Calculation | Count per minute. |
| Assumptions | Distance-to-goal gain reflects meaningful advancement. |
| Limitations | Ignores defensive pressure on the action. |
| Why it matters | How readily a team, or its opponent, moves the ball towards goal. A rise for the opponent signals control loss. |
| Tests | Hand-computed gains (27.3 m and 18.4 m count; 12.4 m across halfway does not). Geometry unit tests. |

Detection: min effect 30% relative.

### attacking_third_entries (progression, count)

| | |
|---|---|
| Definition | Completed passes and carries that start outside and end inside the attacking third. |
| Input events | Pass (complete), Carry. |
| Calculation | Count per minute. |
| Assumptions / limitations | Entries by set pieces count only when they are completed passes. |
| Why it matters | Territorial penetration, independent of what happens next. |
| Tests | Minimal match: away has 1 entry. |

Detection: min effect 30% relative.

### wide_share (width, proportion)

| | |
|---|---|
| Definition | The share of the team's completed open-play passes into the opponent half that end in a wide channel. |
| Input events | Pass (open play, complete, end x ≥ 52.5). |
| Calculation | Per bin: wide receptions / receptions in the opponent half. |
| Assumptions | Wide receptions in the opponent half reflect attacking width. |
| Limitations | It does not see crosses or overlaps as such. Samples are small per minute. |
| Why it matters | Detects a change in where a team attacks, for example after a wide substitute or a formation change. |
| Tests | Minimal match: 0 of 1 for home. |

Detection: min effect 10 percentage points; at least 12 passes per window.

### pass_completion (passing, proportion)

| | |
|---|---|
| Definition | The share of the team's open-play passes that are completed. |
| Input events | Pass (open play). Restarts are excluded. |
| Calculation | Per bin: completed / attempted. |
| Limitations | Not adjusted for pass difficulty (no tracking data; no xPass). |
| Why it matters | A basic execution signal. The opponent's completion is a possible press effect. |
| Tests | Minimal match: home 1/1, away 1/2. |

Detection: min effect 6 percentage points; at least 40 passes per window.

### shots (attacking, count)

| | |
|---|---|
| Definition | Shots by the team (all kinds). |
| Calculation | Count per minute. |
| Limitations | Shot quality is not modelled (no xG in the data contract). |
| Why it matters | Attacking output. Shot share before a goal feeds the run-of-play check. |
| Tests | Minimal match: bins 0 and 48. |

Detection: min effect 50% relative.

### turnovers (ball security, count)

| | |
|---|---|
| Definition | The team's possessions that end lost in open play (the opponent's next control is open play or a regain). |
| Calculation | Count per minute, placed in the bin of the possession's last event. |
| Limitations | It includes forced losses under pressure and simple misplacements alike. |
| Why it matters | Clusters of turnovers are salient, and are often noise. The S08 decoy tests that MatchEyes reports them without inventing a cause. |
| Tests | Minimal match: 2 home turnovers in bins 0 and 1. |

Detection: min effect 30% relative.

## Whole-match summaries (`TeamSummary`, `PlayerInvolvement`)

Goals (own goals credited to the opponent), possessions, totals of the series above, box entries,
regains by third, **PPDA** and possession-sequence reach. Player involvement covers minutes
played and actions.

| Metric | Definition | Limitations |
|---|---|---|
| PPDA | Opponent open-play passes in their first 60% of the pitch, divided by the team's tackles, interceptions and fouls from 40% of the pitch length upwards. | Standard formula. Undefined when there are no defensive actions in the zone. |
| own_half_turnovers | Turnovers whose last controlled location is x < 52.5. | Uses the last controlled location, not the exact loss point. |
| mean_possession_s, passes_per_possession, possessions_reaching_attacking_third | Possession-level tempo and reach. | Event-based durations. |
| share_of_team_actions (player) | The player's on-ball actions divided by the team's on-ball actions while the player was on the pitch. Minutes come from period, substitution and dismissal events. | Uses on-ball actions only; off-ball work is invisible. |

Tests: `test_metrics.py`, which includes PPDA = 0.5, the substitute's share of 0.5, minutes of
46.5 and 47.5, and the exclusion of unused substitutes.

## Shift detection (`changepoints.py`)

| | |
|---|---|
| Definition | A point where a team's metric differs between a baseline window [t − B, t) and the following window [t, t + W). |
| Calculation | The statistic is (after − before) / √(Var_before + Var_after). Each window's variance is the larger of an independent-sample variance and a cluster-robust variance with minutes as clusters. For counts, the independent variance is Poisson (floored at the mean). For proportions it is binomial. For means it is the sample variance, floored at 1 m². A boundary is a candidate when the statistic reaches z and the change exceeds the metric's football-scale floor. For complementary metrics, only the gaining team reports, so one swing is not counted twice. Non-maximum suppression keeps one peak per change. |
| Assumptions | Minutes are approximately independent clusters. |
| Limitations | Windows cannot localise a change more precisely than about W/2 minutes. A gradual change shows as a broad, weak peak. Single-match power is limited for subtle changes (see the evaluation results). |
| Why it matters | Turns 24 series into a short list of "something changed here" points. |
| Tests | `test_changepoints.py`: z computed by hand for counts and proportions, the cluster variance by hand, floors, thin-window skipping, a single peak per change, baseline offset, complementary suppression. |

## Evidence objects and candidate moments

* **MetricShiftEvidence** (ANALYSIS, OBSERVED): metric, team, direction, both windows (start,
  end, value, sample), statistic, a templated statement and the after-window event IDs.
* **KeyEventEvidence** (FACT, OBSERVED): goals (including own goals), dismissals, substitutions
  and formation changes.
* **RunOfPlayEvidence** (ANALYSIS, OBSERVED): for each goal, the scoring team's field tilt,
  on-ball share and shots for and against in the preceding 10 minutes. A goal is "against the run
  of play" when field tilt ≤ 1/3 and the scorers had fewer shots than the opponent.
* **PlayerInvolvementEvidence** (ANALYSIS, OBSERVED): for each substitution, the incoming
  player's share of team on-ball actions in the 15 minutes after, compared with the replaced
  player's share in the 15 minutes before.
* **CandidateMoment** (ANALYSIS):
  * Shifts for the same team within 5 minutes form one moment. The moment is OBSERVED if all
    its shifts are in one metric family, and ASSOCIATED if two or more families co-occur.
  * Every key event is also a moment.
  * Nearby key events are listed only as `context_evidence_ids`: temporal context, never cause.

Every evidence object and moment is validated to be **at most ASSOCIATED**. Deterministic
analytics observe and co-locate changes. Causal language (HYPOTHESISED, SUPPORTED) belongs to
later stages.
