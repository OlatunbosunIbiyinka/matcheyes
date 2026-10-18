"""The live driver: one shared, server-owned replay per match, paced by a replay clock.

The server, never the browser, owns the replay. A single thread per match feeds the observable
events, in sequence order, to one `LifecycleEngine`, and after each event advances one
`CueCompiler` per surface (fan and broadcaster, each with no favourite club or either club).
Viewers only read what has been published, so no viewer causes any recomputation.

The clock only decides *when* an event is fed: the match time of an event divided by `speed`.
Cue content and IDs depend only on the lifecycle state and the events, so the speed cannot
change them. Snapshot evaluations are cached by snapshot ID (a content address), so a looping
replay re-establishes the same truth without re-running the pipeline.

Each ingest that closes snapshots publishes, per surface, the clock tick, then one `stats` message
per new snapshot in snapshot order (ADR-0016), then that ingest's cues. Statistics are built from
the snapshot's own prefix, never from its reasoning: from the validity report and Stage 2 team
analytics the evaluation already computed on that prefix when the evaluation cache holds them,
otherwise from the event log. They are cached by snapshot ID without an edition; the edition is
added when the message is formed.

Lifecycle (ADR-0017): READY until the first request starts the replay, then REPLAYING and, when
looping, PAUSED between editions; FINISHED after the last edition of a non-looping replay. An
exception, an unexpected thread exit or a stall makes the match FAILED, which is sticky: nothing
is published afterwards and only an operator recovers it. `stop()` moves it to STOPPING, then
STOPPED once the thread has exited. A match whose recording is unusable starts, and stays,
RECORDING_UNAVAILABLE. Each `LiveMatch` has a random 128-bit `run` identity for its lifetime.
"""

import json
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from matcheyes.broadcast.compiler import CueCompiler, WorkspaceCache
from matcheyes.broadcast.contracts import Cue, CueTimeline, TeamRef
from matcheyes.broadcast.stats import (
    POOLED_SERIES,
    SnapshotStats,
    snapshot_stats,
    snapshot_stats_from,
    stats_message,
    unavailable_stats,
)
from matcheyes.domain.entities import Identifier, TeamSheet
from matcheyes.domain.events import MatchEvent, PeriodEnd
from matcheyes.domain.match import ObservableMatch
from matcheyes.ingestion.log import EventLog
from matcheyes.lifecycle.contracts import LifecycleState, Revision, SnapshotHeader
from matcheyes.lifecycle.engine import LifecycleEngine
from matcheyes.lifecycle.evaluate import (
    AnalysedEvaluation,
    SnapshotAnalytics,
    SnapshotEvaluation,
    evaluate_and_analyse,
)
from matcheyes.lifecycle.explain import REFERENCE, ReasonerIdentity, RevisionExplanation
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.personalization.contracts import Audience, PersonalizationProfile

AUDIENCES = (Audience.FAN, Audience.BROADCASTER)
EVALUATION_CACHE_LIMIT = 400
"""More than the snapshots of one match (about 100); bounds memory per match."""

STATS_CACHE_LIMIT = EVALUATION_CACHE_LIMIT

READY = "ready"
REPLAYING = "replaying"
PAUSED = "paused"
FINISHED = "finished"
FAILED = "failed"
STOPPING = "stopping"
STOPPED = "stopped"
RECORDING_UNAVAILABLE = "recording_unavailable"
FAILABLE = (READY, REPLAYING, PAUSED)
SERVING = (READY, REPLAYING, PAUSED, FINISHED)
STALL_SECONDS = 120.0
"""Provisional, not a measured limit: an overdue event with no progress for this long fails."""

STACK_FRAMES = 20

SurfaceKey = tuple[Audience, Identifier | None]
Evaluator = Callable[[Snapshot], SnapshotEvaluation]
Analyse = Callable[[Snapshot], AnalysedEvaluation]
Explain = Callable[[Revision], RevisionExplanation]
StatsBuilder = Callable[[Snapshot], SnapshotStats]


@dataclass(frozen=True)
class Message:
    """One published stream message: a canonical cue, a transport-only clock tick, or the
    statistics of one snapshot."""

    event: str
    index: int
    data: str


@dataclass
class Surface:
    profile: PersonalizationProfile
    compiler: CueCompiler
    messages: list[Message] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)
    watermark: int = 0
    snapshots: int = 0


@dataclass(frozen=True)
class Backend:
    """How one match is reasoned: its snapshot evaluator, who reasons, and who shows the work."""

    evaluator: Evaluator | None = None
    reasoner: ReasonerIdentity = REFERENCE
    explainer: Explain | None = None
    available: bool = True
    """False when the pinned recording is unusable: the match is never replayed."""


@dataclass
class Edition:
    number: int
    surfaces: dict[SurfaceKey, Surface]
    done: bool = False
    state: LifecycleState | None = None
    """The lifecycle state as far as this edition has replayed: what has been published."""


class CachingEvaluator:
    """Evaluations by snapshot ID. Unless given a plain `base` evaluator, it evaluates with
    `analyse` (the reference pipeline by default) and also keeps what each evaluation established
    about its prefix for the statistics to reuse: the validity report and only the Stage 2
    summaries and pooled series they read. Both are bounded together and evicted together."""

    def __init__(self, base: Evaluator | None = None, analyse: Analyse | None = None) -> None:
        if base is not None and analyse is not None:
            raise ValueError("give a plain evaluator or an analysing one, not both")
        self.base = base
        self.analyse: Analyse = analyse or evaluate_and_analyse
        self.cache: dict[str, SnapshotEvaluation] = {}
        self.analytics: dict[str, SnapshotAnalytics] = {}

    def __call__(self, snapshot: Snapshot) -> SnapshotEvaluation:
        key = snapshot.header.snapshot_id
        if key not in self.cache:
            if len(self.cache) >= EVALUATION_CACHE_LIMIT:
                self.cache.clear()
                self.analytics.clear()
            if self.base is not None:
                self.cache[key] = self.base(snapshot)
            else:
                result = self.analyse(snapshot)
                self.cache[key] = result.evaluation
                self.analytics[key] = _for_stats(result.analytics)
        return self.cache[key]


def _for_stats(analytics: SnapshotAnalytics) -> SnapshotAnalytics:
    if analytics.series is None:
        return analytics
    series = {k: s for k, s in analytics.series.items() if k[0] in POOLED_SERIES}
    return replace(analytics, series=series)


class StatsCache:
    """Snapshot statistics by snapshot ID, a content address of the match, watermark, minute,
    prefix and team sheet. Values carry no edition. Bounded like the evaluation cache; counts
    builds, how many of them reused the evaluation's analytics, and hits. `builder` is the
    path that computes everything from the prefix, used whenever nothing can be reused."""

    def __init__(self, builder: StatsBuilder = snapshot_stats, limit: int | None = None) -> None:
        self.builder = builder
        self.limit = STATS_CACHE_LIMIT if limit is None else limit
        self.cache: dict[str, SnapshotStats] = {}
        self.calls = 0
        self.reused = 0
        self.hits = 0

    def __call__(
        self,
        snapshot_id: str,
        snapshot: Callable[[], Snapshot],
        reuse: Callable[[], SnapshotStats] | None = None,
    ) -> SnapshotStats:
        found = self.cache.get(snapshot_id)
        if found is not None:
            self.hits += 1
            return found
        self.calls += 1
        if reuse is not None:
            self.reused += 1
            built = reuse()
        else:
            built = self.builder(snapshot())
        if built.snapshot_id != snapshot_id:
            raise ValueError("the statistics describe another snapshot")
        if len(self.cache) >= self.limit:
            self.cache.clear()
        if self.limit > 0:
            self.cache[snapshot_id] = built
        return built


def _reusing(snapshot: Snapshot, found: SnapshotAnalytics) -> Callable[[], SnapshotStats]:
    def build() -> SnapshotStats:
        summaries, series = found.summaries or (), found.series or {}
        return snapshot_stats_from(snapshot, found.validity, summaries, series)

    return build


def prefix_matches(log: EventLog, header: SnapshotHeader) -> bool:
    """Whether the log still holds the exact prefix the snapshot was taken on."""
    return header.watermark <= log.watermark and log.digest(header.watermark) == header.log_digest


def match_seconds(events: tuple[MatchEvent, ...]) -> list[float]:
    """Continuous match time of each event: the second half starts where the first ended."""
    first_half = max(
        (e.clock_ms for e in events if isinstance(e, PeriodEnd) and e.period == 1),
        default=max((e.clock_ms for e in events if e.period == 1), default=0),
    )
    return [(e.clock_ms + (first_half if e.period == 2 else 0)) / 1000 for e in events]


def clock_tick(edition: int, header: SnapshotHeader) -> str:
    return json.dumps(
        {
            "edition": edition,
            "snapshot_id": header.snapshot_id,
            "minute": header.label,
            "as_of": header.as_of.display_minute,
            "period": header.as_of.period,
            "clock_ms": header.as_of.clock_ms,
            "watermark": header.watermark,
        },
        separators=(",", ":"),
    )


def _team(sheet: TeamSheet) -> TeamRef:
    return TeamRef(team_id=sheet.team_id, name=sheet.club.name, short_name=sheet.club.short_name)


def _error_frames(error: BaseException) -> list[str]:
    """The innermost frames of an exception's traceback, outermost first."""
    rows: list[str] = []
    tb = error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        rows.append(f"  {code.co_filename}:{tb.tb_lineno} in {code.co_name}")
        tb = tb.tb_next
    return rows[-STACK_FRAMES:]


def _thread_frames(ident: int | None) -> list[str]:
    """The innermost frames a running thread is in, outermost first."""
    rows: list[str] = []
    frame = sys._current_frames().get(ident) if ident is not None else None
    while frame is not None:
        code = frame.f_code
        rows.append(f"  {code.co_filename}:{frame.f_lineno} in {code.co_name}")
        frame = frame.f_back
    return rows[:STACK_FRAMES][::-1]


def _quantile(ordered: list[float], q: float) -> float:
    return ordered[int(q * (len(ordered) - 1))] if ordered else 0.0


@dataclass
class EditionMetrics:
    """What one edition's replay measured, logged once when the edition ends."""

    began: float
    steps: list[float] = field(default_factory=list)
    max_gap: float = 0.0
    max_lag: float = 0.0
    end_lag: float = 0.0

    def record(self, started: float, ended: float, previous: float, due: float) -> None:
        self.steps.append(ended - started)
        self.max_gap = max(self.max_gap, ended - max(previous, due))
        self.end_lag = ended - due
        self.max_lag = max(self.max_lag, self.end_lag)

    def line(self, match_id: str, edition: int, pause_before: float, ended: float) -> str:
        ordered = sorted(self.steps)
        return (
            f"replay edition: {match_id} edition {edition} events {len(ordered)} "
            f"seconds {ended - self.began:.3f} pause_before {pause_before:.3f} "
            f"ingest_p50 {_quantile(ordered, 0.5):.4f} ingest_p99 {_quantile(ordered, 0.99):.4f} "
            f"ingest_max {_quantile(ordered, 1.0):.4f} max_progress_gap {self.max_gap:.3f} "
            f"max_lag {self.max_lag:.3f} end_lag {self.end_lag:.3f}\n"
        )


class LiveMatch:
    def __init__(
        self,
        match: ObservableMatch,
        speed: float,
        loop: bool = True,
        pause: float = 20.0,
        evaluator: Evaluator | None = None,
        reasoner: ReasonerIdentity = REFERENCE,
        explainer: Explain | None = None,
        stats: bool = True,
        stall_seconds: float = STALL_SECONDS,
        available: bool = True,
    ) -> None:
        """`speed`: match seconds per wall second; 0 replays as fast as possible.
        `reasoner` names who reasoned (presentation only); `explainer` shows a revision's work.
        `stats` publishes snapshot statistics; off only to measure their cost.
        `stall_seconds`: how long an overdue event may make no progress before the match fails.
        `available`: False when its recording is unusable; it is then never replayed."""
        if speed < 0:
            raise ValueError("speed must be non-negative")
        if stall_seconds <= 0:
            raise ValueError("stall_seconds must be positive")
        self.match = match
        self.speed = speed
        self.loop = loop
        self.pause = pause
        self.evaluator = evaluator or CachingEvaluator()
        self.reasoner = reasoner
        self.explainer = explainer
        self.stats_cache: StatsCache | None = StatsCache() if stats else None
        self.events = tuple(sorted(match.events, key=lambda e: e.sequence))
        self.times = match_seconds(self.events)
        clubs = (None, match.info.home.team_id, match.info.away.team_id)
        self.profiles = {
            (a, c): PersonalizationProfile(audience=a, favourite_club_id=c)
            for a in AUDIENCES
            for c in clubs
        }
        self.condition = threading.Condition()
        self.edition = self._edition(0, {})
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.replay_seconds: list[float] = []
        self.run = secrets.token_hex(16)
        self.stall_seconds = stall_seconds
        self.state = READY if available else RECORDING_UNAVAILABLE
        self.failure: str | None = None
        now = time.monotonic()
        self.due_at = self.last_progress = now
        self.pause_until = now
        self.ingested = 0
        self._pause_before = 0.0

    def _edition(self, number: int, workspaces: WorkspaceCache) -> Edition:
        return Edition(
            number,
            {
                key: Surface(profile, CueCompiler(self.match.info, profile, workspaces))
                for key, profile in self.profiles.items()
            },
        )

    def surface(self, audience: Audience, club: Identifier | None) -> SurfaceKey:
        key = (audience, club)
        if key not in self.profiles:
            raise KeyError("unknown surface")
        return key

    def start(self) -> None:
        """Start the replay on first use; does nothing in any state but READY."""
        line = None
        with self.condition:
            if self.state != READY or self._thread is not None:
                return
            self._set(REPLAYING)
            self._thread = threading.Thread(target=self._run, daemon=True)
            try:
                self._thread.start()
            except RuntimeError as error:
                line = self._fail_locked("thread_exited", error)
        if line is not None:
            sys.stderr.write(line)

    def request_stop(self) -> None:
        """Ask the replay to stop, without waiting for it."""
        self._stop.set()
        with self.condition:
            if self.state not in (FAILED, RECORDING_UNAVAILABLE, STOPPED):
                self._set(STOPPING)
            self.condition.notify_all()

    def join(self, timeout: float) -> bool:
        """Wait up to `timeout` for the replay thread; True once it has exited (or never ran)."""
        thread = self._thread
        if thread is not None:
            thread.join(max(0.0, timeout))
            if thread.is_alive():
                return False
        with self.condition:
            if self.state == STOPPING:
                self._set(STOPPED)
        return True

    def stop(self, timeout: float = 30.0) -> bool:
        self.request_stop()
        return self.join(timeout)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def serving(self) -> bool:
        """Whether streams, timelines and explanations may be served from this match."""
        return self.state in SERVING

    def _set(self, state: str) -> None:
        self.state = state
        self.condition.notify_all()

    def _run(self) -> None:
        number = 0
        try:
            while not self._stop.is_set():
                self._replay(number)
                with self.condition:
                    if self.state != REPLAYING or self._stop.is_set():
                        return
                    if not self.loop:
                        self._set(FINISHED)
                        return
                    paused = time.monotonic()
                    self.pause_until = paused + self.pause
                    self._set(PAUSED)
                if self._stop.wait(self.pause):
                    return
                with self.condition:
                    if self.state != PAUSED:
                        return
                    self._pause_before = time.monotonic() - paused
                    self._set(REPLAYING)
                number += 1
        except Exception as error:
            self.fail("replay_error", error)
        except BaseException as error:
            self.fail("thread_exited", error)
            raise
        finally:
            if not self._stop.is_set():
                self.fail("thread_exited")

    def _halted(self) -> bool:
        """Under `condition`: whether nothing may be published any more."""
        return self.state in (FAILED, STOPPING, STOPPED)

    def _replay(self, number: int) -> None:
        workspaces: WorkspaceCache = {}
        edition = self._edition(number, workspaces)
        began = time.monotonic()
        metrics = EditionMetrics(began)
        with self.condition:
            if self._halted():
                return
            self.edition = edition
            self.ingested = 0
            self.due_at = self.last_progress = began
            self.condition.notify_all()
        engine = LifecycleEngine(self.match.info, self.evaluator)
        snapshots = 0
        for event, at in zip(self.events, self.times, strict=True):
            if self.speed > 0:
                due = began + at / self.speed
                self.due_at = due
                delay = due - time.monotonic()
                if delay > 0 and self._stop.wait(delay):
                    return
            elif self._stop.is_set():
                return
            else:
                due = self.due_at = time.monotonic()
            started = time.monotonic()
            engine.ingest(event)
            state, prefix = engine.state, engine.log.events()
            data_status = engine.status().data_status
            new = {
                key: s.compiler.advance(state, prefix, data_status)
                for key, s in edition.surfaces.items()
            }
            workspaces.clear()
            ended = time.monotonic()
            metrics.record(started, ended, self.last_progress, due)
            self.last_progress = ended
            self.ingested += 1
            tick = None
            stats: list[str] = []
            if len(state.snapshots) > snapshots:
                closed = state.snapshots[snapshots:]
                snapshots = len(state.snapshots)
                tick = clock_tick(number, state.snapshots[-1].header)
                if self.stats_cache is not None:
                    stats = [
                        stats_message(number, self._stats(self.stats_cache, r.header, engine.log))
                        for r in closed
                    ]
            with self.condition:
                if self._halted():
                    return
                if tick is None and not any(new.values()):
                    continue
                edition.state = state
                for key, s in edition.surfaces.items():
                    if tick is not None:
                        s.messages.append(Message("clock", len(s.messages), tick))
                    for data in stats:
                        s.messages.append(Message("stats", len(s.messages), data))
                    for cue in new[key]:
                        s.messages.append(Message("cue", len(s.messages), cue.model_dump_json()))
                    s.cues.extend(new[key])
                    s.watermark, s.snapshots = s.compiler.watermark, s.compiler.snapshots
                self.condition.notify_all()
        with self.condition:
            if self._halted():
                return
            edition.done = True
            ended = time.monotonic()
            self.replay_seconds.append(ended - began)
            self.condition.notify_all()
        mid = self.match.info.match_id
        sys.stderr.write(metrics.line(mid, number, self._pause_before, ended))

    def fail(self, category: str, error: BaseException | None = None) -> bool:
        """Make the match FAILED (sticky) from READY, REPLAYING or PAUSED; only the first call
        transitions and logs. The log line goes to stderr, never into an HTTP response."""
        with self.condition:
            line = self._fail_locked(category, error)
        if line is not None:
            sys.stderr.write(line)
        return line is not None

    def _fail_locked(self, category: str, error: BaseException | None) -> str | None:
        if self.state not in FAILABLE:
            return None
        self._set(FAILED)
        self.failure = category
        thread = self._thread
        if error is not None:
            frames = _error_frames(error)
        elif category == "stalled" and thread is not None:
            frames = _thread_frames(thread.ident)
        else:
            frames = []
        kind = "" if error is None else f" {type(error).__name__}"
        head = (
            f"replay failed: {self.match.info.match_id} edition {self.edition.number} "
            f"event {self.ingested}/{len(self.events)} category={category}{kind}"
        )
        return "".join(f"{row}\n" for row in (head, *frames))

    def _problem(self, now: float) -> str | None:
        """Under `condition`: the failure a passive check sees, if any."""
        thread = self._thread
        if self.state not in (REPLAYING, PAUSED):
            return None
        if thread is not None and not thread.is_alive():
            return "thread_exited"
        if self.state == PAUSED:
            return "stalled" if now > self.pause_until + self.stall_seconds else None
        gap = now - max(self.last_progress, self.due_at)
        return "stalled" if gap > self.stall_seconds else None

    def check_progress(self) -> bool:
        """The passive stall and thread check; True if the match is (now) FAILED."""
        line = None
        with self.condition:
            problem = self._problem(time.monotonic())
            if problem is not None:
                line = self._fail_locked(problem, None)
            failed = self.state == FAILED
        if line is not None:
            sys.stderr.write(line)
        return failed

    def health(self, deadline: float) -> str | None:
        """Why this match is not ready (`replay`, `recording`, `stopping`), or None if it is.
        Waits for the lock no later than `deadline`; a lock held past it counts as `replay`."""
        if not self.condition.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return "replay"
        line = None
        try:
            problem = self._problem(time.monotonic())
            if problem is not None:
                line = self._fail_locked(problem, None)
            state = self.state
        finally:
            self.condition.release()
        if line is not None:
            sys.stderr.write(line)
        reasons = {
            FAILED: "replay",
            RECORDING_UNAVAILABLE: "recording",
            STOPPING: "stopping",
            STOPPED: "stopping",
        }
        return reasons.get(state)

    def _stats(self, cache: StatsCache, header: SnapshotHeader, log: EventLog) -> SnapshotStats:
        """The statistics of a closed snapshot, from its own prefix in the log. Anything that
        stops them being built (a prefix that no longer matches the header, an unexpected
        error) gives the fixed unavailable message, never values from another prefix."""

        def snapshot(events: tuple[MatchEvent, ...] = ()) -> Snapshot:
            match = ObservableMatch(info=self.match.info, events=events)
            return Snapshot(header=header, match=match)

        try:
            if not prefix_matches(log, header):
                return unavailable_stats(snapshot())
            found = self._analytics(header)
            reuse = None if found is None else _reusing(snapshot(), found)
            return cache(
                header.snapshot_id, lambda: snapshot(log.events()[: header.watermark]), reuse
            )
        except Exception:  # never let statistics stop the replay or leak an error
            return unavailable_stats(snapshot())

    def _analytics(self, header: SnapshotHeader) -> SnapshotAnalytics | None:
        """What this match's evaluation established about exactly this snapshot's prefix, if it
        is still held and complete enough to build statistics from; otherwise None."""
        if not isinstance(self.evaluator, CachingEvaluator):
            return None
        found = self.evaluator.analytics.get(header.snapshot_id)
        if found is None or (found.snapshot_id, found.log_digest) != (
            header.snapshot_id,
            header.log_digest,
        ):
            return None
        if found.validity.ok and (found.summaries is None or found.series is None):
            return None
        return found

    def explain(self, storyline_id: str, number: int) -> tuple[int, RevisionExplanation] | None:
        """The work behind a published revision, or None if it is not published (yet) or this
        match has no explainer."""
        with self.condition:
            edition, state = self.edition.number, self.edition.state
        if self.explainer is None or state is None:
            return None
        storyline = next((s for s in state.storylines if s.storyline_id == storyline_id), None)
        if storyline is None or not 1 <= number <= len(storyline.revisions):
            return None
        return edition, self.explainer(storyline.revisions[number - 1])

    def timeline(self, key: SurfaceKey) -> tuple[int, bool, CueTimeline]:
        """The cues published so far on one surface, as a canonical timeline."""
        with self.condition:
            edition = self.edition
            s = edition.surfaces[key]
            cues, watermark, snapshots = tuple(s.cues), s.watermark, s.snapshots
            number, done = edition.number, edition.done
        timeline = CueTimeline.build(
            match_id=self.match.info.match_id,
            home=_team(self.match.info.home),
            away=_team(self.match.info.away),
            profile=s.profile,
            watermark=watermark,
            snapshots=snapshots,
            cues=cues,
        )
        return number, done, timeline
