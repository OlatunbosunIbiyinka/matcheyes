# Stage 8 evaluation: broadcast cues and the live surface

Stage 8 turns the Stage 7 lifecycle record into a timed, ordered **cue timeline** per broadcast
surface and replays it live over Server-Sent Events from a read-only, standard-library HTTP
server to a static page. The decision is
[ADR-0014](decisions/0014-broadcast-cue-contract-and-live-presentation-surface.md). This report
gives the architecture, the contract, the measured results and the limits.

Stage 8 is a **deterministic replay** of synthetic, fictional matches, served from one local
process. It is not hosted, not production real-time broadcasting, and uses no Foundry, LLM or
Azure service. The rule-based path is the evaluated path.

## Architecture

```
observable events ─► LifecycleEngine (Stage 7, unchanged) ─► LifecycleState
                                   │                              │
                                   └──────── event log ───────────┤
                                                                  ▼
   matcheyes.broadcast.CueCompiler (per surface: fan / broadcaster × no club / home / away)
     per closed snapshot, in order:
       moments    ◄─ Stage 3 key-event facts + period markers of the events new in this snapshot
       cards      ◄─ current revisions ∩ Stage 6 primary feed of this snapshot
       revisions / retractions ◄─ displayed cards whose revision changed or stopped being current
       status     ◄─ lifecycle status (current / no_candidate / unavailable / data_incomplete)
   ─► CueTimeline (content-addressed, ordered by match time)

matcheyes.api
   LiveMatch (one per match, server-owned thread, replay clock = match time / speed)
     ─► one LifecycleEngine + six CueCompilers ─► published cues (in memory)
   BroadcastServer (http.server, ThreadingHTTPServer)
     GET /health · /matches · /matches/{id}/timeline · /matches/{id}/stream (SSE) · /static/*
   static page (index.html, app.css, app.js) ─ renders cue sections; no football reasoning
```

`broadcast` imports the domain, analytics, agents, orchestration, personalization and lifecycle
layers, but no clock, randomness, network, process or persistence module. `api` uses an allow-list
of standard-library modules. Neither imports the generator or the evaluation package. pydantic is
still the only runtime dependency.

## Cue contract

| Kind | Emitted | Text | Leaves the screen |
| --- | --- | --- | --- |
| `moment` | at the close of the first snapshot containing the event | Stage 3 key-event fact or a period template | after 2 match minutes |
| `insight` | on the snapshot whose current revision enters a free card slot | Stage 6 view of that snapshot, verbatim | when revised or retracted |
| `revision` | on the snapshot that revised a displayed card | the new Stage 6 view, plus the lifecycle notice when it interrupts | when revised or retracted |
| `retraction` | on the snapshot where a displayed card stops being current or primary | fixed template with the reason, and the retracted card's fact | after 2 match minutes |
| `status` | when the lifecycle status changes; `awaiting_snapshot` at kick-off | fixed template | when a later status replaces it |

Every cue carries a typed source (event IDs, or storyline, revision, insight fingerprint and
Stage 6 view hash, or the retracted cue, or the status and watermark) and its snapshot ID. Cue
and timeline IDs are SHA-256 content addresses of their content; no wall clock, request,
connection or random value is part of any identity. Validators enforce kinds, sources, expiry,
audiences, single supersession, ordering and that every superseded cue appears earlier.

## Timing

* A moment is shown at the close of the next canonical snapshot, so at most one match minute after
  the event (measured median 36–39 s, maximum 60 s).
* Insight, revision and retraction cues are shown at the `as_of` of the snapshot that established
  the change. Stage 8 adds no latency to insights; their latency is Stage 2 detection latency,
  inherited through Stage 7.
* The compiled timeline is a pure function of the lifecycle state and event log: compiling at
  once, or snapshot by snapshot as the log grows, gives the same cues.
* Live, the replay clock only decides *when* an event is fed to the engine (match time / `speed`);
  it never changes what a cue contains.

## Selection

* Two broadcast audiences (fan, broadcaster); the analyst view is not a broadcast surface.
* At most three insight cards. Free slots are filled in Stage 6 primary-feed order. Nothing is
  displaced: a card leaves only through a revision or a retraction.
* Revisions interrupt only for a material change other than re-anchoring. Re-anchor-only,
  evidence-only and audit-only revisions replace the card silently.
* Fixed priorities: retraction 90, goal and red card 80, interrupting revision 70, insight 60,
  other moments 40, silent revision 20, status 10.
* Within a snapshot: moments, then changes to displayed cards, then new cards, then status.

## Notice-text fix

The Stage 7 `explanation_changed` notice named the leading explanation even when only the
alternatives changed ("leading explanation none -> none"). It now names only fields that changed.
Truth, change kinds and revision semantics are unchanged.

## Security

* Read-only API: only `GET`; every other method returns 405. No ingest, mutation, debug or truth
  endpoint. Match IDs are an allow-list loaded at start-up; queries accept only `audience` and
  `club` with allow-listed values; paths are length-bounded (414); concurrent streams are bounded
  (503); errors are short JSON messages without internals; the request log strips control
  characters.
* Headers: a strict Content Security Policy (`default-src 'none'`; `script-src`, `style-src`,
  `connect-src`, `img-src 'self'`; `base-uri`, `form-action`, `frame-ancestors 'none'`),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  same-origin opener and resource policies, `Cache-Control: no-store`.
* Static page: no inline script, style or event handlers; text is set with `textContent` only;
  no storage, cookies, credentials or external origins; it reads an allow-listed set of cue
  fields and only fetches `/matches` and the match stream.
* Hidden truth: `broadcast` and `api` neither import nor name `matcheyes_synth` or
  `matcheyes_eval`; a subprocess probe confirms neither is loaded while serving; cue schemas carry
  no hidden vocabulary; every served timeline and HTTP response is scanned for scenario, planted
  truth and generator vocabulary.

These are enforced by `tests/architecture/test_stage8_security.py` and `tests/api/`.

## Methodology

`python -m matcheyes_eval stage8` replays every match of a split through `LifecycleEngine` (the
reference) and, independently, folds the snapshot evaluations to get the lifecycle state after
every snapshot. It compiles six surfaces per match and checks each cue against those states, the
raw observable events and the Stage 6 views. On-screen state is re-derived from the cue contract
alone (show from, expire, supersede). Properties:

| # | Property | Measured as |
| --- | --- | --- |
| 1 | Traceability | every card cue names a revision current on its snapshot and carries that snapshot's Stage 6 view; every moment is an observable event with Stage 3 text; retractions and statuses use the fixed templates; timelines re-validate from JSON |
| 2 | Retraction completeness | a card whose revision stops being current is revised or retracted on that very snapshot, for the right reason |
| 3 | Stale minutes | snapshot-minutes with a non-current card on screen, or a status disagreeing with the lifecycle |
| 4 | Moments | goals, dismissals, substitutions and period markers found directly in the events, each shown exactly once within one snapshot |
| 5 | First output | match minute of the first cue, moment, goal or red card and insight card, against the first current lifecycle revision |
| 6 | Tautology | notices naming an unchanged value (`X -> X`), and what the pre-fix template gives on the same revisions |
| 7 | Overlay load | cues on screen per snapshot, cards per snapshot, interrupting cues per snapshot |
| 8 | Offline equals live | the server's shared replay, the timeline endpoint and the SSE stream (and 16 concurrent streams) equal the offline timelines, byte for byte, on the first two matches |
| 9 | Order independence | a shuffled delivery compiles to the canonical timeline; compiled after every event it gives the same non-status cues and the canonical final status |
| 10 | Security | no scenario, planted-truth or generator vocabulary in any timeline or HTTP response |

Also: card limit, silent re-anchor and evidence-only revisions, determinism, and a pipeline
failure injected on a snapshot with cards on screen. No targets were set; results are reported as
measured. Commands:

```
uv run python -m matcheyes_eval stage8 --split development --seeds 1 --live-matches 2
uv run python -m matcheyes_eval stage8 --split held-out --seeds 1 --live-matches 2
```

## Results

1 seed per scenario, 17 matches per split, 102 surfaces per split.

| Property | Development | Held-out |
| --- | --- | --- |
| Snapshots | 1,669 | 1,696 |
| Cues (moment / insight / revision / retraction / status) | 1,500 / 448 / 376 / 154 / 306 | 1,536 / 358 / 342 / 88 / 306 |
| Reference fold reproduces the engine | 17/17 | 17/17 |
| Traceability: card / moment / retraction and status cues | 824/824 / 1,500/1,500 / 460/460 | 700/700 / 1,536/1,536 / 394/394 |
| Timelines re-validate from JSON | 102/102 | 102/102 |
| Card superseded on the snapshot it stops being current | 530/530 | 430/430 |
| Retraction reason correct | 154/154 | 88/88 |
| Stale card-minutes / stale status-minutes | 0 / 0 | 0 / 0 |
| Moments shown within one snapshot; no duplicate or unexpected | 250/250; 17/17 | 256/256; 17/17 |
| Moment delay event to cue (median / max) | 38.7 s / 60.0 s | 36.3 s / 60.0 s |
| Notices on material revisions naming an unchanged value | 0 of 563 | 0 of 439 |
| Same revisions under the pre-fix template | 67 | 58 |
| Interrupting revisions / silent revisions | 66 / 310 | 66 / 276 |
| At most three cards on screen | 10,014/10,014 | 10,176/10,176 |
| Re-anchor and evidence-only revisions silent | 376/376 | 342/342 |
| Offline equals live: server replay / timeline endpoint / SSE | 12/12 each | 12/12 each |
| 16 concurrent streams receive identical cues | 2/2 | 2/2 |
| Shuffled: final timeline / live non-status cues / final status | 17/17 each | 17/17 each |
| Determinism | 17/17 | 17/17 |
| Injected failure: every card retracted, status unavailable | 17/17 | 17/17 |
| Leaked hidden-truth tokens | 0 | 0 |

Retraction reasons: development 124 `no_verified_explanation`, 30 `withdrawn`; held-out 88
`no_verified_explanation`. No `unavailable` retraction appears in the reference runs; the
failure-injection runs (one per match, all passing) are separate and not counted in the cue
totals.

First output, fan surface, match minute (median; min–max):

| | Development | Held-out |
| --- | --- | --- |
| First cue (kick-off) | 1.0; 1–1 | 1.0; 1–1 |
| First goal or red card | 22.0; 8–43 | 37.5; 5–90 |
| First insight card | 32.0; 30–62 | 47.0; 30–78 |
| Baseline: first current lifecycle revision | 31.0; 30–46 | 30.0; 30–41 |

Overlay load (cues on screen excluding status, per snapshot) reaches six at most: three cards
plus moments and retractions in their two-minute window. Held-out: 0 cues on 2,166 surface
snapshots, 1–3 on 2,482, 4–6 on 440; interrupting cues per snapshot 0 on 4,732, 1 on 327, 2 on 8,
3 on 21. Fan and broadcaster surfaces have identical load: placement selects the same insights
for both, and only the wording differs.

## Baseline versus Stage 8

The baseline is the Stage 8 discovery, which measured what a viewer of the Stage 7 lifecycle feed
would see on ten matches (a different match set from the evaluation splits):

| | Stage 7 feed (discovery) | Stage 8 surface (held-out) |
| --- | --- | --- |
| First output | minute 31–47 | minute 1 (kick-off), every match |
| Goals and red cards shown | 0 of 34 | every goal and red card, within one snapshot (256/256 moments) |
| Notices naming an unchanged value | 42 of 343 | 0 of 439 (58 under the old template) |
| Re-anchor-only notices interrupting | 79 of 343 | 0 |
| Retractions | implicit (withdrawn list) | explicit cue on the same snapshot, 0 stale minutes |

## Performance

Measured on a Windows 11 development laptop. The evaluation timings above ran the development and
held-out evaluations concurrently, so they are upper bounds.

| Measurement | Result |
| --- | --- |
| Server start-up to first `/health` (new process, 2 matches, includes interpreter start) | 1.1 s |
| Server memory (working set): idle / after one full match / after two | 49 MB / 70 MB / 94 MB (peak 95 MB) |
| Full live replay of one match at `--speed 0`, uncached, 6 surfaces | 37 s and 78 s (two matches) |
| Same, evaluations cached, 6 surfaces (evaluation) | median 7.4 s dev, 20.6 s held-out (contended) |
| Offline compile of one surface (evaluation) | median 2.0 s dev, 1.6 s held-out |
| SSE time to first byte | median 2.2–2.3 ms, max 6.7 ms |
| Full stream of a finished match (one connection) | median 2.8–3.1 ms |
| 16 concurrent streams of a finished match | median 54 ms, max 63 ms |

Most of the replay cost is the Stage 6 feed audit run on each surface's snapshots (Stage 6 is not
changed in Stage 8). An uncached full match costs about 0.4–0.8 s per snapshot, so at the default
`--speed 20` (3 s of wall time per match minute) the replay keeps pace with its clock. Memory
grows with the number of replayed matches; the per-match evaluation cache is bounded.

## Limitations

* English only; no user study; usefulness to real audiences is unmeasured.
* In-memory, single process, no persistence, no authentication, no accounts. The server is meant
  for localhost; it has no TLS and no rate limiting beyond the stream bound.
* No Foundry or LLM, no Azure, no Event Hubs or Cosmos DB: transport and hosting are later
  decisions.
* Synthetic, fictional matches only.
* Insight latency is inherited from Stage 2 detection. The first insight card can come later than
  the first current lifecycle revision (held-out median minute 47 against 30) because only
  revisions placed on the Stage 6 primary feed become cards; tentative ones stay off the surface.
* Moments are shown up to one minute late by design (next snapshot close); there is no event-time
  path.
* Moments and retractions can bring the overlay to six items at once; only cards are capped.
* The "no idle card while a primary insight waits" check was never triggered (0 cases): in every
  evaluated snapshot with a free slot, every primary current revision was already shown.
* Live equivalence over HTTP is measured on the first two matches of each split (12 surfaces);
  the rest are checked offline and under shuffled live compilation.
* Recomputing Stage 6 feeds dominates replay cost; very high `--speed` values replay faster than
  the clock only if evaluations are cached.

## Demo workflow

```
uv run python -m matcheyes cues data/fixtures/minimal_match --audience fan --json cues.json
uv run python -m matcheyes_synth generate --scenario S05_red_card_reorganisation --out demo
uv run python -m matcheyes serve demo/observable --speed 20
```

Open <http://127.0.0.1:8000>, choose the match, audience (fan or broadcaster) and favourite club.
The page shows the clock, the score, the status, up to three insight cards, key moments and a log
of revisions and retractions. At `--speed 20` a match replays in about five minutes, then the
replay restarts after a pause (new edition) unless `--no-loop` is given.
