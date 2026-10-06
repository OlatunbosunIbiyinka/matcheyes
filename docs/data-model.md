# Data model: observable match data

Status: **designed and implemented (Stage 1)**. Code: `src/matcheyes/domain/`, validation in
`src/matcheyes/ingestion/`.

This is the **complete** information MatchEyes may consume. Hidden ground truth is described in
[synthetic-data.md](synthetic-data.md) and is never part of this model
([ADR-0005](decisions/0005-observable-and-hidden-worlds.md)).

## Design principles

1. **Raw facts, not conclusions.** The feed carries what a real event provider would supply.
   Possession IDs, xG, pass difficulty, momentum and tactical state are *not* in the feed. They
   are either derived by deterministic analytics (Stage 2) or hidden truth MatchEyes must infer.
2. **Closed schemas.** Every model rejects unknown fields (`extra="forbid"`), so hidden data
   can't slip into the engine.
3. **Immutable.** Facts never change after ingestion. Corrections arrive as new events (Stage 9).
4. **Fictional and labelled.** Every match has `synthetic: true` and uses the fictional
   Meridian League.

## Entities

| Entity | Key fields | Notes |
| --- | --- | --- |
| `Club` | `club_id`, `name`, `short_name` (3 letters), colours | Observable identity only. Style is hidden. |
| `Player` | `player_id`, `name`, `shirt_number`, `position` | Attributes (pace, stamina, ...) are hidden. |
| `TeamSheet` | `club`, `formation`, `starting_xi` (11), `bench` (≤ 12) | Exactly one GK in the XI. Formation sums to 10. Unique IDs and shirt numbers. |
| `MatchInfo` | `match_id`, competition, season, matchday, kickoff, venue, referee, `home`, `away`, `synthetic` | Clubs differ. No player in both squads. `match_id` is opaque. |
| `ObservableMatch` | `info`, `events` | The whole observable match. |

## Conventions

| Topic | Convention |
| --- | --- |
| Pitch | 105 × 68 m. |
| Coordinates | Metres in the **acting team's attacking frame**: x = 0 own goal line, x = 105 opponent goal line, y = 0 the acting team's right touchline when attacking. `Location.mirrored()` converts to the opponent's frame. Analytics never need to know the kick-off direction. |
| Clock | `period` (1 or 2) + `clock_ms` since that period's kick-off. Stoppage time is `clock_ms` beyond 45:00. `MatchInstant.display_minute` gives broadcast minutes (`45+2'`, `63'`). Extra time and shoot-outs are out of scope. |
| Ordering | `sequence` is the producer's canonical order, contiguous from 1, and the key for ordering, gap detection and idempotency. `event_id` is globally unique. |
| Units | Ball speed in km/h (optional, as from an optical tracking provider). |

## Event vocabulary

Every event has `event_id`, `match_id`, `sequence`, `period`, `clock_ms`, `type`.
Player events add `team_id` and `player_id`. On-pitch actions add `location`.

| `type` | Extra fields | Covers (rules wording) |
| --- | --- | --- |
| `period_start`, `period_end` | — | match metadata / structure |
| `pass` | `end_location`, `outcome` (complete / incomplete / out_of_play / offside), `recipient_id`, `height`, `kind` (open_play / kick_off / throw_in / goal_kick / corner / free_kick), `ball_speed_kmh?` | passes; pass distance and accuracy; ball speed |
| `carry` | `end_location` | progression |
| `take_on` | `outcome`, `opponent_id?` | dribbles |
| `shot` | `end_location`, `outcome` (goal / saved / off_target / blocked / woodwork), `kind`, `body_part`, `goalkeeper_id?`, `ball_speed_kmh?` | shots; shot speed |
| `pressure` | `pressured_player_id` | pressure events |
| `tackle` | `outcome`, `opponent_id` | tackles |
| `interception`, `ball_recovery`, `clearance`, `block` | — | possession changes, defensive actions |
| `foul` | `fouled_player_id` | discipline |
| `own_goal` | — | scoring |
| `card` | `card` (yellow / second_yellow / red) | discipline |
| `substitution` | `replacement_id` | squad changes |
| `formation_change` | `formation` | observable tactical change, as announced |

Possession changes are **derived** from this stream (Stage 2), not sent as events. That avoids
two sources of truth that could disagree.

Deferred and documented: per-player physical samples (distance, top speed) for the rules'
"speed and distance thresholds" feature. These need a tracking-style stream, which will be
specified with the generator if we adopt it.

## Invariants

Enforced in two places.

**Schema level (domain models):** types, enums, ranges (pitch bounds, ball speed ≤ 150 km/h,
shirt numbers 1–99), closed schemas, team-sheet shape (11 starters, one GK, formation sums to 10,
unique IDs and numbers) and distinct clubs.

**Match level (`validate_match`):** returns every violation with a code, not just the first one.

| Code | Rule |
| --- | --- |
| `match_id_mismatch` | Every event belongs to this match. |
| `duplicate_event_id` | Event IDs are unique. |
| `sequence_gap_or_duplicate` | Sequences are unique and contiguous from 1. |
| `sequence_order` | Stored in sequence order. |
| `period_structure` | Exactly periods 1 and 2, each opened at clock 0 and closed, in order. |
| `event_outside_period` | No events between or outside periods. |
| `clock_not_monotonic` | The clock never goes backwards within a period. |
| `unknown_team` | `team_id` is home or away. |
| `player_not_on_pitch` | The actor is on the pitch now (tracking line-ups, substitutions, dismissals). |
| `opponent_reference_invalid` | Pressured, tackled, fouled or goalkeeper references are opponents on the pitch. |
| `pass_recipient_invalid` | A complete pass goes to a different teammate on the pitch. Other outcomes have no recipient. |
| `substitution_invalid` | The replacement is an unused substitute. |
| `substitution_limit` | At most 5 substitutions per team. |
| `card_sequence_invalid` | A second booking is `second_yellow`, and only after a yellow. |
| `restart_invalid` | Each period opens with a kick-off. After a goal or own goal, the conceding team kicks off. No kick-offs mid-play. |

**Prefix level (`validate_prefix`, Stage 7):** the same rules on a match in progress, except those
that need the whole match. Only periods that have started are required, and the last period may
still be open.

Statistical realism (event volumes, completion rates, ...) is a **generator acceptance check**,
not an invariant. See [synthetic-data.md](synthetic-data.md#realism-acceptance).

## On-disk format

```
<observable-root>/<match_id>/
  match.json      MatchInfo
  events.jsonl    one event per line, in sequence order
```

A tiny hand-built valid match is committed at `data/fixtures/minimal_match/`, used by tests.

## Live delivery (Stage 7)

During a match the same events arrive one at a time, in any order. They are identified by
`(match_id, event_id)` and ordered by `sequence`; `matcheyes.ingestion.log.EventLog` admits
them:

* an exact duplicate (byte-identical canonical JSON) is ignored;
* a different payload under an existing `event_id`, or a different event under an existing
  `sequence`, is a **conflict** and is refused. This first-writer-wins rule is a temporary Stage 7
  ingestion policy, not a judgment that the first version is correct. Corrections are deferred,
  and supporting them will require rebuilding the affected snapshots and revisions;
* the **watermark** is the highest sequence up to which every event has arrived. Events beyond a
  gap are buffered, and the data status is `data_incomplete` until the gap fills.

Derived records, none of which are part of the observable feed:

| Record | Content |
| --- | --- |
| `SnapshotHeader` | content-addressed ID, watermark, closed minute, `as_of`, digests of the prefix and of the team sheet |
| `SnapshotRecord` | outcome (`evaluated` / `invalid` / `failed`), insight count, storylines established |
| `Storyline` | key (team, metric, direction), first and current anchor, state (`open` / `withdrawn`), link to a replaced storyline, revisions |
| `Revision` | number, snapshot, state, change kinds, the verified `FinalInsight` and its fingerprint, Stage 5 audit findings, withdrawal reason, chain fingerprints |

See [living-insights.md](living-insights.md).
