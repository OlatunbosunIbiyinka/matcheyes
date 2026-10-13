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
"""

import json
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
    ) -> None:
        """`speed`: match seconds per wall second; 0 replays as fast as possible.
        `reasoner` names who reasoned (presentation only); `explainer` shows a revision's work.
        `stats` publishes snapshot statistics; off only to measure their cost."""
        if speed < 0:
            raise ValueError("speed must be non-negative")
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
        with self.condition:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self.condition:
            self.condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=30)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _run(self) -> None:
        number = 0
        while not self._stop.is_set():
            self._replay(number)
            if not self.loop or self._stop.wait(self.pause):
                return
            number += 1

    def _replay(self, number: int) -> None:
        workspaces: WorkspaceCache = {}
        edition = self._edition(number, workspaces)
        with self.condition:
            self.edition = edition
            self.condition.notify_all()
        engine = LifecycleEngine(self.match.info, self.evaluator)
        began = time.monotonic()
        snapshots = 0
        for event, at in zip(self.events, self.times, strict=True):
            if self.speed > 0:
                delay = began + at / self.speed - time.monotonic()
                if delay > 0 and self._stop.wait(delay):
                    return
            elif self._stop.is_set():
                return
            engine.ingest(event)
            state, prefix = engine.state, engine.log.events()
            data_status = engine.status().data_status
            new = {
                key: s.compiler.advance(state, prefix, data_status)
                for key, s in edition.surfaces.items()
            }
            workspaces.clear()
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
            if tick is None and not any(new.values()):
                continue
            with self.condition:
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
            edition.done = True
            self.replay_seconds.append(time.monotonic() - began)
            self.condition.notify_all()

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
