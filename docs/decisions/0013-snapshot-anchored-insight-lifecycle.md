# ADR-0013: Snapshot-anchored insight lifecycle

- Status: Accepted
- Date: 2026-10-05
- Stage: 7

## Problem

Stages 2–6 produce verified, audited insights for a complete match. A live match is never
complete. Events arrive late, out of order or twice, and the truth established at minute 30 can
change by minute 50.

The Stage 7 discovery measured how the existing pipeline behaves when replayed on growing prefixes
of a match. It found four problems:

* About 19% of candidates disappear later because Stage 2 suppression re-anchors them. Of those,
  43 of 45 come back within 15 bins for the same team, metric and direction.
* A candidate ID embeds its onset bin, so the same phenomenon can change ID.
* `continues` resolves late: it starts as unknown and later becomes true or false.
* Auditing an insight against a later workspace produces false findings.

We need to say what is true *now*, keep what was said earlier, and never mix the two.

## Context

* The deterministic pipeline (Stages 2–5) is approved and must not change. It is a pure function
  of an observable match.
* Stage 6 personalizes a `FinalInsight` that it holds by fingerprint
  ([ADR-0012](0012-personalization-presentation-layer.md)).
* `validate_match` requires a complete match, and the runtime never called it.
* No streaming infrastructure exists, and none is wanted at this stage. Azure choices are Stage 8.

## Decision

Truth is anchored to the snapshot on which it was established.

1. **The event log (`ingestion/log.py`)** keys every event by `(match_id, event_id)`.
   * An exact duplicate is a no-op.
   * A different payload under an existing event ID is a conflict, and so is a reused sequence
     number. Conflicts are rejected. An event of another match is rejected.
   * The watermark is the highest contiguous sequence. Events beyond a gap are buffered and
     excluded.
   * An unresolved gap is reported as `DATA_INCOMPLETE`, which is a data status, not an insight
     state. Nothing waits for the missing event.
2. **Canonical snapshots (`lifecycle/snapshot.py`)** are taken once per closed match minute of the
   contiguous prefix. There are no per-event snapshots and no speculative key-event snapshots.
   * Snapshot identity is a content address. It is a hash of the match, the team sheet, the
     prefix digest, the watermark and the closed minute, with no wall clock.
   * `validate_prefix` checks every invariant of `validate_match` except completeness.
     `validate_match` itself is unchanged.
3. **Fresh evaluation (`lifecycle/evaluate.py`).** Every snapshot is analysed, investigated,
   verified and audited from scratch by the unchanged pipeline, on its own events only.
   * Nothing from an earlier snapshot is reused.
   * An invalid prefix is `INVALID`, and a pipeline exception is `FAILED`. Neither falls back to
     an earlier result.
4. **Storylines (`lifecycle/identity.py`, `reconcile.py`)** follow one phenomenon across
   snapshots.
   * The key is `(match, team, metric, direction)`, plus the first anchor.
   * An insight continues a storyline with the same key whose current anchor is within Stage 2's
     own `suppression_bins`. Ties go to the identical candidate ID, then the nearest anchor, then
     the earliest storyline.
   * There are two states: OPEN and WITHDRAWN.
   * An opposite-direction change at the same place withdraws the old storyline and creates a new,
     linked one. A returning phenomenon reinstates the original ID.
   * IDs are derived, never supplied.
5. **Append-only revisions.** A revision records:
   * the snapshot it was established on: ID, watermark and `as_of`;
   * the verified insight, its fingerprint and integrity;
   * the Stage 5 audit on its own snapshot;
   * its change kinds, computed from verified fields, never from prose;
   * a chain fingerprint, `SHA-256(previous chain, snapshot ID, insight fingerprint, state)`,
     starting from a fixed genesis value.
   Evidence-only, wording-only and rank-only changes are not material. Evidence and wording
   changes are kept in history without a notice; rank is not part of the insight.
   A revision is appended exactly when the record of what was established on the latest snapshot
   differs from the storyline's latest revision: the insight (by fingerprint), its Stage 5 audit
   findings, or its state (reinstatement). An unchanged insight appends nothing; it advances
   `verified_through`. See "Revision semantics" below.
6. **The lifecycle feed (`lifecycle/feed.py`)** has three parts:
   * current insights: OPEN storylines freshly verified on the latest snapshot, and only if that
     snapshot was evaluated;
   * withdrawn storylines, listed separately;
   * templated, factual notices for material changes.
   The feed distinguishes a current insight, NO_CANDIDATE, AWAITING_SNAPSHOT, UNAVAILABLE, and
   (from the log) DATA_INCOMPLETE.
7. **Stage 6 consumes only current revisions**, against the latest snapshot's workspace. A view
   whose source fingerprint is not a current revision is stale. Integrity is part of the insight,
   so a compromised revision is current, and warned, on the snapshot that established it.
8. **An independent lifecycle audit (`lifecycle/audit.py`)** re-derives the snapshots from the
   log. It re-runs the Stage 5 audit of every revision against that revision's own snapshot, and
   re-checks chain, numbering, identity, transitions, change kinds, linkage and anchoring. It also
   re-evaluates the snapshots ("reproduction") and confirms the current revisions equal a fresh
   evaluation of the latest snapshot: the same insights, with the same audit findings.
9. **Replay is a pure function** of the contiguous prefix and the schedule. The canonical state
   is byte-identical across replays and across arrival orders. `python -m matcheyes replay` runs
   it.

No agent, model or LLM takes part in reconciliation, identity or truth decisions. The reasoning
model is reached only through the unchanged Stage 4 pipeline inside snapshot evaluation.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Incremental analytics (update Stage 2/3 state per event) | Reopens approved stages; a second implementation of every metric to keep equal to the batch one |
| Per-event snapshots | ~1,800 pipeline runs per match for no new truth: detection needs a 15-bin after-window |
| Speculative key-event snapshots (goal, red card) | Analyses an open minute; the extra snapshot cannot establish more than the next closed minute |
| Carry forward earlier evidence or verification | Mixes snapshots; the discovery showed audits against a different workspace give false findings |
| Fall back to the previous revision on failure | Presents unverified-now truth as current; UNAVAILABLE is honest |
| More states (EMERGING, VERIFIED, UPDATED, SUPERSEDED, EXPIRED) | Not derivable from verified truth without prediction; OPEN/WITHDRAWN plus change kinds carries the same history |
| Identity by candidate ID | Suppression re-anchoring changes the ID of the same phenomenon (43 of 45 re-anchors) |
| LLM-based matching or change description | Non-reproducible; identity and truth decisions must be checkable |
| Corrections (accept a changed payload) | Needs a revision model for facts, not just insights; deferred (see limitations) |
| Event Hubs / Kafka / Redis / PostgreSQL now | Solves transport and storage, not truth; the abstractions here are what such a system would host (Stage 8) |

## Consequences

* The same delivered events give the same lifecycle, whatever the order, duplication or delay
  (measured in [stage7-evaluation.md](../stage7-evaluation.md)).
* At full time, the current revisions equal the batch Stage 4 investigation of the complete match.
* Each snapshot costs one full pipeline run, a full replay is roughly a hundred runs, and the
  independent audit with reproduction costs another replay. The evaluation caches evaluations by
  snapshot ID, which is a content address.

### Revision semantics

Three kinds of difference exist between consecutive snapshots of one storyline:

| Kind | What differs | Canonical revision? | Change kinds / notice |
| --- | --- | --- | --- |
| Truth revision | a verified field of the `FinalInsight`: candidate (onset), verdict, strength, explanation, integrity, evidence | yes | the matching change kinds; a notice unless evidence-only |
| Audit revision | only the Stage 5 audit findings of the same insight on the new snapshot | yes | none |
| Wording within the verified record | only templated text inside the `FinalInsight` (narrative, claim text, evidence summaries) | yes | none |
| Presentation-only | a Stage 6 view of a current revision | no; views are never canonical | n/a |

An audit-only change creates a canonical revision because the audit result is part of what is
established on a snapshot, and the fresh-verification rule forbids presenting an earlier audit
as current. Without that revision, the current revision would carry the audit findings of an
older snapshot while being marked verified on the latest one. A reader of the feed would see a
clean audit that the latest audit no longer gives, or a finding it no longer makes. Such a
revision is history plus a fresh trust record, not a truth change, so it has no change kind and
raises no notice. The `current` check of the lifecycle audit compares the current audit
findings with a fresh audit, so a reconciliation that skipped these revisions would be caught.

A wording-only change creates a revision for the same reason at the level of bytes: Stage 6
renders the current revision's `FinalInsight` and checks it by fingerprint, so the current
revision must be the exact insight the pipeline produced on the latest snapshot. Wording is
deliberately not a change kind.

In the Stage 7 evaluation, neither case occurred: on both splits, every revision after the
first carries at least one change kind, and no revision has audit findings
([stage7-evaluation.md](../stage7-evaluation.md)). The rule causes no churn in practice; it
exists so the record stays exact when they do occur.

### Trust model

* Truth is decided only by the deterministic pipeline on one snapshot.
* The lifecycle decides only identity, by a fixed rule, and records history.
* The feed and Stage 6 only present the current revisions.
* The independent audit trusts nothing in a lifecycle state. It needs the event log and the
  pipeline.
* Planted truth never reaches the runtime. This is enforced by import, vocabulary and runtime
  probes, and checked on every canonical state in the evaluation.

### Latency limitation

A shift needs a 15-bin after-window, and a snapshot is taken only when a minute closes. So a
change is first reported at least about 15 match minutes after its onset, plus up to one minute.
Measured from the start of the planted window, the median first detection is 1,080 s
(development) and 1,320 s (held-out), from 3 detected insights per split
([stage7-evaluation.md](../stage7-evaluation.md)). This is a snapshot-anchored lifecycle over
deterministic recomputation, **not** production real-time streaming.

### Conflict policy: temporary Stage 7 ingestion policy (first writer wins)

This is a **temporary Stage 7 ingestion policy**, not a statement about which version is
correct:

* **Corrections are intentionally deferred.** Stage 7 has no correction model.
* **Conflicting payloads are rejected.** A different payload under an existing event ID, or a
  different event under an existing sequence number, is refused and counted as a conflict.
* **Canonical state stays based on the first accepted version.** Snapshots, evaluations and
  revisions already derived from it are not touched.
* **"First writer wins" does not mean the first event is correct.** It only means the log never
  overwrites an accepted event. If a producer issues a genuine correction, the log keeps the
  original, and the conflict count is the only signal.
* **A future correction mechanism must rebuild what the corrected event affects:** every
  snapshot whose prefix contains it, and every evaluation and revision derived from those
  snapshots. Because snapshot IDs are content addresses over the prefix digest, a corrected
  prefix gets new snapshot IDs. The correction model still has to decide how the history of the
  superseded revisions is presented. That design belongs to a later stage.

### Future infrastructure path

The pieces map onto infrastructure without changing semantics:

* `EventLog` becomes a partitioned event store keyed by match with idempotent writes, for
  example Event Hubs plus a store.
* The scheduler becomes a minute-close trigger.
* `evaluate_snapshot` becomes a stateless worker keyed by snapshot ID. Content addressing makes
  retries idempotent and results cacheable.
* `LifecycleState` becomes an append-only table of revisions with the chain fingerprint as an
  integrity check.

These choices are Stage 8 decisions, to be recorded as ADRs when evidence exists.
