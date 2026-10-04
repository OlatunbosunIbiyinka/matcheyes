"""Possession sequences reconstructed from the event stream.

A possession is a maximal run of events in which one team controls the ball. Control is shown by
on-ball actions (pass, carry, take-on, shot) and by ball-winning actions (interception, ball
recovery, won tackle). Events that show no control (pressures, clearances, blocks, fouls,
cards, substitutions) never start or end a possession; they belong to whichever possession is
in progress. A kick-off always starts a new possession.
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field

from matcheyes.analytics.geometry import (
    in_attacking_third,
    in_penalty_box,
)
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.events import (
    BallRecovery,
    Carry,
    DuelOutcome,
    Interception,
    MatchEvent,
    OwnGoal,
    Pass,
    PassKind,
    PassOutcome,
    PeriodEnd,
    Shot,
    ShotKind,
    ShotOutcome,
    Tackle,
    TakeOn,
)
from matcheyes.domain.pitch import Location
from matcheyes.domain.time import Period

ON_BALL = (Pass, Carry, TakeOn, Shot)
REGAINS = (Interception, BallRecovery, Tackle)
OUT_OF_PLAY_RESTARTS = frozenset({PassKind.THROW_IN, PassKind.GOAL_KICK, PassKind.CORNER})


class StartType(StrEnum):
    KICK_OFF = "kick_off"
    SET_PIECE = "set_piece"
    REGAIN = "regain"
    OPEN_PLAY = "open_play"
    """Picked up a loose ball, e.g. a goalkeeper collecting a shot or a cleared ball."""


class EndReason(StrEnum):
    GOAL = "goal"
    SHOT = "shot"
    LOST_IN_PLAY = "lost_in_play"
    OUT_OF_PLAY = "out_of_play"
    FREE_KICK_CONCEDED = "free_kick_conceded"
    OFFSIDE = "offside"
    PERIOD_END = "period_end"
    STOPPAGE = "stoppage"


class Possession(DomainModel):
    index: int = Field(ge=0)
    team_id: Identifier
    period: Period
    start_clock_ms: int = Field(ge=0)
    end_clock_ms: int = Field(ge=0)
    start_type: StartType
    end_reason: EndReason
    start_location: Location
    end_location: Location
    event_ids: tuple[Identifier, ...]
    on_ball_actions: int = Field(ge=0)
    passes: int = Field(ge=0)
    completed_passes: int = Field(ge=0)
    shots: int = Field(ge=0)
    goals: int = Field(ge=0)
    max_x: float
    reached_attacking_third: bool
    reached_box: bool

    @property
    def duration_ms(self) -> int:
        return self.end_clock_ms - self.start_clock_ms


def controlling_team(event: MatchEvent) -> Identifier | None:
    """The team an event shows to be in control of the ball, or None."""
    if isinstance(event, ON_BALL):
        return event.team_id
    if isinstance(event, Interception | BallRecovery):
        return event.team_id
    if isinstance(event, Tackle) and event.outcome is DuelOutcome.WON:
        return event.team_id
    return None


def _start_type(event: MatchEvent) -> StartType:
    if isinstance(event, Pass) and event.kind is PassKind.KICK_OFF:
        return StartType.KICK_OFF
    if isinstance(event, Pass) and event.kind is not PassKind.OPEN_PLAY:
        return StartType.SET_PIECE
    if isinstance(event, Shot) and event.kind is not ShotKind.OPEN_PLAY:
        return StartType.SET_PIECE
    if isinstance(event, REGAINS):
        return StartType.REGAIN
    return StartType.OPEN_PLAY


class _Builder:
    def __init__(self, first: MatchEvent, team_id: Identifier, index: int) -> None:
        self.index = index
        self.team_id = team_id
        self.period: Period = first.period
        self.start_clock_ms = first.clock_ms
        self.end_clock_ms = first.clock_ms
        self.start_type = _start_type(first)
        location = getattr(first, "location", None)
        if not isinstance(location, Location):
            raise TypeError("a possession must start with an on-pitch action")
        self.start_location = location
        self.end_location = location
        self.event_ids: list[Identifier] = []
        self.last_on_ball: MatchEvent | None = None
        self.on_ball_actions = 0
        self.passes = 0
        self.completed_passes = 0
        self.shots = 0
        self.goals = 0
        self.max_x = location.x
        self.reached_attacking_third = in_attacking_third(location)
        self.reached_box = in_penalty_box(location)

    def _touch(self, location: Location) -> None:
        self.max_x = max(self.max_x, location.x)
        self.reached_attacking_third |= in_attacking_third(location)
        self.reached_box |= in_penalty_box(location)

    def add(self, event: MatchEvent) -> None:
        self.event_ids.append(event.event_id)
        self.end_clock_ms = event.clock_ms
        if controlling_team(event) != self.team_id:
            return
        location = getattr(event, "location", None)
        if isinstance(location, Location):
            self.end_location = location
            self._touch(location)
        if not isinstance(event, ON_BALL):
            return
        self.on_ball_actions += 1
        self.last_on_ball = event
        if isinstance(event, Carry):
            self.end_location = event.end_location
            self._touch(event.end_location)
        elif isinstance(event, Pass):
            self.passes += 1
            if event.outcome is PassOutcome.COMPLETE:
                self.completed_passes += 1
                self.end_location = event.end_location
                self._touch(event.end_location)
        elif isinstance(event, Shot):
            self.shots += 1
            self.goals += int(event.outcome is ShotOutcome.GOAL)

    def close(self, reason: EndReason) -> Possession:
        return Possession(
            index=self.index,
            team_id=self.team_id,
            period=self.period,
            start_clock_ms=self.start_clock_ms,
            end_clock_ms=self.end_clock_ms,
            start_type=self.start_type,
            end_reason=reason,
            start_location=self.start_location,
            end_location=self.end_location,
            event_ids=tuple(self.event_ids),
            on_ball_actions=self.on_ball_actions,
            passes=self.passes,
            completed_passes=self.completed_passes,
            shots=self.shots,
            goals=self.goals,
            max_x=self.max_x,
            reached_attacking_third=self.reached_attacking_third,
            reached_box=self.reached_box,
        )


def _end_reason(current: _Builder, next_event: MatchEvent | None) -> EndReason:
    last = current.last_on_ball
    if current.goals:
        return EndReason.GOAL
    if isinstance(last, Shot):
        return EndReason.SHOT
    if isinstance(last, Pass) and last.outcome is PassOutcome.OFFSIDE:
        return EndReason.OFFSIDE
    if next_event is None:
        return EndReason.PERIOD_END
    if isinstance(next_event, Pass) and next_event.kind in OUT_OF_PLAY_RESTARTS:
        return EndReason.OUT_OF_PLAY
    if isinstance(next_event, Pass) and next_event.kind is PassKind.FREE_KICK:
        return EndReason.FREE_KICK_CONCEDED
    if isinstance(next_event, Pass) and next_event.kind is not PassKind.OPEN_PLAY:
        return EndReason.STOPPAGE
    if isinstance(next_event, Shot) and next_event.kind is ShotKind.CORNER:
        return EndReason.OUT_OF_PLAY
    if isinstance(next_event, Shot) and next_event.kind is not ShotKind.OPEN_PLAY:
        return EndReason.FREE_KICK_CONCEDED
    return EndReason.LOST_IN_PLAY


def build_possessions(events: Sequence[MatchEvent]) -> tuple[Possession, ...]:
    possessions: list[Possession] = []
    current: _Builder | None = None
    for event in events:
        if isinstance(event, PeriodEnd):
            if current is not None:
                possessions.append(current.close(_end_reason(current, None)))
                current = None
            continue
        team = controlling_team(event)
        kick_off = isinstance(event, Pass) and event.kind is PassKind.KICK_OFF
        if team is not None and (current is None or current.team_id != team or kick_off):
            if current is not None:
                possessions.append(current.close(_end_reason(current, event)))
            current = _Builder(event, team, len(possessions))
        if current is not None:
            current.add(event)
        if isinstance(event, OwnGoal) and current is not None:
            possessions.append(current.close(EndReason.STOPPAGE))
            current = None
    if current is not None:
        possessions.append(current.close(_end_reason(current, None)))
    return tuple(possessions)
