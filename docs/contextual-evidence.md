# Contextual evidence (Stage 3)

Stage 3 strengthens or weakens Stage 2 evidence using match context. It stays deterministic and
observable-only: no AI, no hidden state, no knowledge of how the synthetic data is produced. It
detects nothing new. Every Stage 2 metric shift is re-assessed and becomes one **contextual
candidate** with an evidence level, a claim strength capped at ASSOCIATED, a rank and a context
note. Stage 2 output is frozen and unchanged (`tests/analytics/test_stage2_frozen.py`).

Design record: [ADR-0009](decisions/0009-contextual-evidence.md). Results:
[stage3-evaluation.md](stage3-evaluation.md).

| Module | Responsibility |
| --- | --- |
| `context.py` | Match state per minute: score, numbers, substitutions, phase, time since events |
| `workload.py` | Observable workload proxies (context only) |
| `baselines.py` | Is the shift still unusual within the same game state? |
| `patterns.py` | Do related metric families move with it? |
| `persistence.py` | Does it hold across its window? |
| `strength.py` | Evidence level, claim strength, ranking |
| `contextual.py` | Composition, candidates, moments |

## Contextual match state

All features are bookkeeping over observable events, at one-minute resolution. `ContextBin` is
the state at the start of a minute; the *regime* of a minute is the state at its end, so a goal
in minute g places minute g in the post-goal regime.

| Feature | Definition | Source events | Calculation | Assumptions and limitations | Tests |
| --- | --- | --- | --- | --- | --- |
| Score / goal difference | Goals for minus against, for a team | `Shot` with outcome goal; `OwnGoal` (credited to the opponent) | Cumulative count before the minute | Disallowed goals are absent from the data contract | `test_context.py` (minimal fixture, own goal) |
| Game state | Leading, drawing or trailing | As above | Sign of goal difference | Margin is ignored; a 1-0 and a 3-0 lead are the same state | `test_score_transitions_and_game_state` |
| Players on the pitch | 11 minus dismissals | `Card` red or second yellow | Cumulative | Injuries without a substitution are not observable | `test_dismissals_reduce_numbers_from_the_next_bin` |
| Substitutions made | Count per team | `Substitution` | Cumulative | None | `test_minimal_substitutions_and_period_boundary` |
| Period and phase | Opening (0–15'), middle (15–30'), closing (30'+ incl. stoppage) of each period | Timeline | From the minute within the period | Fifteen-minute thirds are a broadcast convention, not tuned | `test_phase_boundaries` |
| Time since goal | Minutes since the last goal's minute | Goals | Bin difference | One-minute resolution | `test_minimal_goal_changes_state_from_the_next_bin` |
| Time since key event | Minutes since the last goal, dismissal, substitution or formation change | Those events | Bin difference | Yellow cards are not key events | `test_minimal_transitions_and_key_events` |
| Transitions | Goals and dismissals, with team and minute | As above | Ordered list | A goal that does not change the score sign (2-0 to 3-0) is a transition but not a regime change | `test_dismissals_reduce_numbers_from_the_next_bin` |

Nothing describes intent, tactics or physical condition.

## Score and numbers effects

Football research consistently finds that teams behind in the score have more of the ball and
play further forward, while teams ahead protect their lead (for example Lago, 2009;
Lago-Peñas and Dellal, 2010). The same applies when one team has a numerical advantage after a
dismissal. Stage 3 uses only the **direction** of these effects, never a magnitude:

| After | Team expected to push | Team expected to protect |
| --- | --- | --- |
| A goal | The conceding team | The scoring team |
| A dismissal | The team with the extra player | The short-handed team |

*Push* covers: on-ball share, field tilt, shots, attacking-third entries, progressive actions,
defensive action height, pressures and high regains all rising. *Protect* covers: shots,
entries, progressive actions, defensive action height, pressures and high regains all falling.
Complementary metrics only ever appear as gains, because Stage 2 reports one team's loss as the
other team's gain.

## Contextual baselines

A Stage 2 comparison can straddle a goal or a dismissal, in which case its "shift" may simply be
the difference between two game states. Each shift is re-assessed in one of four ways:

| Kind | When | What happens |
| --- | --- | --- |
| Unchanged | No transition inside the comparison span | The Stage 2 statistic stands |
| Same regime | A transition is inside the span, at least 5 minutes from the shift | Both windows are cut back to the regime next to the boundary and the test is recomputed. The shift survives only if it stays at z ≥ 2, material, and in the same direction |
| Removed | As above, but the shift does not survive | INSUFFICIENT: the change came from mixing game states |
| Coincident | A transition is within 5 minutes of the shift | No same-regime window of useful length exists. The Stage 2 statistic is kept. If the direction is the ordinary response (table above), the shift is **context-aligned** and capped at WEAK |

The coincidence distance equals the Stage 2 merge window (5 minutes), and it is also the
shortest same-regime window allowed. It was reasoned, not tuned.

**Rejected baselines:**

* *Whole-match or same-phase averages*: they mix game states, and they cannot be computed in
  real time without future data.
* *Population baselines from other matches*: the engine sees one match, and a baseline learnt
  from generated matches would be a way of learning the generator.
* *Magnitude adjustment for score effects* (for example "a trailing team gains about 5% on-ball
  share"): there is no defensible single-match estimate, and it would require exactly the kind
  of calibrated assumption the brief forbids.
* *Workload-adjusted baselines*: rejected below.

## Workload

Event data records actions, not distance, speed or recovery, so physical condition cannot be
measured. Stage 3 reports **observable workload proxies** as context beside each candidate. They
never change an evidence level.

| Proxy | Status | Reason |
| --- | --- | --- |
| Mean minutes on the pitch of current outfield players | Accepted (context) | Directly observable exposure; substitutions reset it |
| Recent pressures per outfield player (15 minutes) | Accepted (context) | Observable defensive activity load |
| Recent on-pitch actions per outfield player (15 minutes) | Accepted (context) | Observable overall activity load |
| Any "fatigue" index or score | Rejected | Not measurable from events; naming it fatigue would overclaim |
| Workload as a baseline modifier or level input | Rejected | No defensible dose-response between event counts and any metric; it would be a tuned assumption |
| Per-player distance, sprints or speed | Rejected | Not in the observable data contract |
| Per-player pressing counts as individual load | Rejected | Pressing involvement reflects role as much as load |

Granularity is one minute; a substitute counts from the minute after coming on.

## Patterns

A pattern is a set of signals that football reasoning expects to move together. The Stage 2
shift is the core signal. Every other signal of each pattern containing it is tested once, at
the same boundary and on the same contextual windows. There is no search, so each is a single
comparison rather than a scan.

| Pattern | Football rationale | Signals (team unless marked opponent) |
| --- | --- | --- |
| Pressing intensification | A higher, more active press wins the ball higher and disrupts the opponent's passing | pressures up, high regains up, pressure regain rate up, defensive action height up, opponent pass completion down, opponent turnovers up |
| Pressing decline | A press that fades lets the opponent progress more freely | pressures down, pressure regain rate down, high regains down, opponent progressive actions up, opponent pass completion up |
| Deeper defending | A deeper block concedes territory and entries | defensive action height down, high regains down, opponent field tilt up, opponent attacking-third entries up |
| Territorial dominance | Sustained pressure in the final third pins the opponent back | field tilt up, attacking-third entries up, shots up, opponent defensive action height down |
| Attacking decline | Fewer entries and shots, territory ceded | attacking-third entries down, progressive actions down, shots down, opponent field tilt up |
| Control gain | Keeping the ball and moving it forward securely | on-ball share up, pass completion up, progressive actions up, turnovers down |
| Ball security loss | Losing the ball in open play, often to a higher opponent press | turnovers up, pass completion down, opponent high regains up |
| Wide attacking shift | More attacks through wide areas reach the final third | wide share up, attacking-third entries up, progressive actions up |

A narrower wide share has no pattern: on its own it has no clear complementary signal.

**Support and contradiction.** A signal supports when its directional statistic is at least
1.5, and contradicts when it is at most −1.0. The bar for contradiction is deliberately lower:
it should be easier to be contradicted than supported. Support at 1.5 standard errors is a
consistency check on an already-detected shift, not a new detection.

**Independence.** Strength comes from distinct independent families, never from the number of
metrics. A supporting signal is only *corroborating* when it is:

* in the core's own metric family; or
* mechanically linked to the core metric, because the two share events by construction. The
  linked pairs are: high regains and defensive action height; field tilt and attacking-third
  entries; field tilt and shots; pass completion and turnovers; turnovers and opponent high
  regains; progressive actions and attacking-third entries.

When several patterns contain the core, the one with the most independent families is
reported, with ties broken by more supports and then fewer contradictions. A pattern is
*contradicted* when it has at least one contradiction and no fewer contradictions than
independent supports.

## Persistence

A 15-minute window difference can come from one three-minute spell. The after-window is split
into three 5-minute sub-windows, each compared with the same before-window, with each metric's
minimum sample requirement divided by three.

* A sub-window **holds** at a directional z of at least 1.
* It is **against** the shift at z ≤ −1.

| Classification | Rule |
| --- | --- |
| Indeterminate | Fewer than two sub-windows measurable |
| Reversed | Any sub-window is against the shift |
| Sustained | At least two hold, including the last measured one (no decay by the window's end) |
| Transient | Otherwise (the change is concentrated in part of the window) |

A uniform change of z in the full window gives roughly z/√3 in each third, so a hold threshold
of 1 is the natural bar for a z ≈ 2 shift. `continues` reports whether the next 5 minutes still
hold, within the same regime. It is reported only and does not affect the level.

## Evidence levels and claim strength

| Level | Basis |
| --- | --- |
| Insufficient | Not unusual when compared within the same game state (baseline removed) |
| Weak | Unusual, but no independent family corroborates it; or capped |
| Moderate | Unusual, plus independent support from at least one other family, and persistence sustained or indeterminate |
| Strong | Unusual, independent support from at least two families, sustained, and no contradictions |

A candidate is capped at Weak when:

* it is context-aligned;
* it is transient or reversed; or
* its pattern is contradicted.

Each candidate carries `level_basis`, the reasons for its level in plain words.

Claim strength is ASSOCIATED when at least one independent family changed with the core, which
means two co-occurring changes beyond their baselines. Otherwise it is OBSERVED. Nothing exceeds
ASSOCIATED, and every candidate and moment model validates that ceiling.

## Ranking

Candidates are sorted lexicographically by:

1. level;
2. sustained before anything else;
3. independent families, capped at 2;
4. fewer contradictions;
5. the absolute contextual statistic.

A third family adds nothing: related metrics are correlated, so a third family is not a third
independent witness. The number of metrics never enters the rank. `rank_basis` states the
values used.

**Moments** are candidates at Moderate or above, merged per team within 5 minutes, with the
highest-ranked candidate leading.

## Traceability

Every candidate lists the core metric's events in both the Stage 2 windows and the contextual
windows, plus the after-window events of supporting signals. An empty list fails validation. The
context note's key events are temporal context only and never count as evidence.

## The six questions

| Component | Concept | Observable evidence | Why existing metrics are insufficient | Failure mode addressed | Test | Generator-decoding risk |
| --- | --- | --- | --- | --- | --- | --- |
| Same-regime baseline | Compare like with like | Goals, dismissals, metric series | A Stage 2 window can mix game states | Shifts that only reflect a goal or red card | `test_baselines.py` | Low: uses only observable transitions and a published football effect direction |
| Context alignment | Ordinary response to a transition | Transition plus shift direction | Stage 2 has no notion of expected responses | Promoting score effects to moments | `test_coincident_goal_marks_the_ordinary_response_as_aligned` | Low: direction only, from football literature |
| Patterns | Football changes move several families | Other metric series at the same boundary | One shift is one metric | Isolated metric noise | `test_patterns.py` | Medium: patterns could mirror generator levers. They are defined from football reasoning, and mechanically linked metrics do not count |
| Persistence | Sustained versus burst | Sub-window comparisons | A 15-minute mean hides bursts | Spells of pressure flagged as changes | `test_persistence.py` | Low |
| Levels and ranking | Explainable uncertainty | The above | Stage 2 ranks by summed z | Many weak metrics outranking one strong change | `test_strength.py` | Low |
| Workload context | Exposure and activity load | Minutes, pressures, actions | Not represented | Readers mistaking late-match drift for tactics | `test_workload.py` | Low, because it never affects levels |

## Limitations

* **Stage 3 cannot recover what Stage 2 missed.** It only re-grades Stage 2 shifts, so recall at
  Weak or above can never exceed Stage 2 evidence recall.
* **Score effects build gradually.** A same-regime comparison 5 or more minutes after a goal
  still partly contains a developing score effect.
* **Context alignment cannot separate a planned response from an ordinary one.** A team that
  deliberately drops deeper after scoring looks the same as one doing what teams usually do.
  Stage 3 caps both at Weak; telling them apart is a Stage 4 investigation question.
* **Support thresholds are single-comparison bars.** With several signals per pattern and
  several patterns per core, some chance support is expected. Requiring independent families
  limits, but does not remove, this.
* **One-minute resolution** throughout.
* **Workload proxies are descriptive only.** They are not validated against any physical
  measure.

## Rejected approaches

* **"Sustained alone reaches Moderate."** This was the first design. On development seeds it
  increased control moment density (6.6 per match against Stage 2's 5.5). The reason: about 60%
  of control candidates are sustained, because a shift significant over 15 minutes usually
  holds in each third. Persistence is now a gate, not a route to Moderate.
* **Counting supporting metrics.** Three metrics from related families are not three witnesses.
  Strength counts independent families, capped at two.
* **Suppressing context-aligned shifts entirely.** They remain observable evidence at Weak. A
  reader should still see that a team dropped deeper after scoring.
* **A fatigue proxy**, and workload as a level input: see Workload.
* **Scenario-informed thresholds or patterns**: never considered. A static test forbids scenario
  IDs, synthetic club names and generator vocabulary in analytics source
  (`tests/architecture/test_no_scenario_decoding.py`).
