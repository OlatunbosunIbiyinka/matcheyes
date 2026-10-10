# Living insights: the snapshot-anchored insight lifecycle

Status: **implemented (Stage 7)**. Code: `src/matcheyes/ingestion/log.py` and
`src/matcheyes/lifecycle/`. Decision: [ADR-0013](decisions/0013-snapshot-anchored-insight-lifecycle.md).
Results: [stage7-evaluation.md](stage7-evaluation.md).

A live match is not a file. Events arrive one at a time, sometimes out of order, duplicated or
late. An insight that is right at minute 30 can be outdated at minute 50. Stage 7 keeps insights
truthful over time without changing how truth is established:

> Each insight is established by the unchanged Stage 2–5 pipeline on one canonical snapshot of
> the match. The lifecycle only decides which insights are the same phenomenon over time, and
> records what changed.

This is deterministic local recomputation over a growing log. It is **not** production real-time
streaming. There is no broker, no database and no network, and latency is bounded below by
detection (see [Latency](#latency)).

## Pipeline

```
raw events (any order, duplicates, gaps)
  -> EventLog: idempotent; conflicts rejected; contiguous watermark; DATA_INCOMPLETE beyond a gap
  -> Scheduler: one canonical snapshot per closed match minute of the contiguous prefix
  -> evaluate_snapshot: unchanged Stage 2 -> 3 -> 4 (+ Stage 5 verification) -> Stage 5 audit
  -> reconcile: storyline identity, append-only chained revisions, withdrawals, reinstatements
  -> lifecycle_feed: current / withdrawn / notices / status
  -> audience_feed: Stage 6 views of the current revisions only
```

`LifecycleEngine` wires these together in memory. `replay(info, events)` feeds events to a fresh
engine, and the CLI exposes it:

```bash
uv run python -m matcheyes replay data/fixtures/minimal_match --json lifecycle.json
uv run python -m matcheyes replay <match_dir> --audience fan --club <club_id>
```

## Event log

Events are identified by `(match_id, event_id)` and ordered by the producer's `sequence`.

| Arrival | Outcome |
| --- | --- |
| new event | `accepted` |
| same `event_id`, byte-identical canonical payload | `duplicate` (ignored) |
| same `event_id`, different payload | `conflict` (rejected; the first payload stays) |
| new `event_id` under an existing `sequence` | `conflict` (rejected) |
| another match, or a line that is not a valid event | `rejected` |

The **watermark** is the highest sequence up to which every event has arrived. Events beyond a gap
are kept but are not usable, and the log reports `DATA_INCOMPLETE` (with `missing_from`) until
the gap fills. Nothing waits: the status is immediate, and the feed keeps showing the last
snapshot's truth together with the data status.

Every prefix has a digest chained over canonical event JSON, so the same prefix has the same
digest however its events arrived.

### Conflict policy: temporary Stage 7 ingestion policy

"First writer wins" is a **temporary Stage 7 ingestion policy**. It does not mean the first
version of an event is correct.

* Corrections are intentionally deferred.
* Conflicting payloads (a changed payload under an existing event ID, or a different event under
  an existing sequence number) are rejected and counted.
* Existing canonical state (snapshots, evaluations, revisions) stays based on the first accepted
  version.
* A future correction mechanism would have to rebuild every snapshot whose prefix contains the
  corrected event, and every evaluation and revision derived from those snapshots. Corrected
  prefixes get new content-addressed snapshot IDs.

The decision is recorded in
[ADR-0013](decisions/0013-snapshot-anchored-insight-lifecycle.md#conflict-policy-temporary-stage-7-ingestion-policy-first-writer-wins).

## Canonical snapshots

A match minute **closes** when the contiguous prefix contains an event of a later minute, or
when its period ends (`PeriodEnd`). Each closing minute produces one snapshot of the prefix up to
that point. Minutes that close together with no events between them share the first one's
snapshot, because their prefixes are identical.

The schedule is a pure function of the contiguous prefix. Arrival order, duplicates and delays
cannot change which snapshots exist or what they contain. A snapshot's ID is a content address:

```
snap-{watermark:05d}-{sha256(match_id, watermark, period, minute, log_digest, info_digest)[:16]}
```

Wall-clock time plays no part. A snapshot contains the team sheet and the prefix events only.

## Evaluation on one snapshot

`evaluate_snapshot` runs the existing pipeline, unchanged, on the snapshot's own events:

1. `validate_prefix` checks the data invariants that hold for an in-progress match (the
   full-match rules such as "the match has ended" are skipped). A violation makes the snapshot
   **INVALID**, and nothing is evaluated.
2. Stage 2 analysis, Stage 3 candidates and a fresh `MatchWorkspace` are built from the prefix.
3. Every candidate at or above the configured level is investigated (Stage 4, verified by
   Stage 5). The claim audit and lineage audit run against the same workspace.
4. Any exception makes the snapshot **FAILED**. It never falls back to earlier results.

Nothing is reused between snapshots: evidence, verification and audit are recomputed every time.
Traces are excluded because they carry wall-clock latencies.

## Storylines and identity

A **storyline** is one observed phenomenon for one team, followed across snapshots. Its key is
`(match, team, metric, direction)`, and it is anchored at a timeline bin.

Stage 3 candidate IDs embed their onset bin, and Stage 2 non-maximum suppression can move an
onset as more play arrives, so one phenomenon can carry different candidate IDs over time.
`match_storylines` therefore continues a storyline when:

* the key is equal, and
* the storyline's current anchor is within the detector's own `suppression_bins` (the distance
  inside which Stage 2 itself treats two changes as one).

Ties are broken by an identical latest candidate ID, then the nearest anchor, then the earliest
created. Matching is one-to-one. Withdrawn storylines take part, so a returning phenomenon is
**reinstated** under its original ID.

Storyline IDs are derived only from observable, canonical data:

```
sl-{match_id}-{team_id}-{metric}-{direction}-{first_anchor}[-n]
```

The numeric suffix separates a later storyline with the same first anchor. No input can supply a
storyline ID, and no scenario, answer key or generator parameter is involved (enforced by
architecture tests).

An insight that matches nothing creates a storyline. If an **open** storyline with the same team
and metric, the opposite direction and an anchor within the suppression distance exists, the
new change replaces it: the old storyline is withdrawn (`opposite_direction`) and the new one is
`linked_to` it.

## Revisions

Each storyline holds an append-only tuple of revisions. A revision records what the pipeline
established on one snapshot: either a complete verified and audited `FinalInsight` (state
`OPEN`), or a withdrawal (state `WITHDRAWN`) with its reason (`not_detected` or
`opposite_direction`).

| Change kind | Recorded when |
| --- | --- |
| `created` | first revision of a storyline |
| `reanchored` | the candidate ID changed (the onset moved) |
| `verdict_changed` | the verifier's verdict changed |
| `strength_changed` | the claim strength changed |
| `explanation_changed` | the leading explanation or the alternatives changed |
| `integrity_changed` | evidence integrity changed |
| `evidence_changed` | the evidence the insight rests on changed (tool, facts, events, quarantine) |
| `withdrawn` | an open storyline has no insight on an evaluated snapshot |
| `reinstated` | a withdrawn storyline is matched again |

Change kinds compare verified truth fields, never prose. A revision is appended only when the
insight fingerprint or the audit findings differ from the latest revision, or on reinstatement.
A snapshot that re-establishes the same insight appends nothing; it advances the storyline's
`verified_through` to that snapshot. If only wording or audit findings change, a revision is
appended with no change kinds. It stays in the history and raises no notice.

Why, briefly (the full decision is in ADR-0013, "Revision semantics"):

* **Truth revision:** a verified field changed. It gets change kinds, and a notice unless it is
  evidence-only.
* **Audit revision:** the same insight, but the Stage 5 audit on the new snapshot differs. The
  fresh-verification rule forbids presenting an older audit as current, so the new audit is
  recorded. It is not a truth change: no change kind, no notice.
* **Wording within the verified record:** the current revision must be byte-exact, because
  Stage 6 renders it and checks its fingerprint. No change kind, no notice.
* **Presentation-only:** Stage 6 views are never canonical and never create revisions.

Neither audit-only nor wording-only revisions occurred in the Stage 7 evaluation.

Every revision is chained:

```
chain_fingerprint = sha256({previous, snapshot_id, insight_fingerprint, state})
```

The first revision chains from a genesis of 64 zeros. The contracts validate the chain, the
numbering (1, 2, 3, … without gaps), the state and the agreement between the revision and its
insight. Snapshots that are INVALID or FAILED are recorded but change no storyline.

## The feed: current, withdrawn, notices, status

`lifecycle_feed(state, log_status)` separates:

* **current**: the latest revision of every open storyline that was freshly established on the
  latest snapshot, and only when that snapshot was evaluated;
* **withdrawn**: storylines the latest evidence no longer supports, with their withdrawal. They
  are never silently dropped;
* **notices**: material changes on the latest snapshot, as factual templated text, for example
  "Revised as of <minute>: verdict tentative -> explained." Evidence-only changes are history,
  not notices. When only the alternatives changed, the notice names the alternatives instead of
  repeating an unchanged leading explanation ("none -> none"); see
  [ADR-0014](decisions/0014-broadcast-cue-contract-and-live-presentation-surface.md).

| Status | Meaning |
| --- | --- |
| `current` | the latest snapshot was evaluated and has open storylines |
| `no_candidate` | the latest snapshot was evaluated and nothing is open |
| `unavailable` | the latest snapshot failed or was invalid; earlier revisions are history, not current |
| `awaiting_snapshot` | no minute has closed yet |

`data_status` (`contiguous` / `data_incomplete`), the watermark and the buffered count come from
the log and are reported alongside. An incomplete log does not hide the last truth, but it is
always labelled.

## Stage 6 integration

`audience_feed(state, profile, ws)` personalizes only the current revisions, against the latest
snapshot's workspace. It refuses a workspace of another snapshot. `view_is_current(view, state)`
is false for a view whose source is not a current revision, or whose source was altered, so a
stale view can be detected and never presented as current. Stage 6 itself is unchanged, and its
view audit and compromised-evidence warnings carry through.

## Lifecycle audit

`LifecycleAuditor` (`audit_lifecycle`) trusts nothing in the state. It rebuilds every snapshot
from the log and checks:

| Check | What it re-derives |
| --- | --- |
| `snapshot` | headers from the log, in schedule order; records list real storylines |
| `numbering` | revisions numbered from 1, one per snapshot, on evaluated snapshots only |
| `chain` | every chain fingerprint from its predecessor |
| `fingerprint` | every insight fingerprint from the insight |
| `identity` | storyline IDs from key and first anchor; consecutive anchors within suppression |
| `transitions` | created first; withdrawal only when open; reinstatement only when withdrawn |
| `change_kinds` | recorded kinds against kinds recomputed by independent code |
| `linkage` | opposite-direction withdrawals and linked successors correspond |
| `anchoring` | every cited event is inside the revision's own snapshot prefix |
| `revision_audit` | the Stage 5 audit of each revision on its own snapshot |
| `integrity` | recorded evidence integrity against the insight and the verifier |
| `reproduction` | each evaluated snapshot re-evaluates to the recorded insights |
| `current` | current revisions are exactly the fresh evaluation of the latest snapshot, including its audit findings |

Reproduction costs a full replay. Callers that audit often pass an evaluator that caches by
snapshot ID, which is safe because snapshot IDs are content addresses.

## Determinism and replay

The canonical state (`LifecycleState.model_dump_json()`) carries no wall-clock time or trace
data. The same set of events gives byte-identical state in any arrival order, across repeated
replays in one process and across fresh processes.

## Latency

Stage 2 confirms a shift only with a 15-bin after-window, so a lifecycle insight appears at
least about 15 match minutes after the onset it describes, plus up to one minute for the snapshot
schedule. Measured from the start of the planted window, the median first detection was 18
minutes on development and 22 on held-out
([stage7-evaluation.md](stage7-evaluation.md)). Recomputing every snapshot from scratch costs
about 0.2 s per snapshot on a development machine. That is fine
for one match per closed minute, but it is not a production throughput claim.

## Model-backed lifecycles (Stage 9)

The lifecycle does not change when a hosted model fills a reasoning role. A model-backed
lifecycle is recorded once (`python -m matcheyes record`) and replayed from its pinned
transcript ([ADR-0015](decisions/0015-foundry-model-as-untrusted-reasoner.md)). Each snapshot's
investigations ask the transcript, never a live endpoint. A request the transcript cannot answer
makes that investigation `unavailable`, which the lifecycle records like any other failure:
nothing is invented and nothing falls back to an earlier revision. Only the canonical delivery is
recorded, so delivery perturbations that create new snapshots fail closed on replay. Recorded and
replayed lifecycles are identical on the S05 demo ([stage9-evaluation.md](stage9-evaluation.md)).

## Limitations

* **No corrections.** A changed payload under an existing event ID is rejected as a conflict
  under the temporary first-writer-wins ingestion policy. That is not a judgment that the first
  version is correct.
* **Detection latency.** Insights lag their onset by the detector's confirmation window.
* **Full recomputation.** Every snapshot reruns the pipeline. There is no incremental analytics.
* **In memory only.** The log and the state live in one process. Persistence, transport,
  retries across processes and correlation IDs remain later infrastructure decisions.
* **Identity is heuristic within the detector's own resolution.** Two genuinely different
  changes of the same key inside the suppression distance are one storyline, exactly as Stage 2
  treats them as one change.
