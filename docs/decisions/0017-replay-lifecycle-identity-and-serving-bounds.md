# ADR-0017: Replay lifecycle, run identity and serving bounds

- Status: Proposed (implemented and tested locally; the 0.5 vCPU container matrix and any Azure
  acceptance are not yet run)
- Date: 2026-10-15
- Stage: 10 (pre-deployment hardening)

## Context

The serving process ([ADR-0014](0014-broadcast-cue-contract-and-live-presentation-surface.md))
was built for local demos. Inspection before deployment
([ADR-0015](0015-foundry-model-as-untrusted-reasoner.md), deployment gate) found:

* A replay thread that raised or stalled left the process answering `/health` with 200 and
  streams waiting forever. Nothing reported the failure.
* A mis-pinned recording was served as if it were the pinned one.
* Stream IDs `edition:index` did not identify the server process, so a reconnect after a restart
  could splice two replays together (formerly a known issue, reproduced by
  `tests/api/test_edition_identity.py`).
* Connections were unbounded, a request head could be read slowly forever, and `ThreadingHTTPServer`
  read request bodies the API never needs.
* Shutdown had no deadline, and a wedged main thread had no way to end the process.

## Decision

### Replay lifecycle (`api/live.py`)

* Each `LiveMatch` has an explicit state: `ready`, `replaying`, `paused`, `finished`, `failed`,
  `stopping`, `stopped`, or `recording_unavailable` (a backend that cannot be served, such as a
  recording whose transcript does not match its pin).
* `failed` is reached only from `ready`, `replaying` or `paused`, and it is terminal. Categories:
  `replay_error` (an exception), `thread_exited` (the thread ended without being asked), `stalled`
  (no progress for more than `STALL_SECONDS` = 120 s beyond the event's due time or the pause's end).
  One stderr line names the match, edition, event position, category and exception class, followed by at
  most 20 stack frames (those of the stuck thread when stalled). Messages and payloads are never
  logged.
* Stall detection runs on demand: from the readiness probe and from idle stream slices. It is
  not a separate thread.
* Each finished edition writes one line of replay timing metrics (ingest quantiles, maximum
  progress gap, lag).

### Readiness and liveness

* `GET /health` is readiness. It answers 200 only when every match can be served, and otherwise 503 with
  `{"status": "unavailable", "reason": r}`, where `r` is `replay`, `recording` or `stopping`. All
  matches share one 1 s deadline, so a wedged match cannot hold the probe. The probe never starts
  a replay.
* Timelines and streams of a match that cannot be served answer 503 `replay unavailable`. An open
  stream whose replay fails receives `event: unavailable` and is closed. The page says the replay
  is unavailable and stops reconnecting.
* There is no HTTP liveness endpoint. Liveness is enforced in the process (watchdog, below).

### Run identity

* Each `LiveMatch` draws `run = secrets.token_hex(16)` at construction.
* Stream IDs are `{run}:{edition}:{index}`, and the edition marker's ID is `{run}:{edition}:start`.
  The `edition` event data is `{"edition": E, "run": "<run>"}`. The timeline and revision responses
  carry `run`.
* `Last-Event-ID` handling:
  * empty: start the current edition;
  * an older edition of the same run: start the current edition;
  * `start` or index `i` of the current edition: resume after it;
  * another run, a malformed value, a future edition or an index not yet published: 204 with no
    body, so the client rebuilds from a fresh connection. Pages that predate this change see a 204
    once and need one manual retry.
* The page compares `(edition, run)` for held messages and show-your-work responses, and says so
  when the server's replay restarted.
* Cue content, cue IDs and `BROADCAST_VERSION` (0.1.0) are unchanged.

### HTTP bounds (`api/server.py`)

* At most 64 connections (`MAX_CONNECTIONS`). A connection over the cap gets a fixed `503 server
  busy` with a non-blocking send and is closed. The accept loop never blocks on a client.
  Rejections are counted and reported at most every 10 s.
* One request per connection, which is then closed. The head must arrive within 10 s of accept and
  is limited to 16 KiB: an 8 KiB request line and 50 header lines. Bodies are refused
  (`Content-Length` or `Transfer-Encoding` gives 400). Only `HTTP/1.0` and `HTTP/1.1` are accepted
  (505 otherwise). Methods other than GET give 405.
* At most 32 streams (`MAX_STREAMS`, 503 `too many streams`). A stream sends a keep-alive every 15 s,
  checks its peer while idle, and releases its slot on every exit path.

### Watchdog and shutdown (`__main__.py`)

* `faulthandler.dump_traceback_later(30, exit=True)` is armed before the startup banner and before
  `serve_forever`. If that first arm fails, the process exits with `os._exit(1)` without writing a
  banner or accepting requests.
* The accept loop ticks at least every 0.5 s and re-arms the watchdog at most every 5 s. While
  unprotected (a re-arm failed), it retries on every tick and writes nothing to stderr from the main
  thread. If protection is not restored within 5 s, the process calls `os._exit(1)` without
  logging. Recovery is logged only after a successful arm.
* The first SIGTERM or SIGINT is recorded, and the accept loop stops at its next safe point.
  Shutdown then:
  1. marks the API stopping (readiness `stopping`);
  2. closes the listener;
  3. asks every replay to stop;
  4. drains streams;
  5. shuts down remaining connections;
  6. joins replays.

  All of this finishes within 10 s of the first signal. The exit code is 0 if everything finished and 1 otherwise, with one
  `shutdown deadline exceeded` line. On stop, the watchdog is marked stopped and cancelled
  unconditionally. A second signal exits at once with `128 + signal`.

### Module allow-list

The API may also import `secrets` (run identity) and `io.BytesIO` (parsing the bounded head).
`FileIO` joins the banned write vocabulary. Nothing else is added.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Liveness over HTTP (`/health/live`) | A probe can be served while the accept loop is wedged, or starved under load; the in-process watchdog bounds a frozen main thread directly |
| Restart a failed replay in-process | Hides failures; readiness 503 lets the platform replace the replica, and the failure stays visible |
| Fall back to the reference reasoner on a bad pin | A silent switch of reasoner (ADR-0015); the match is reported `recording` instead |
| Edition numbers only, or a process start time | Not unique across restarts or replicas; a random 128-bit run is |
| Keep-alive connections | Lets one client hold a thread indefinitely; one request per connection is enough for this API |
| Option C (log and keep serving when the watchdog cannot be armed) | Leaves a process with no frozen-thread bound; not approved |

## Consequences

* A failed, stalled or mis-pinned replay is visible: readiness 503, a stderr line, and the
  unavailable state in open pages.
* A server restart can no longer produce a spliced page. Reconnects to another run rebuild.
* Connection, head, stream and shutdown costs are bounded. A deliberate attack lasting a minute or more can
  still cause restarts. The watchdog trades that for bounded recovery from a frozen process.
* Some platform tests run only on POSIX: process signals, FIFO-based watchdog tests, and waking a
  pending head read on shutdown (on Windows, `shutdown()` does not wake a blocked `recv`).
* Still to do: the 0.5 vCPU container matrix (M1, LA8, LA9 and W-MAX, three runs each, with a main-loop tick
  gap of at most 3 s). The deployment gate of ADR-0015 is unchanged.
