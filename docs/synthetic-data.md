# Synthetic data: two worlds, one generator

Status: **implemented (Stage 1b, generator `0.1.0`)**. Code lives in `src/matcheyes_synth/`.
Calibration evidence: [realism-report.md](realism-report.md).
Acceptance rules: [ADR-0007](decisions/0007-generator-calibration.md).

| Module | Role |
| --- | --- |
| `rng.py`, `seeds.py` | Named random streams; development versus held-out seeds |
| `squads.py` | Persistent squads and hidden player attributes, keyed by club and generator version |
| `state.py` | Hidden-state formula and its drivers |
| `engine.py` | Possession-by-possession simulator and scripted-event steering |
| `generator.py` | `generate_match(spec, seed)`: validation, `GenerationError`, `GroundTruth` |
| `io.py`, `__main__.py` | Output layout, manifest, CLI |
| `realism.py`, `effects.py` | Realism bands; counterfactual-twin effect sizes |

## Two logically separate worlds

```
                     ┌────────────────────── matcheyes_synth (hidden) ───────────────────────┐
 ScenarioSpec + seed │  league styles + interventions + background dynamics → hidden state   │
                     │                    │                                                   │
                     │                    ▼                                                   │
                     │             event simulation ─────────────► GroundTruth (answer key)   │
                     └────────────────────┬──────────────────────────────────┬───────────────┘
                                          ▼                                  │
                          OBSERVABLE MATCH DATA                     HIDDEN GROUND TRUTH
                       observable/<match_id>/...                  truth/<match_id>/...
                                          │                                  │
                                          ▼                                  ▼
                              matcheyes (engine)  ──── insights ────►  matcheyes_eval
```

| | Observable match data | Hidden ground truth |
| --- | --- | --- |
| Contents | Team sheets, events (see [data-model.md](data-model.md)) | Club styles, player attributes, hidden team-state timeline, scripted events, planted interventions, expected insights, decoys |
| Code | `matcheyes.domain` | `matcheyes_synth.truth`, `.league`, `.scenarios` |
| Consumer | MatchEyes engine | Evaluation only (`matcheyes_eval`) |
| Storage root | `data/generated/observable/<match_id>/` | `data/generated/truth/<match_id>/answer_key.json` |

**How separation is enforced** ([ADR-0005](decisions/0005-observable-and-hidden-worlds.md)), all
tested in `tests/architecture/test_truth_separation.py`:

1. **Source:** `matcheyes` may not reference `matcheyes_synth` or `matcheyes_eval`, checked by a
   text scan that also catches string-based imports. `importlib` is banned in the engine by lint.
2. **Runtime:** a clean interpreter imports every engine module, runs ingestion, and asserts no
   hidden module was loaded. This catches obfuscated dynamic imports.
3. **Artefact:** the wheel contains only `matcheyes`. CI builds it and checks its contents. The
   deployed engine cannot import hidden code because the code isn't there.
4. **Schema:** observable and hidden schemas share only reviewed structural field names (IDs,
   clock, `formation`, `kind`).
5. **Ingestion:** closed schemas reject events or metadata carrying any hidden field.
6. **Identifiers:** `match_id` and observable paths are opaque hashes (`m-3f9a…`). Nothing the
   engine can see encodes the scenario name. The `match_id` → scenario mapping lives only in the
   truth root.
7. **Deployment (Stage 8):** the engine container mounts only the observable root.

## The Meridian League (fictional)

Eight invented clubs. Observable identity: name, short name, colours, ground, default
formation. Hidden: style vector, squad quality, squad stamina.

| Club | Formation | Hidden identity |
| --- | --- | --- |
| Kestrel Bay FC (KBY) | 4-3-3 | High press, possession, high line |
| Northmoor Athletic (NMA) | 4-4-2 | Direct, compact mid-block |
| Saltmarsh Rovers (SMR) | 4-2-3-1 | Wide, high tempo |
| Thornvale City (TVC) | 4-2-3-1 | Balanced, adaptable |
| Elderfield United (EFU) | 3-5-2 | Counter-attacking, wing-backs |
| Brightwater Albion (BWA) | 4-3-3 | Patient possession, low press |
| Corran Valley (CRV) | 4-1-4-1 | Relentless press, thin squad, fades late |
| Redmarsh Town (RMT) | 5-3-2 | Deep block, counters |

Player names are generated from seeded, invented name parts. No real names, crests or kits.

## Hidden-state model

Each team has a `HiddenTeamState` at every moment, with all dimensions in [0, 1]:
`press_intensity`, `defensive_line`, `tempo`, `directness`, `width`, `risk_appetite`,
`execution`.

```
state(team, t) = clamp( baseline_style(team)
                      + Σ planted interventions active at t  × ramp(t)
                      + game_state_adjustment(score difference, minute)     ← background
                      + fatigue_adjustment(minute, squad stamina, press load) ← background )
```

- **Planted interventions** (`Intervention`) are the causes under test. They have a kind, a
  trigger (manager instruction / game state / fatigue / observable event), a start, a linear
  ramp and a `StateDelta`.
- **Background dynamics** exist in every match, including controls: trailing teams take more
  risk, everyone tires. They are real causes too, so they're recorded in the answer key.
  Attributing a change to them correctly is not a false positive.
- The answer key stores the resulting state as a timeline of `TeamStateSegment`s, with the
  `drivers` responsible for each segment. Segments are contiguous, open on every minute boundary
  and on every state-changing event (goal, dismissal, substitution), and last at most one minute.
- **Onsets:** a cause triggered by an event begins at the later of its scheduled start and the
  emitted trigger. The actual onset is recorded in `GroundTruth.intervention_onsets`.
- **Game state** (`state.py`): urgency = clamp((minute − 30) / 60), scaled by 0.6 for a one-goal
  margin. Trailing teams gain risk, line height, press, directness and tempo. Leading teams lose
  risk, line height, press and tempo.
- **Fatigue:** each player's fatigue rises per minute by (0.0045 + 0.0055 × team press) ×
  (1.5 − stamina) and recovers 20% at half-time; substitutes start fresh. Mean on-pitch fatigue
  above 0.25 erodes press, execution and tempo.

Player attributes (`passing`, `finishing`, `pace`, `stamina`, `pressing`, `composure`) are
hidden and shape action outcomes.

## Scenario catalogue

Implemented in `src/matcheyes_synth/scenarios.py`. Every scenario is validated on
construction: references resolve, every effect window starts no earlier than its cause, and
decoys never overlap a planted effect for the same team.

| ID | Planted cause(s) | Expected conclusion | Tests |
| --- | --- | --- | --- |
| S01 control_balanced | none | No planted shift. Background effects only. | False-positive rate |
| S02 press_surge | Away press +, line + at 60' | Momentum shift to away side, caused by the press | Core causal attribution |
| S03 game_state_deep_block | Home scores 52'; drops deep 54' | Home reorganised; opponent's territory is a score effect | Separating score effects from quality |
| S04 fatigue_press_decay | Home execution decays from 68' | Presses continue but stop winning the ball | Effort versus effectiveness |
| S05 red_card_reorganisation | Red card 38'; shape change 40' | Reorganisation caused by the dismissal | Observable trigger plus hidden response |
| S06 impact_substitution | Winger on at 63'; width + | Player impact on wide progression | Player-level attribution |
| S07 against_the_run_of_play | Forced counter goal 71', no state change | Goal against the run of play (descriptive only) | **Decoy:** no causal shift for the scorer |
| S08 coincidence_decoy | Substitution 58' + forced turnover noise 58.5–63' | Nothing causal | **Decoy:** proximity is not causation |
| S09 halftime_formation_shift | Back five → back four at HT | Territorial and pressing shift caused by the formation | Formation change effects |
| S10 comeback_multi_cause | 2–0 down; press at 58' (primary), sub at 62' and opponent fatigue at 70' (secondary) | Momentum shift with ranked causes | Primary versus secondary attribution |

Expected insights cite **mechanism signals**: a controlled vocabulary of observable signatures
such as `high_recoveries_up` or `territory_shift`. Each maps to a Stage 2 metric, and the
mapping lives in `matcheyes_eval/scoring.py`, never in the engine. That makes "did the
explanation cite the right evidence?" objectively scorable.

An insight may also list `supporting_mechanisms`. These are plausible side effects that are not
reliably detectable from observable data, and they are **never scored**. S02's
`opponent_pass_completion_down` is one: about −0.4 percentage points against its counterfactual
twin, below match noise (ADR-0007 approval decision). A mechanism cannot be both scored and
supporting.

## Generator specification (implemented)

### Interface

```python
generate_match(spec: ScenarioSpec, seed: int) -> GeneratedMatch
# GeneratedMatch.observable: ObservableMatch   (validated by validate_match before return)
# GeneratedMatch.truth:      GroundTruth
```

### Determinism

- The same `(spec, seed, generator_version)` gives byte-identical output, verified by hashing.
- Named random sub-streams (`squad`, `attributes`, `actions`, `outcomes`, `timing`, `noise`,
  `scripted`), each seeded from `sha256(seed, stream)`. Changing one part of the model doesn't
  shift randomness everywhere else.
- Standard library `random` only. No wall-clock time, no environment-dependent state.

### Simulation loop (possession-phase state machine)

1. **Kick-off** at the start of each period and after goals (by the conceding team).
2. **In possession:** pick the next action (pass / carry / take-on / shot) from probabilities
   conditioned on zone, `tempo`, `directness`, `width`, `risk_appetite` and the game state.
3. **Out of possession:** emit `pressure` with probability rising with `press_intensity` and
   with how high the ball is relative to the defending team's `defensive_line`.
4. **Resolve the outcome** from player attributes, `execution`, pressure and fatigue (pass
   success, duel results, shot outcome). The true scoring probability of each shot is hidden.
5. **Turnovers** emit the matching defensive event (tackle / interception / recovery /
   clearance / block). Ball out of play emits the right restart pass kind.
6. **Discipline and changes:** fouls and cards at low base rates. Three to five substitutions
   per team from about 55'.
7. **Advance the clock** by sampled action durations scaled by `tempo`. Stoppage time is
   sampled per period.
8. **Scripted events** are steered: from `at`, the next suitable possession is shaped to
   produce the event within a tolerance. Tolerances are goal 120 s, red card 180 s,
   substitution 300 s (at the next dead ball) and formation change 60 s (emitted in open play).
   The emitted event IDs are recorded in `GroundTruth.resolved_events`. A goal is steered by
   making the scoring side's next possession progress cleanly and the opponent's next pass
   fail; a red card by a foul from the scripted team outside its own box. Missing a tolerance
   raises `GenerationError`.

**Mechanisms linking hidden state to observable events** (the parts that make planted causes
visible):

- *Line of engagement:* a team presses only up to 25 + 60 × `defensive_line` metres from its
  own goal. Beyond that, pressing falls away sharply. A deep block leaves the opponent's half
  unpressed.
- *Press effectiveness:* pressured passes lose accuracy, and tackles are won, in proportion to
  the pressing team's `execution` and `press_intensity`. A fatigued press still presses but wins
  the ball less.
- *Time on the ball:* pressed players recycle possession, while unpressed players carry further
  and pass forward more.
- *Width:* `width` raises the share of passes into wide channels and the involvement of wide
  players.
- *Box caution:* defenders commit far fewer fouls inside their own penalty area.

### Outputs

```
data/generated/
  observable/<match_id>/match.json, events.jsonl
  truth/<match_id>/answer_key.json        GroundTruth
  truth/manifest.json                     match_id → scenario, seed, version, sha256 of files
```

`data/generated/` is git-ignored except for a small curated set used by tests and demos.
Files are written with `\n` line endings on every OS, so manifest hashes are reproducible across
machines.

The CLI (`python -m matcheyes_synth`) has three commands: `generate` (a scenario at a seed, or the
whole catalogue over a seed split), `realism` (band table) and `effects` (counterfactual-twin
effect sizes).

### Realism acceptance

Each generated match must pass `validate_match` (hard requirement) and fall inside realism
bands. **Provisional bands, to be checked against cited public benchmarks when the generator
is calibrated:**

| Metric | Band |
| --- | --- |
| Events per match | 1,400 – 2,400 |
| Passes per team | 300 – 650 |
| Pass completion per team | 70 – 90 % |
| Shots per team | 4 – 25 |
| Goals per match (mean over seeds) | 2.3 – 3.3 |
| Fouls per match | 14 – 32 |
| Possession share per team | 30 – 70 % |

### Evaluation hygiene (so ground truth measures, rather than flatters, MatchEyes)

- **No cherry-picking:** each scenario is evaluated over many seeds (target 20) and the full
  distribution is reported, not a chosen example.
- **Dev/test seed split:** thresholds are tuned only on development seeds. Reported metrics
  come from held-out seeds that were never used for tuning.
- **No circular acceptance:** a match is accepted on invariants, realism and the scripted
  events resolving. Whether MatchEyes detects the planted cause is never an acceptance rule.
- **Independence:** detectors are written against observable concepts (Stage 2 metrics), not
  generator parameters. Decoys and controls measure false positives.
- **Planted effects must be detectable in principle:** each scenario's primary observable proxy
  is compared with its counterfactual twin (same seed, interventions removed) over 20
  development seeds. A cause that leaves no observable trace is a generator bug, not an engine
  failure. Results: [realism-report.md](realism-report.md#planted-effects-are-they-detectable-in-principle).

### Real-time replay (specified now, built in Stages 3/9)

A replayer streams `events.jsonl` with a speed factor and can apply a **transport perturbation
profile** (jitter, duplicates, late or out-of-order delivery). Perturbations are logged
separately from both worlds, to test idempotency and ordering without changing the facts.
