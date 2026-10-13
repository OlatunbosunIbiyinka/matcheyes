# ADR-0016: Snapshot-anchored team statistics

- Status: Proposed (Stage 10.3 step 1: contract and tests; step 2: serving implemented and tested,
  with the evaluation's analytics reused, both performance gates met; awaiting approval; frontend
  not started)
- Date: 2026-10-10
- Stage: 10.3

## Context

The match centre ([ADR-0014](0014-broadcast-cue-contract-and-live-presentation-surface.md), Stage
10.2 timeline) shows moments, verified insights, revisions and retractions, but no team
statistics. Viewers expect a comparison (shots, share of the ball, territory, passing, pressing)
and its development over the match.

Stage 2 ([ADR-0008](0008-deterministic-analytics-design.md)) already computes team summaries, and
every canonical snapshot ([ADR-0013](0013-snapshot-anchored-insight-lifecycle.md)) carries its own
event prefix. Each snapshot evaluation runs `analyse_match` on that prefix and then discards
everything except the insights.

Forces:

* The browser must not calculate football numbers, and the AI layers must not become a second
  source of them.
* Statistics must not be presented as explanations of insights, or insights as their cause.
* A statistic shown at minute *m* must stay true later. The stream is append-only, so a published
  value that later events would change would be misleading.
* Invalid and incomplete data must never look like a value.

## Investigation: retrospective revision and look-ahead

Each Stage 2 series was computed on every snapshot prefix and compared with the full match's
series restricted to the same minute bins. The matches were the 40-minute lifecycle fixture
(39 snapshots), the complete S03 match (98) and the demo match `m-b71a80f94d25` (98).

| Measure | Revised by later events | Why |
| --- | --- | --- |
| `turnovers` | yes: 4, 9 and 5 snapshots | the possession open at the end of a prefix closes as `PERIOD_END`; later events turn it into `LOST_IN_PLAY` |
| `pressure_regain_rate` | not observed; possible by construction | a pressure "wins the ball" if the pressing team controls it within 5 s, so a pressure in the last 5 s of a prefix can change later |
| possession-based fields (`possessions`, `mean_possession_s`, `passes_per_possession`, `possessions_reaching_attacking_third`, `own_half_turnovers`) | possible by construction | the last possession of a prefix is truncated |
| the eight measures below | never | each counts single events by their own type, location and outcome |

`ppda` is not a series. It is a ratio of two counts of single-event predicates (opponent open-play
passes in their first 60 %, the team's tackles, interceptions and fouls from 40 %), so it cannot
be revised either. It is undefined when the denominator is zero.

## Decision

### Measures (`broadcast/stats.py`, `MEASURES`)

Eight existing `TeamSummary` fields, cumulative over the snapshot's prefix, with their existing
definitions ([metrics.md](../metrics.md)):

| Key | Label | Unit | Sample shown | Minimum sample |
| --- | --- | --- | --- | --- |
| `shots` | Shots | count | – | – |
| `on_ball_share` | Share of on-ball actions ("Not possession time.") | share | on-ball actions by both teams | 60 |
| `field_tilt` | Field tilt (share of on-ball actions in the attacking third) | share | attacking-third on-ball actions by both teams | 20 |
| `pass_completion` | Open-play pass completion | share | the team's open-play passes | 40 |
| `progressive_actions` | Progressive passes and carries | count | – | – |
| `high_regains` | High regains (within 40 m of the opponent's goal) | count | – | – |
| `pressures` | Pressures | count | – | – |
| `ppda` | PPDA (opponent open-play passes per defensive action) | ratio | – | defined |

The minimum samples are the existing `MetricSpec.min_samples`, which is the minimum denominator
Stage 2 uses for windows. Excluded: goals (the score has one source, the moment cues), xG, shots
on target, clock-time possession, momentum, and every measure in the revision table above. No
rolling windows in this stage.

The values are computed by `team_analytics`, which calls the same Stage 2 functions as
`analyse_match` (possessions, series, team summaries) without change-point detection. Tests pin
equality with `analyse_match` on every snapshot.

### Validity and status semantics

Statistics follow data and analytics validity only, independently of reasoning, verification or
audit on the same snapshot. The builder takes only the `Snapshot` (header and prefix), never a
`SnapshotEvaluation`.

| `status` | When | `reason` | `teams` |
| --- | --- | --- | --- |
| `valid` | the prefix passes `validate_prefix` and the analytics complete | `null` | both teams, team-sheet order (home, away) |
| `invalid` | `validate_prefix` reports violations: the same rule and codes that make the lifecycle snapshot `INVALID` | sorted violation codes, `", "`-joined, e.g. `player_not_on_pitch` | `[]` |
| `unavailable` | the analytics raised | `analytics_error` (never exception text) | `[]` |

Per value, `status` is `ok` or `insufficient_sample`. A value is `insufficient_sample` when its
sample is below the measure's minimum, or when it is undefined (no denominator). It then has
`value: null` and `display: "Not enough data"`; a proportion keeps its `sample`.

Invariant violations are checked per event against the preceding events, so once a prefix is
invalid every later prefix is too. Statistics stop for the rest of that replay; a test pins this
and its agreement with the lifecycle outcomes.

Data gaps (`DATA_INCOMPLETE`) create no snapshot past the watermark, so they produce no statistics
message. The surface keeps showing the last snapshot's statistics, labelled with that snapshot's
minute, and the existing data-incomplete status cue explains why.

### Display formatting (server only)

`display` is the only text a surface shows for a value: whole-number percentages (`"59%"`), one
decimal for PPDA (`"5.3"`), integers for counts (`"5"`). Rounding is half-up from the value's
shortest decimal form. A surface may use `value` for geometry (bar length, line position) only.
The contract validator rejects any `display` that is not the formatted `value`.

#### Complementary shares (approved rule; implemented in step 2)

Independent rounding can make the two teams' displays total 101 % or 99 %: field tilt did so on 1
of 98 snapshots in both the demo match and the complete S03 match (on-ball share on none). For
measures whose Stage 2 definition is complementary (`MetricSpec.complementary`), the displays must
total exactly 100 %:

* **Scope.** `on_ball_share` and `field_tilt` only. Both teams share one pooled denominator (every
  qualifying action counts in both teams' denominators and in exactly one numerator), so the
  shares sum to 1. That also means both teams always have the same sample, so both values are
  shown or both are "Not enough data". `pass_completion` is not complementary and keeps
  independent rounding.
* **Rule.** Largest remainder, with a tie going to the larger share. For two teams this is the
  same as: round the larger share half-up (from its shortest decimal form) to a whole percent,
  then display the smaller as 100 minus that. Equal shares (0.5 and 0.5) display 50 % and 50 %.
  The smaller display comes from the larger one, not from the smaller value, so a floating-point
  sum such as 0.9999999999 cannot produce 99 %.
* **Values.** `value` is never altered; only `display` is allocated. For the smaller share,
  `display` can then differ from its own independent rounding by one point.

| Values (home, away) | Independent rounding | Allocated |
| --- | --- | --- |
| 0.505, 0.495 | 51 % + 50 % = 101 % | 51 %, 49 % |
| 0.125, 0.875 | 13 % + 88 % = 101 % | 12 %, 88 % |
| 0.585, 0.415 | 59 % + 41 % | 59 %, 41 % |
| 0.584, 0.416 | 58 % + 42 % | 58 %, 42 % |
| 0.5, 0.5 | 50 % + 50 % | 50 %, 50 % |
| 0.995, 0.005 | 100 % + 1 % = 101 % | 100 %, 0 % |
| 1.0, 0.0 | 100 % + 0 % | 100 %, 0 % |
| pass completion 0.505, 0.495 (not complementary) | 51 %, 50 % | unchanged: 51 %, 50 % |

Implementation (`stats.py`), with the wire schema unchanged:

1. `COMPLEMENTARY` is derived from `MetricSpec.complementary` (`on_ball_share`, `field_tilt`), and
   the pure `complementary_displays(home, away) -> (str, str)` sits beside `display`.
2. `snapshot_stats` builds both teams, then replaces the two displays of each complementary
   measure that has values with the allocated pair. Values are not touched.
3. The validator checks every other measure as before. It checks complementary measures as a pair:
   both shown or both insufficient, values summing to 1 (within 1e-9), and displays equal to
   `complementary_displays` of the values.

Tests (`tests/broadcast/test_stats_complementary.py`): the table above, including the 101 % cases
and the mirrored ones; every pooled split `part/whole` for wholes up to 240 totals 100 % with the
larger share rounded as `display` rounds it; a floating-point sum just below 1; every real snapshot
of the demo and complete S03 matches totals exactly 100 % with Stage 2 values unchanged (field
tilt is re-allocated on at least one snapshot of each); pass completion still rounds
independently; the validator rejects a 101 % pair, one share shown with the other insufficient,
and shares that do not sum to 1.

### Wire schema: SSE event `stats`

A new, separate SSE event type. The cue contract, cue IDs, timeline IDs and `BROADCAST_VERSION`
are unchanged. `data` is `stats_message(edition, stats)`, compact JSON with keys in this order:

```json
{
  "edition": 0,
  "stats_version": "0.1.0",
  "match_id": "m-b71a80f94d25",
  "snapshot_id": "snap-00804-2c0ae5d249cceead",
  "watermark": 804,
  "period": 1,
  "minute": "40'",
  "as_of": "41'",
  "clock_ms": 2400000,
  "status": "valid",
  "reason": null,
  "measures": [
    {"key": "shots", "label": "Shots", "unit": "count", "min_sample": 0,
     "sample_of": null, "note": null},
    {"key": "on_ball_share", "label": "Share of on-ball actions", "unit": "share",
     "min_sample": 60, "sample_of": "on-ball actions by both teams",
     "note": "Not possession time."}
  ],
  "teams": [
    {"team_id": "saltmarsh", "values": {
      "shots": {"value": 5, "display": "5", "sample": null, "status": "ok"},
      "on_ball_share": {"value": 0.5853658536585366, "display": "59%", "sample": 574,
                        "status": "ok"},
      "ppda": {"value": 5.3125, "display": "5.3", "sample": null, "status": "ok"}}},
    {"team_id": "redmarsh", "values": {
      "shots": {"value": 2, "display": "2", "sample": null, "status": "ok"},
      "on_ball_share": {"value": 0.4146341463414634, "display": "41%", "sample": 574,
                        "status": "ok"},
      "ppda": {"value": 10.5, "display": "10.5", "sample": null, "status": "ok"}}}
  ]
}
```

(`measures` and `values` are abridged here; the message always carries all eight, in catalogue
order.) The snapshot fields `snapshot_id`, `minute`, `as_of`, `period`, `clock_ms` and `watermark`
are those of the clock tick for the same snapshot. Every message carries the measure catalogue, so
a resumed stream needs no other request.

### Step 2 acceptance criteria: ordering and replay

Per surface, for each ingest that closes canonical snapshots: the `clock` tick (unchanged), then
one `stats` message per new snapshot in snapshot order, then that ingest's cues (unchanged), in
compiler order. Step 2 is accepted only when tests show all of the following:

1. **One per snapshot.** Every surface (both audiences, each with no club or either club) carries
   exactly one `stats` message per canonical snapshot of the edition. The `snapshot_id`s are those
   of `engine.state.snapshots`, in order, with no repeats.
2. **Ordering.** For each snapshot, its `clock` tick precedes its `stats` message, which precedes
   that snapshot's cues: every cue whose `snapshot_id` is that snapshot. Status cues before the
   first snapshot have no `snapshot_id` and keep their current position.
3. **Contiguous IDs.** `stats` messages take ordinary stream indices. Numbered message IDs remain
   `edition:index`, contiguous from `edition:0` across `clock`, `stats` and `cue`. The unnumbered
   `edition` and `end` events stay first and last, as now.
4. **Resume without duplicates.** Reconnecting with `Last-Event-ID: e:i` delivers exactly the
   messages after `i` of edition `e`, so no `stats` message already delivered is sent again and
   none is skipped. Resuming against a newer edition starts that edition from its first message,
   as now.
5. **Edition restart.** A looping replay's next edition rebuilds an identical statistics history:
   the same `snapshot_id`s, the same payloads apart from `edition`, in the same order. Every
   message carries its own edition and no edition contains a point of another.
6. **Cues unchanged.** The cues of each surface, filtered from the stream, equal the offline
   `compile_timeline` cue for cue, byte for byte, in the same order. Timeline IDs, `/timeline`
   responses and the cue schema digest pinned in `test_stage10_stats.py` are unchanged.
   `BROADCAST_VERSION` stays 0.1.0.
7. **Identical across surfaces.** The `stats` payloads of the six surfaces are byte-identical.
8. **Existing tests unchanged.** The current stream, resume, bounded-stream, live-replay and
   explain tests pass without modification.

`GET /matches/{id}/stats` is deferred until a consumer needs it.

### Step 2 implementation (`api/live.py`)

* `LiveMatch(..., stats=True)` publishes statistics; `stats=False` exists only to measure their
  cost. In `_replay`, when an ingest closes snapshots, each new snapshot's statistics are built
  outside the lock and the payload is shared by all six surfaces. Each surface appends the clock
  tick, then the `stats` messages in snapshot order, then its cues.
* Each snapshot is rebuilt from the engine's own log: its lifecycle header plus
  `log.events()[:watermark]`. Before that, `prefix_matches` checks that the log still holds that
  prefix (the watermark lies within the contiguous prefix and `log.digest(watermark)` equals the
  header's `log_digest`). If it does not, or anything raises, the snapshot is served as
  `unavailable` / `analytics_error` from its header alone, with no values and no exception text.
  Reasoning outcomes are never read.
* `StatsCache` belongs to the `LiveMatch`. It maps `snapshot_id` to `SnapshotStats` (no edition),
  is bounded by `STATS_CACHE_LIMIT` (= `EVALUATION_CACHE_LIMIT`, 400; 0 disables storing), counts
  builder calls and hits, rejects a built value whose `snapshot_id` differs from the key, and is
  looked up before the snapshot is assembled, so warm editions do no statistics work beyond the
  lookup.
* **Reuse of the evaluation's analytics (Option B, approved after the cold gate failed).** The
  evaluator already validates each snapshot's prefix and runs Stage 2 on it. `lifecycle/evaluate.py`
  now has `evaluate_and_analyse`, which returns the unchanged `SnapshotEvaluation` together with
  a `SnapshotAnalytics` (snapshot ID, log digest, validity report and, if Stage 2 completed, the
  team summaries and series). `evaluate_snapshot` is a thin wrapper that returns only the
  evaluation, so its signature, the evaluation contract, reconciliation, audit and recordings are
  unchanged. Stage 2 results are kept when reasoning fails after them.
  The default `CachingEvaluator` (reference pipeline) keeps, beside each evaluation, the validity
  report, the summaries and only the pooled series the statistics read (`POOLED_SERIES`), under the
  same key and bound, evicted together. `_stats` reuses them through
  `snapshot_stats_from(snapshot, validity, summaries, series)` only if they belong to this
  `LiveMatch`'s evaluator, their snapshot ID and log digest equal the header's, and they are either
  invalid or carry Stage 2 results. Otherwise, including recorded backends, other evaluators,
  evicted entries and Stage 2 failures, the full build runs as before. `snapshot_stats` is now
  validity and analytics followed by `snapshot_stats_from`, so both paths share all formatting,
  allocation and validation. Tests: `tests/api/test_stats_reuse.py`.
* Tests: `tests/api/test_live_stats.py` (criteria 1–8, cache, failure paths, resume through the
  real server) and `tests/architecture/test_stage10_serving.py` (the frontend listens to no
  `stats` event and reads no statistics field; the live driver reaches analytics only through
  `broadcast.stats`).

### Time cursor (for step 3)

Selecting a timeline event selects the snapshot that recorded it (its moment cue's
`snapshot_id`). The comparison then shows that snapshot's statistics, labelled "as of" its minute.
This is a time cursor only: the context-only disclaimer stays, and no wording links a statistic to
an event or insight.

### Boundaries

* `broadcast/stats.py` imports only domain, `ingestion.invariants`, analytics and
  `lifecycle.snapshot`. The existing purity checks (no clock, randomness, network, persistence or
  reasoning model) apply to it.
* No reasoning layer (agents, orchestration, personalization, lifecycle) imports `broadcast`.
* The statistics schema carries no hidden vocabulary, event IDs or insight fields (verdict,
  storyline, narrative, claims, evidence, explanation).

## Performance

### Step 2 acceptance gate

* **What is measured.** Full-speed (`speed=0`) six-surface `LiveMatch` replays of the demo match
  `m-b71a80f94d25` with the reference evaluator, timed by `LiveMatch.replay_seconds` (wall time from
  the first ingest to `end`). Two figures are recorded: the **cold** first edition (empty
  evaluation and statistics caches) and **warm** later editions (caches filled).
* **Baseline and integrated.** Both run with the same code, fixture, machine and method. The only
  difference is whether statistics are published: a constructor switch in step 2, default on.
  Runs alternate baseline and integrated, at least five of each, in fresh processes, with no other
  MatchEyes server running.
* **Gate.** The median integrated replay time is at most 1.10 × the median baseline time, for the
  cold edition and for warm editions separately. Isolated builder timings never count towards the
  gate.
* **Cache behaviour.** Reported per replay: builder calls, cache hits and misses. Required: exactly
  one builder call per distinct `snapshot_id` per match across all editions (warm editions are
  all hits). The cache is bounded like the evaluation cache.
* **Cache correctness.** The key is the `snapshot_id`, a content address of the match, watermark,
  minute, prefix digest and team-sheet digest, and each `LiveMatch` owns its own cache. Cached
  values carry no edition (the edition is added when the message is formed). Tests: a cached
  payload's `snapshot_id` equals the key; replaying a different prefix (corrupted fixture) never
  returns a cached valid payload; messages of edition *n* carry `edition: n` only; payloads equal
  across editions.
* **Report.** The step 2 report gives exact commands, fixture, Python and OS versions, CPU count,
  per-run timings, medians and spread, and the cache counters.

### Step 2 gate result: cold fails, warm passes

Protocol as above: demo match `m-b71a80f94d25`, `speed=0`, six surfaces, reference evaluator,
`LiveMatch.replay_seconds`; one process per run, each running a cold edition and three warm ones,
variants alternating. No MatchEyes server was running. Windows 11 (10.0.22631), Python 3.13.0,
Intel64 Family 6 Model 142, 4 logical CPUs. Every integrated run published 98 `stats` messages
for 98 snapshots, all `valid`, with no failures. The cached runs made 98 builder calls and 294
hits (warm editions are all hits). The cache-disabled runs made 392 calls and no hits.

| Set | Variant | Runs | Cold median / mean / max (s) | Warm median / mean / max (s) |
| --- | --- | --- | --- | --- |
| 1 | baseline | 5 | 25.54 / 24.92 / 30.31 | 5.07 / 5.94 / 11.31 |
| 1 | integrated | 5 | 30.16 / 28.90 / 34.70 | 4.61 / 4.98 / 6.78 |
| 1 | integrated, cache disabled | 5 | 23.72 / 29.48 / 50.57 | 8.01 / 9.39 / 19.28 |
| 2 | baseline | 6 | 19.96 / 22.83 / 35.05 | 4.56 / 4.61 / 6.02 |
| 2 | integrated | 6 | 23.45 / 23.71 / 28.33 | 4.91 / 4.90 / 6.37 |

* **Cold: fails.** Integrated / baseline median is 1.181 (set 1) and 1.175 (set 2), against a
  limit of 1.10. Timed inside the integrated runs of set 2, the statistics work on the cold
  edition was 3.15–4.24 s (median 3.38 s), about 17 % of the cold baseline, which agrees with the
  ratio. The machine is noisy (cold baselines range from 18.5 s to 35.1 s), but both sets and the
  direct timing agree.
* **Warm: passes.** The ratio is 0.911 (set 1) and 1.076 (set 2). Statistics work on a warm
  edition was about 1 ms (cache lookups), so the difference between the variants is noise.
* **Cache disabled.** Every edition rebuilds all 98 snapshots: warm medians of about 8 s against
  about 5 s, which is why the cache is required.

### Gate result after reusing the evaluation's analytics: both met

The same runner and protocol: 10 rounds in fresh processes with a seeded random variant order,
at High process priority (applied equally to every variant), on the same machine. This run was
much quieter: the cold baseline ranged 17.69–18.75 s.

| Variant | Cold median / mean / min–max (s) | Warm median (per run) / min–max (s) | Statistics work, cold / warm edition |
| --- | --- | --- | --- |
| baseline | 18.12 / 18.11 / 17.69–18.75 | 4.33 / 4.19–7.22 | — |
| integrated | 17.89 / 18.15 / 17.57–19.80 | 4.35 / 4.16–4.55 | 0.038 s / 0.004 s |
| integrated, cache disabled | 17.98 / 18.04 / 17.42–18.72 | 4.36 / 4.19–7.17 | 0.039 s / 0.041 s |

* **Cold: met.** −1.3 % (median ratio 0.987, bootstrap 95 % interval 0.977–1.017); paired by round,
  median −0.8 %. The statistics work on the cold edition fell from about 3.4 s to 0.04 s.
* **Warm: met.** +0.4 % (cache on) and +0.6 % (cache off): within noise.
* Counters are unchanged: 98 snapshots, 98 valid `stats` messages, 98 builds (all reusing the
  evaluation) and 294 hits; 392 builds and no hits with the cache disabled.

The other options listed below were not needed.

### Measurements before step 2 (isolated; not evidence for the gate)

Windows 11 (10.0.22631), Python 3.13.0, 4 logical CPUs. A MatchEyes server (speed 20) was running
at the same time, which adds noise.

| Measurement | Result |
| --- | --- |
| Baseline replay, demo match, reference evaluator: cold edition, then three warm | 21.33 s; 4.61, 5.12, 4.75 s |
| Builder over the demo match's 98 snapshots, uncached | 3.5 s and 5.0 s on two runs |
| Builder breakdown (5.0 s run) | `validate_prefix` 1.05 s, possessions 0.96 s, series 1.89 s, summaries 1.19 s |

### Known limitation: the cold gate is at risk

The uncached builder costs 3.5–5.0 s against a cold baseline of about 21 s, which is 16–24 %.
Warm editions should add almost nothing once statistics are cached across editions. Their noise,
though, is already about 10 % between consecutive runs (4.61–5.12 s), so a 10 % gate needs the
repeated, alternating protocol above, not single runs.

Each snapshot recomputes its whole prefix, so the cost grows roughly with the square of match
length. If the measured cold overhead exceeds 10 %, options for approval (none chosen here):

1. take validity from the lifecycle snapshot record (`INVALID` and its codes come from the same
   `validate_prefix`) instead of validating again: about 20 % of the builder cost, not enough on
   its own;
2. expose the Stage 2 summaries and series the evaluation already computes on the same prefix
   (`evaluate.py` runs `analyse_match` and discards it): this changes the lifecycle contract;
3. compute statistics for all snapshots before edition 0 starts: this moves the cost to start-up
   rather than removing it;
4. accept a different cold-edition budget.

A reproducible benchmark (proposal): a small runner, in `matcheyes_eval` or as a slow-marked test
outside the default gate, that executes the protocol above and prints the report fields. It
reuses `LiveMatch.replay_seconds` and needs no new dependency.

## Alternatives considered

| Option | Why not |
| --- | --- |
| New cue kind `stats` | Changes the cue contract and `BROADCAST_VERSION`; statistics are not presentation decisions about verified insights |
| Per-minute bins or rolling windows | Bins of the last minutes can be revised by later events (turnovers); windows are a new derived statistic, deferred |
| Expose all `TeamSummary` fields | Turnovers and possession measures are revised retrospectively; pressure regain rate looks ahead |
| Compute or format in the browser | The browser would calculate football numbers; formatting would diverge between surfaces |
| Carry forward the last valid values after an invalid snapshot | Stale values would look current; the surface labels the last valid minute instead |
| Tie statistics to the reasoning outcome | A reasoning failure says nothing about the data; statistics would vanish for the wrong reason |
| `GET /matches/{id}/stats` now | No consumer yet; it is a new route |

## Consequences

* The match centre can show team statistics that equal Stage 2 exactly on every snapshot and the
  full-match summary at the final whistle, and are never revised once published.
* Early in a match proportions show "Not enough data" until the minimum sample is reached
  (minutes 1–8 in the measured matches), and PPDA until both teams have a defensive action in the
  zone (up to minute 8).
* Each snapshot costs a second, lighter Stage 2 pass. Caching by `snapshot_id` bounds it to once
  per snapshot.
* Step 2 (serving) and step 3 (frontend) need their own review. The frontend allowlist test will
  gain a statistics-field allowlist. The page renders only `display` strings and keeps the existing
  bans (no `fetch` besides the allowed ones, banned wording including "metric").
