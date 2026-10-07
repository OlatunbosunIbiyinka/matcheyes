# ADR-0014: Broadcast cue contract and live presentation surface

- Status: Accepted
- Date: 2026-10-06
- Stage: 8

## Context

Stage 7 ([ADR-0013](0013-snapshot-anchored-insight-lifecycle.md)) established what is true *now*
on a live match: one canonical snapshot per closed minute, fresh verification per snapshot, and
append-only storylines whose current revisions are the only current truth. Stage 6
([ADR-0012](0012-personalization-presentation-layer.md)) renders a verified insight for an
audience and audits the view.

Nothing yet turns that record into something a broadcast surface can show over time. The Stage 8
discovery replayed ten matches and measured what a viewer of the lifecycle feed would see:

* the first output arrives at minute 31–47, so the screen is blank for the first half hour;
* the 34 goals and red cards in those matches produce no output at all;
* 42 of 343 lifecycle notices contained a tautological change such as
  "leading explanation none -> none" (7 were entirely tautological), caused by the
  `explanation_changed` notice naming the leading explanation even when only the alternatives
  changed;
* re-anchor-only revisions (79 of 343 notices) would interrupt a viewer with no news.

## Problem

A broadcast surface needs timed, ordered instructions — show, revise, retract, report status —
that are traceable to the verified record, never ahead of it and never behind it, identical for
every viewer of the same surface, and reproducible offline. It must work without Foundry
credentials, without cloud infrastructure and without letting the browser reason about football.

## Decision

Stage 8 adds a **presentation-only** layer, `matcheyes.broadcast`, which compiles the Stage 7
lifecycle record and the observable events into a **cue timeline**, and a read-only stdlib HTTP
API with Server-Sent Events, `matcheyes.api`, which replays it live. Stage 8 does not modify the
truth-producing pipeline: Stages 2–5 (metrics, detection thresholds, contextual semantics and
candidate levels, reasoning, verification, evidence integrity) and the Stage 7 truth and revision
semantics are unchanged. The only change outside the new packages is the approved notice-text
fix (below), which changes wording, not truth.

### Cue contract (`broadcast/contracts.py`)

A `Cue` has:

| Field | Meaning |
| --- | --- |
| `cue_id` | `cue-` + 24 hex of SHA-256 over the canonical JSON of every other field |
| `match_id` | the match |
| `kind` | `moment`, `insight`, `revision`, `retraction`, `status` |
| `audience` | `fan` or `broadcaster` for insight and revision cues; `None` (every audience) otherwise |
| `show_from` | match instant from which it is on screen |
| `expires_at` | for moments and retractions only; other cues stay until superseded |
| `supersedes` | the cue a revision revises, a retraction retracts, or a status replaces |
| `priority` | 0–100, fixed per kind (below) |
| `interrupt` | whether the surface should draw attention to it |
| `sections` | `(kind, text, mandatory)` — the only text a surface renders |
| `snapshot_id` | the canonical snapshot the cue belongs to (`None` only before the first) |
| `source` | a typed, discriminated source that makes the cue traceable |

Sources:

* **moment** — event IDs, the event's match instant, the snapshot that first contains it, the
  team, and the score after it (a count of observable goal events);
* **insight / revision** — storyline ID, revision number, insight fingerprint, snapshot ID, the
  SHA-256 of the Stage 6 view, verdict, evidence integrity and change kinds;
* **retraction** — the retracted cue ID, storyline, the displayed revision, the revision that
  ended it, the reason and the snapshot;
* **status** — `current`, `no_candidate`, `unavailable`, `data_incomplete` or
  `awaiting_snapshot`, the snapshot, the watermark and a short detail.

A `CueTimeline` is the ordered cues of one surface (audience and favourite club) for one match,
with its own content address (`tl-` + 32 hex). Validators enforce: IDs equal their content
address; the source type matches the kind and names the same snapshot; only moments and
retractions expire, after they are shown; audiences; a revision supersedes exactly one cue and a
retraction exactly the cue it retracts; cues are ordered by match time, unique, of one match and
one audience, and every superseded cue appears earlier. No wall clock, request, connection,
latency, trace or random value takes part in any identity.

### Timing model

* **Moment cues** are emitted at the close of the next canonical snapshot — the snapshot that
  first contains the event — with `show_from` equal to that snapshot's `as_of`. There is no
  second, event-time truth path: a moment is shown once, at most one minute after it happened.
  Moments of an invalid prefix are not presented.
* **Insight, revision and retraction cues** are emitted on the snapshot that established the
  change, at its `as_of`.
* **Status cues** are emitted whenever the status changes; the first, `awaiting_snapshot`, is at
  kick-off.
* Display semantics: a cue is on screen from `show_from`; moments and retractions leave at
  `expires_at` (two match minutes); insight, revision and status cues stay until a later cue
  supersedes them.

The timeline is a pure function of the lifecycle state and the event log: compiling a finished
state at once, or snapshot by snapshot as it grows, gives the same cues. The compiler recomputes
the prefix digest of each snapshot and refuses a log that is not the one the lifecycle was built
from.

### Insight and retraction model

* Insight cues come only from **current** revisions (re-read per snapshot from the record:
  `history.current_at`), and only from the Stage 6 **primary** feed of that snapshot, built
  against that snapshot's own workspace. Card text is the Stage 6 view verbatim; the view
  fingerprint keeps the cue traceable to it.
* A displayed card whose storyline stays current with the same revision stays (no cue).
* A new revision of a displayed storyline that is still in the primary feed is a **revision**
  cue superseding the card.
* A displayed card is **retracted**, explicitly, with a templated reason:
  `withdrawn` (no longer detected), `replaced` (opposite-direction change),
  `no_verified_explanation` (revised off the primary surface), `withheld` (the revised view
  failed audit), or `unavailable` (the snapshot failed or was invalid). A retraction copies the
  card's fact so the viewer knows what was retracted.
* When a snapshot is unavailable, every card is retracted and the status becomes `unavailable`.
  Nothing from an earlier snapshot is presented as current.
* Re-anchor notices are history only: they never interrupt.

### Selection policy (`broadcast/selection.py`)

* Fans and broadcasters only; the analyst timeline is not a broadcast surface.
* At most three insight cards (`MAX_ON_SCREEN`). Free cards are filled from the Stage 6 primary
  feed in Stage 6 order (relevance, time, candidate ID). Nothing is displaced: a card leaves only
  by revision or retraction, so a card never disappears unexplained.
* Fixed priorities: retraction 90, goal and red card 80, interrupting revision 70, insight 60,
  substitution and period markers 40, silent revision 20, status 10.
* A revision interrupts only for a material change other than re-anchoring
  (`MATERIAL - {reanchored}`). Re-anchor-only, evidence-only and audit-only revisions replace the
  card silently, without a notice.
* Within a snapshot the order is deterministic: moments in sequence order, then changes to
  displayed cards in display order, then new cards in Stage 6 order, then the status.

### Notice-text fix (approved)

`lifecycle.feed.notice_text` described `explanation_changed` as
"leading explanation X -> Y" even when only the alternatives changed. It now names the leading
explanation only when it differs, and describes alternatives that changed, were added or removed,
or were reordered. Truth, change kinds and revision semantics are unchanged; regression tests
assert that no notice in the reference runs names an unchanged value.

### API boundary (`api/`)

* Python standard library only (`http.server`, `ThreadingHTTPServer`), Server-Sent Events.
  pydantic remains the only runtime dependency.
* Read-only endpoints: `GET /health`, `/matches`, `/matches/{id}/timeline`,
  `/matches/{id}/stream`, plus the three static files. Every other method is refused (405). No
  ingest, mutation, truth or debug endpoint.
* Match IDs are an allow-list loaded at start-up from observable match directories (only
  `match.json` and `events.jsonl` are read; the directory name must equal the match ID). Queries
  accept only `audience` (fan or broadcaster) and `club` (empty or one of the two clubs).
* **One shared replay per match, owned by the server.** A `LiveMatch` thread feeds the events in
  sequence order to one `LifecycleEngine` on a deterministic replay clock (match time divided by
  `speed`) and advances one compiler per surface. Viewers only read published cues: no viewer
  causes recomputation. The clock decides when an event is fed, never what a cue contains.
* Errors are short JSON messages without stack traces; streams are bounded by a semaphore;
  request paths are bounded; the evaluation cache is bounded.

### UI boundary (`api/static/`)

* A minimal static page: match, score, clock, status, insight cards, key moments, revisions and
  retractions, audience and favourite-club selectors.
* No inline script or style, no HTML injection sinks (`textContent` only), no storage, cookies,
  credentials or third-party origins; a strict Content Security Policy (`default-src 'none'`,
  `script-src 'self'`, `style-src 'self'`, `connect-src 'self'`).
* The page renders cue sections and applies the display rules. It does not generate, alter or
  rank explanations and performs no football reasoning; the score it shows is the cue's.

### Security

* Planted truth never reaches the runtime: `matcheyes_synth` and `matcheyes_eval` are never
  imported or named by `broadcast` or `api` (static and runtime probes); cue schemas carry no
  hidden vocabulary or insight internals; served cues are scanned for scenario and generator
  vocabulary in the evaluation.
* Broadcast compilation imports no clock, randomness, network, process or persistence module and
  no reasoning model. The API uses an allow-list of standard-library modules and writes nothing.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Event-time moment path (show a goal at the event) | A second truth path beside snapshots; the approved design shows moments at the next snapshot close (≤ 1 minute) |
| Interrupt on every material revision | Re-anchor-only notices carry no news (79 of 343 in the discovery) |
| Displace older cards with newer insights | A card would disappear without a revision or retraction |
| Recompute per viewer or per browser | Unbounded cost, and viewers could diverge; one server-owned replay serves all |
| FastAPI / uvicorn / WebSockets | New runtime dependencies for a read-only, one-way stream that stdlib SSE covers |
| Foundry or an LLM writing cue text | Not needed for presentation; non-reproducible; deferred (Q1, Q2) |
| Client-side composition of insight text | The UI would reason about football |
| Event Hubs, Cosmos DB, Kubernetes, Azure hosting now | Transport and hosting, not presentation semantics; explicitly out of scope |

## Consequences

* The screen is no longer blank until the first verified insight: kick-off, goals, red cards,
  substitutions and period markers appear within one snapshot, and the lifecycle's insights,
  revisions and retractions follow when the lifecycle produces them
  (measured in [stage8-evaluation.md](../stage8-evaluation.md)).
* The offline timeline, the server's live replay and the HTTP stream are identical cue for cue;
  shuffled delivery gives the canonical timeline.
* Insight latency is inherited from Stage 2 detection (ADR-0013's latency limitation). Stage 8
  adds at most one snapshot to moments and nothing to insights.
* Each surface's Stage 6 feed is recomputed on snapshots where its cards may change; a full
  six-surface replay costs seconds with cached evaluations, spread over the replay.

## Explicit non-goals

No change to Stages 2–5 or to Stage 7 truth or revision semantics. No Foundry or LLM
integration, Azure deployment, Event Hubs, Cosmos DB, Kubernetes, voice, multilingual output,
user accounts, authentication, persistence, 3D or real footage. Stage 8 is a deterministic replay
of synthetic, fictional matches over SSE, not production real-time broadcasting.

## Future compatibility

* **Foundry / LLM:** a model may later rephrase a cue's sections for an audience or language. The
  cue contract already separates the verified source (fingerprints) from the rendered text, so a
  rephrased section can be audited against its source like a Stage 6 view, and the rule-based
  path remains the evaluated reference.
* **Hosting:** `LiveMatch` is the unit a hosted service would run per match. Its input is the
  event log, which maps onto a partitioned event store; its output is an append-only, content-
  addressed cue stream that a CDN or pub/sub fan-out can serve unchanged. Content addressing
  makes republishing idempotent. These are later decisions, to be recorded when evidence exists.
