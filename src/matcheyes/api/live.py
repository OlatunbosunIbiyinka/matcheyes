"""The live driver: one shared, server-owned replay per match, paced by a replay clock.

The server, never the browser, owns the replay. A single thread per match feeds the observable
events, in sequence order, to one `LifecycleEngine`, and after each event advances one
`CueCompiler` per surface (fan and broadcaster, each with no favourite club or either club).
Viewers only read what has been published, so no viewer causes any recomputation.

The clock only decides *when* an event is fed: the match time of an event divided by `speed`.
Cue content and IDs depend only on the lifecycle state and the events, so the speed cannot
change them. Snapshot evaluations are cached by snapshot ID (a content address), so a looping
replay re-establishes the same truth without re-running the pipeline.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from matcheyes.broadcast.compiler import CueCompiler, WorkspaceCache
from matcheyes.broadcast.contracts import Cue, CueTimeline, TeamRef
from matcheyes.domain.entities import Identifier, TeamSheet
from matcheyes.domain.events import MatchEvent, PeriodEnd
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle.contracts import SnapshotHeader
from matcheyes.lifecycle.engine import LifecycleEngine
from matcheyes.lifecycle.evaluate import SnapshotEvaluation, evaluate_snapshot
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.personalization.contracts import Audience, PersonalizationProfile

AUDIENCES = (Audience.FAN, Audience.BROADCASTER)
EVALUATION_CACHE_LIMIT = 400
"""More than the snapshots of one match (about 100); bounds memory per match."""

SurfaceKey = tuple[Audience, Identifier | None]
Evaluator = Callable[[Snapshot], SnapshotEvaluation]


@dataclass(frozen=True)
class Message:
    """One published stream message: a canonical cue, or a transport-only clock tick."""

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


@dataclass
class Edition:
    number: int
    surfaces: dict[SurfaceKey, Surface]
    done: bool = False


class CachingEvaluator:
    def __init__(self, base: Evaluator = evaluate_snapshot) -> None:
        self.base = base
        self.cache: dict[str, SnapshotEvaluation] = {}

    def __call__(self, snapshot: Snapshot) -> SnapshotEvaluation:
        key = snapshot.header.snapshot_id
        if key not in self.cache:
            if len(self.cache) >= EVALUATION_CACHE_LIMIT:
                self.cache.clear()
            self.cache[key] = self.base(snapshot)
        return self.cache[key]


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
    ) -> None:
        """`speed`: match seconds per wall second; 0 replays as fast as possible."""
        if speed < 0:
            raise ValueError("speed must be non-negative")
        self.match = match
        self.speed = speed
        self.loop = loop
        self.pause = pause
        self.evaluator = evaluator or CachingEvaluator()
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
            if len(state.snapshots) > snapshots:
                snapshots = len(state.snapshots)
                tick = clock_tick(number, state.snapshots[-1].header)
            if tick is None and not any(new.values()):
                continue
            with self.condition:
                for key, s in edition.surfaces.items():
                    if tick is not None:
                        s.messages.append(Message("clock", len(s.messages), tick))
                    for cue in new[key]:
                        s.messages.append(Message("cue", len(s.messages), cue.model_dump_json()))
                    s.cues.extend(new[key])
                    s.watermark, s.snapshots = s.compiler.watermark, s.compiler.snapshots
                self.condition.notify_all()
        with self.condition:
            edition.done = True
            self.replay_seconds.append(time.monotonic() - began)
            self.condition.notify_all()

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
