"""Possession-by-possession match simulator (docs/synthetic-data.md#simulation-loop).

The simulator reads hidden team state and player attributes and emits only observable events.
Coordinates are internal `(x, y)` tuples in the acting team's attacking frame (attacking towards
x = 105); events are emitted in the frame of the team that performs them.
"""

import math
import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar, cast

from matcheyes.domain.entities import Position
from matcheyes.domain.events import (
    BallRecovery,
    Block,
    BodyPart,
    Card,
    CardType,
    Carry,
    Clearance,
    DuelOutcome,
    FormationChange,
    Foul,
    Interception,
    MatchEvent,
    OwnGoal,
    Pass,
    PassHeight,
    PassKind,
    PassOutcome,
    PeriodEnd,
    PeriodStart,
    Pressure,
    Shot,
    ShotKind,
    ShotOutcome,
    Substitution,
    Tackle,
    TakeOn,
    _EventBase,
)
from matcheyes.domain.pitch import PITCH_LENGTH_M, PITCH_WIDTH_M, Location
from matcheyes.domain.time import MatchInstant, Period
from matcheyes_synth.league import ClubProfile
from matcheyes_synth.rng import Streams
from matcheyes_synth.squads import ATTACKERS, DEFENDERS, MIDFIELDERS, WIDE, Squad
from matcheyes_synth.state import ResolvedState, StateContext, match_minute, resolve_state
from matcheyes_synth.truth import (
    HiddenTeamState,
    Intervention,
    InterventionKind,
    InterventionOnset,
    PlayerAttributes,
    ScenarioSpec,
    ScriptedEvent,
    ScriptedEventKind,
    TeamStateSegment,
)

MAX_EVENTS = 6000
MAX_SUBSTITUTIONS = 5
TOLERANCE_S: dict[ScriptedEventKind, int] = {
    ScriptedEventKind.GOAL: 120,
    ScriptedEventKind.RED_CARD: 180,
    ScriptedEventKind.SUBSTITUTION: 300,
    ScriptedEventKind.FORMATION_CHANGE: 60,
}

Point = tuple[float, float]
CENTRE: Point = (52.5, 34.0)
PENALTY_SPOT: Point = (94.0, 34.0)
EventT = TypeVar("EventT", bound=_EventBase)


class GenerationError(Exception):
    """A match could not be generated validly. Raised instead of silently repairing output."""


class Restart(StrEnum):
    KICK_OFF = "kick_off"
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    CORNER = "corner"
    FREE_KICK = "free_kick"
    PENALTY = "penalty"


@dataclass
class PendingRestart:
    kind: Restart
    team_id: str
    at: Point
    delay_s: float


@dataclass
class TeamRuntime:
    profile: ClubProfile
    squad: Squad
    on_pitch: list[str]
    bench: list[str]
    positions: dict[str, Position]
    fatigue: dict[str, float]
    planned_subs: list[float]
    booked: set[str] = field(default_factory=set)
    subs_made: int = 0
    goals: int = 0
    resolved: ResolvedState | None = None

    @property
    def team_id(self) -> str:
        return self.profile.club.club_id

    @property
    def state(self) -> HiddenTeamState:
        if self.resolved is None:
            raise GenerationError("team state read before it was resolved")
        return self.resolved.state

    @property
    def goalkeeper(self) -> str | None:
        for player_id in self.on_pitch:
            if self.positions[player_id] is Position.GK:
                return player_id
        return None


@dataclass
class _OpenSegment:
    start: MatchInstant
    resolved: ResolvedState


def mirror(point: Point) -> Point:
    return (PITCH_LENGTH_M - point[0], PITCH_WIDTH_M - point[1])


def to_location(point: Point) -> Location:
    x = min(PITCH_LENGTH_M, max(0.0, point[0]))
    y = min(PITCH_WIDTH_M, max(0.0, point[1]))
    return Location(x=round(x, 1), y=round(y, 1))


def in_box(point: Point) -> bool:
    return point[0] >= 88.5 and abs(point[1] - 34.0) <= 20.16


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


class MatchSimulator:
    def __init__(
        self,
        spec: ScenarioSpec,
        match_id: str,
        streams: Streams,
        home: tuple[ClubProfile, Squad],
        away: tuple[ClubProfile, Squad],
    ) -> None:
        self.spec = spec
        self.match_id = match_id
        self.streams = streams
        self.teams: dict[str, TeamRuntime] = {}
        for profile, squad in (home, away):
            self.teams[profile.club.club_id] = self._runtime(profile, squad)
        self.home_id = home[0].club.club_id
        self.away_id = away[0].club.club_id

        self.events: list[MatchEvent] = []
        self.period: Period = 1
        self.clock_ms = 0
        self.period_end_ms = 0
        self.attacking = self.home_id
        self.carrier = ""
        self.ball: Point = CENTRE
        self.restart: PendingRestart | None = None
        self.last_was_cross = False
        self.steer_started_ms: int | None = None
        self.steer_actions = 0

        self.resolved_events: dict[str, list[str]] = {}
        self.resolved_at: dict[str, MatchInstant] = {}
        self.onsets: dict[str, MatchInstant] = {}
        self.segments: list[TeamStateSegment] = []
        self._open_segments: dict[str, _OpenSegment] = {}
        self._state_minute = -1

    # ------------------------------------------------------------------ setup

    def _runtime(self, profile: ClubProfile, squad: Squad) -> TeamRuntime:
        sheet = squad.sheet
        team_id = profile.club.club_id
        scripted_subs = sum(
            1
            for e in self.spec.scripted_events
            if e.kind is ScriptedEventKind.SUBSTITUTION and e.team_id == team_id
        )
        rng = self.streams[f"subs-plan-{team_id}"]
        count = max(0, rng.choices((3, 4, 5), (0.2, 0.45, 0.35))[0] - scripted_subs)
        planned = sorted(45.0 if rng.random() < 0.12 else rng.uniform(55, 88) for _ in range(count))
        return TeamRuntime(
            profile=profile,
            squad=squad,
            on_pitch=[p.player_id for p in sheet.starting_xi],
            bench=[p.player_id for p in sheet.bench],
            positions={p.player_id: p.position for p in sheet.squad},
            fatigue={p.player_id: 0.0 for p in sheet.squad},
            planned_subs=planned,
        )

    def rng(self, name: str) -> random.Random:
        return self.streams[name]

    # ------------------------------------------------------------------ clock

    @property
    def now(self) -> MatchInstant:
        return MatchInstant(period=self.period, clock_ms=self.clock_ms)

    @property
    def minute(self) -> float:
        return match_minute(self.now)

    def _advance(self, seconds: float) -> None:
        self.clock_ms += max(0, int(seconds * 1000))
        self._tick()

    def _scale(self, team: TeamRuntime) -> float:
        return 1.3 - 0.6 * team.state.tempo

    def _seconds_since(self, instant: MatchInstant) -> float:
        return (match_minute(self.now) - match_minute(instant)) * 60

    # ------------------------------------------------------------------ emission

    def _emit(self, event_type: type[EventT], **fields: Any) -> str:
        if len(self.events) >= MAX_EVENTS:
            raise GenerationError(f"runaway simulation: more than {MAX_EVENTS} events")
        sequence = len(self.events) + 1
        event_id = f"{self.match_id}-{sequence:05d}"
        event = event_type(
            event_id=event_id,
            match_id=self.match_id,
            sequence=sequence,
            period=self.period,
            clock_ms=self.clock_ms,
            **fields,
        )
        self.events.append(cast("MatchEvent", event))
        return event_id

    def _resolve(self, ref: str, *event_ids: str) -> None:
        self.resolved_events.setdefault(ref, []).extend(event_ids)
        self.resolved_at.setdefault(ref, self.now)

    # ------------------------------------------------------------------ teams

    def team(self, team_id: str) -> TeamRuntime:
        return self.teams[team_id]

    def opponent(self, team_id: str) -> TeamRuntime:
        return self.teams[self.away_id if team_id == self.home_id else self.home_id]

    def _attrs(self, team: TeamRuntime, player_id: str) -> PlayerAttributes:
        return team.squad.attributes[player_id]

    def _weight(self, team: TeamRuntime, player_id: str, at: Point) -> float:
        position = team.positions[player_id]
        x, y = at
        if position is Position.GK:
            return 1.0 if x < 18 else (0.15 if x < 35 else 0.0)
        if position in DEFENDERS:
            weight = 3.0 if x < 35 else (1.3 if x < 70 else 0.5)
        elif position in MIDFIELDERS:
            weight = 2.0 if x < 35 else (3.0 if x < 70 else 2.0)
        else:
            weight = 0.4 if x < 35 else (1.3 if x < 70 else 3.0)
        wide_ball = abs(y - 34.0) > 16
        if position in WIDE:
            weight *= (1.0 + 1.5 * team.state.width) if wide_ball else 0.6
        elif wide_ball:
            weight *= 0.7
        return weight * (0.5 + self._attrs(team, player_id).passing)

    def _pick(
        self, team: TeamRuntime, at: Point, *, exclude: str = "", allow_gk: bool = True
    ) -> str:
        candidates = [
            p
            for p in team.on_pitch
            if p != exclude and (allow_gk or team.positions[p] is not Position.GK)
        ]
        weights = [self._weight(team, p, at) for p in candidates]
        if sum(weights) <= 0:
            weights = [0.0 if team.positions[p] is Position.GK else 1.0 for p in candidates]
        return self.rng("selection").choices(candidates, weights)[0]

    # ------------------------------------------------------------------ hidden state

    def _onset(self, intervention: Intervention, now: MatchInstant) -> MatchInstant | None:
        if intervention.intervention_id in self.onsets:
            return self.onsets[intervention.intervention_id]
        start = intervention.start
        if intervention.trigger_ref is not None:
            triggered = self.resolved_at.get(intervention.trigger_ref)
            if triggered is None:
                return None
            start = max(start, triggered, key=lambda i: i.sort_key)
        if now.sort_key >= start.sort_key:
            self.onsets[intervention.intervention_id] = start
        return start

    def _active_interventions(self, team_id: str, now: MatchInstant) -> list[Intervention]:
        active: list[Intervention] = []
        for intervention in self.spec.interventions:
            if intervention.team_id != team_id:
                continue
            onset = self._onset(intervention, now)
            if onset is not None:
                active.append(intervention.model_copy(update={"start": onset}))
        return active

    def _refresh_state(self, at: MatchInstant | None = None) -> None:
        now = at or self.now
        for team in self.teams.values():
            opponent = self.opponent(team.team_id)
            fatigue = [team.fatigue[p] for p in team.on_pitch]
            context = StateContext(
                goal_difference=team.goals - opponent.goals,
                match_minute=match_minute(now),
                average_fatigue=sum(fatigue) / len(fatigue),
            )
            team.resolved = resolve_state(
                team.profile.style, self._active_interventions(team.team_id, now), now, context
            )
            current = self._open_segments.get(team.team_id)
            if current is not None and now.sort_key < current.start.sort_key:
                raise GenerationError("hidden-state timeline went backwards")
            if current is not None and current.start.sort_key < now.sort_key:
                self._close_segment(team.team_id, current, now)
            self._open_segments[team.team_id] = _OpenSegment(start=now, resolved=team.resolved)

    def _close_segment(self, team_id: str, segment: _OpenSegment, end: MatchInstant) -> None:
        self.segments.append(
            TeamStateSegment(
                team_id=team_id,
                start=segment.start,
                end=end,
                state=segment.resolved.state,
                drivers=segment.resolved.drivers,
                intervention_ids=segment.resolved.intervention_ids,
            )
        )

    def _close_all_segments(self) -> None:
        for team_id in (self.home_id, self.away_id):
            segment = self._open_segments.pop(team_id)
            if segment.start.sort_key < self.now.sort_key:
                self._close_segment(team_id, segment, self.now)

    def _tick(self) -> None:
        """Advance fatigue and re-resolve hidden state at every minute boundary crossed, so
        each answer-key segment spans at most one minute."""
        period_offset = 0 if self.period == 1 else 45
        for minute in range(self._state_minute + 1, int(self.minute) + 1):
            for team in self.teams.values():
                load = 0.0045 + 0.0055 * team.state.press_intensity
                for player_id in team.on_pitch:
                    stamina = self._attrs(team, player_id).stamina
                    team.fatigue[player_id] += load * (1.5 - stamina)
            self._state_minute = minute
            boundary = MatchInstant(period=self.period, clock_ms=(minute - period_offset) * 60_000)
            self._refresh_state(boundary)

    # ------------------------------------------------------------------ main loop

    def run(self) -> None:
        self._play_period(1)
        self._play_period(2)
        self._check_scripted_complete()

    def _play_period(self, period: Period) -> None:
        timing = self.rng("timing")
        self.period = period
        self.clock_ms = 0
        added_min = timing.randint(1, 4) if period == 1 else timing.randint(2, 7)
        self.period_end_ms = (45 + added_min) * 60_000 + timing.randint(0, 45_000)
        if period == 2:
            for team in self.teams.values():
                for player_id in team.fatigue:
                    team.fatigue[player_id] *= 0.8
        self._state_minute = int(self.minute)
        self._emit(PeriodStart)
        self._refresh_state()
        self._dead_ball_window()
        kicker = self.home_id if period == 1 else self.away_id
        self.restart = PendingRestart(Restart.KICK_OFF, kicker, CENTRE, 0.0)

        while True:
            self._tick()
            pending_penalty = self.restart is not None and self.restart.kind is Restart.PENALTY
            if self.clock_ms >= self.period_end_ms and not pending_penalty:
                break
            self._formation_changes()
            self._check_tolerances()
            if self.restart is not None:
                self._take_restart(self.restart)
            else:
                self._open_play()
        self._emit(PeriodEnd)
        self._close_all_segments()

    # ------------------------------------------------------------------ scripted events

    def _due(self, kind: ScriptedEventKind) -> list[ScriptedEvent]:
        now = self.now.sort_key
        return [
            e
            for e in self.spec.scripted_events
            if e.kind is kind and e.ref not in self.resolved_events and e.at.sort_key <= now
        ]

    def _goal_steer(self) -> ScriptedEvent | None:
        due = self._due(ScriptedEventKind.GOAL)
        return due[0] if due else None

    def _red_steer(self) -> ScriptedEvent | None:
        due = self._due(ScriptedEventKind.RED_CARD)
        return due[0] if due else None

    def _cluster_for(self, team_id: str) -> ScriptedEvent | None:
        now = self.now.sort_key
        for e in self.spec.scripted_events:
            if e.kind is not ScriptedEventKind.TURNOVER_CLUSTER or e.team_id != team_id:
                continue
            if e.until is not None and e.at.sort_key <= now < e.until.sort_key:
                return e
        return None

    def _check_tolerances(self) -> None:
        for event in self.spec.scripted_events:
            tolerance = TOLERANCE_S.get(event.kind)
            if tolerance is None or event.ref in self.resolved_events:
                continue
            if event.at.sort_key <= self.now.sort_key and self._seconds_since(event.at) > tolerance:
                raise GenerationError(
                    f"scripted event {event.ref} ({event.kind}) not produced within {tolerance}s"
                )

    def _check_scripted_complete(self) -> None:
        missing = [e.ref for e in self.spec.scripted_events if e.ref not in self.resolved_events]
        if missing:
            raise GenerationError(f"scripted events never produced: {missing}")

    def _formation_changes(self) -> None:
        """Tactical shifts are instructions from the bench; they need no stoppage."""
        for formation_change in self._due(ScriptedEventKind.FORMATION_CHANGE):
            event_id = self._emit(
                FormationChange,
                team_id=formation_change.team_id,
                formation=formation_change.formation,
            )
            self._resolve(formation_change.ref, event_id)
            self._refresh_state()

    def _dead_ball_window(self) -> None:
        """Substitutions happen while the ball is dead: scripted and background."""
        self._formation_changes()
        for scripted_sub in self._due(ScriptedEventKind.SUBSTITUTION):
            team = self.team(scripted_sub.team_id)
            impact = any(
                i.trigger_ref == scripted_sub.ref and i.kind is InterventionKind.IMPACT_SUBSTITUTION
                for i in self.spec.interventions
            )
            event_id = self._substitute(team, impact=impact)
            if event_id is None:
                raise GenerationError(f"{scripted_sub.ref}: {team.team_id} cannot substitute")
            self._resolve(scripted_sub.ref, event_id)
            self._refresh_state()
        for team in (self.team(self.home_id), self.team(self.away_id)):
            made = 0
            while team.planned_subs and self.minute >= team.planned_subs[0] and made < 2:
                team.planned_subs.pop(0)
                if self._substitute(team, impact=False) is not None:
                    made += 1
            if made:
                self._advance(self.rng("timing").uniform(10, 20))
                self._refresh_state()

    def _substitute(self, team: TeamRuntime, *, impact: bool) -> str | None:
        rng = self.rng("subs")
        bench = [p for p in team.bench if team.positions[p] is not Position.GK]
        if team.subs_made >= MAX_SUBSTITUTIONS or not bench:
            return None
        outfield = [p for p in team.on_pitch if team.positions[p] is not Position.GK]
        if impact:
            outfield = [p for p in outfield if team.positions[p] not in DEFENDERS] or outfield
        weights = [(0.05 + team.fatigue[p]) ** 2 for p in outfield]
        leaving = rng.choices(outfield, weights)[0]

        def group(position: Position) -> frozenset[Position]:
            for g in (DEFENDERS, MIDFIELDERS, ATTACKERS):
                if position in g:
                    return g
            return frozenset()

        if impact:
            preferred = [p for p in bench if team.positions[p] in WIDE & ATTACKERS]
        else:
            preferred = [p for p in bench if team.positions[p] in group(team.positions[leaving])]
        joining = (preferred or bench)[0]

        event_id = self._emit(
            Substitution, team_id=team.team_id, player_id=leaving, replacement_id=joining
        )
        team.on_pitch[team.on_pitch.index(leaving)] = joining
        team.bench.remove(joining)
        team.fatigue[joining] = 0.0
        team.subs_made += 1
        return event_id

    # ------------------------------------------------------------------ possession changes

    def _gain(self, team: TeamRuntime, player_id: str, at: Point) -> None:
        if team.team_id != self.attacking:
            self.steer_actions = 0
        self.attacking = team.team_id
        self.carrier = player_id
        self.ball = at
        self.restart = None
        self.last_was_cross = False

    def _set_restart(self, kind: Restart, team: TeamRuntime, at: Point, delay_s: float) -> None:
        self.attacking = team.team_id
        self.restart = PendingRestart(kind, team.team_id, at, delay_s)
        self.steer_actions = 0
        self.last_was_cross = False

    def _goal_scored(self, scorer: TeamRuntime) -> None:
        scorer.goals += 1
        conceding = self.opponent(scorer.team_id)
        self._set_restart(Restart.KICK_OFF, conceding, CENTRE, self.rng("timing").uniform(45, 75))
        self._refresh_state()

    # ------------------------------------------------------------------ restarts

    def _take_restart(self, restart: PendingRestart) -> None:
        self.restart = None
        self._advance(restart.delay_s)
        self._dead_ball_window()
        team = self.team(restart.team_id)
        self.attacking = team.team_id
        self.ball = restart.at
        if restart.kind is Restart.KICK_OFF:
            self._kick_off(team)
        elif restart.kind is Restart.THROW_IN:
            self._throw_in(team, restart.at)
        elif restart.kind is Restart.GOAL_KICK:
            self._goal_kick(team, restart.at)
        elif restart.kind is Restart.CORNER:
            self._corner(team, restart.at)
        elif restart.kind is Restart.FREE_KICK:
            self._free_kick(team, restart.at)
        else:
            self._penalty(team)

    def _kick_off(self, team: TeamRuntime) -> None:
        rng = self.rng("actions")
        self.carrier = self._pick(team, CENTRE, allow_gk=False)
        target = (rng.uniform(38, 48), 34 + rng.gauss(0, 8))
        self._complete_pass(team, target, PassHeight.GROUND, PassKind.KICK_OFF)

    def _throw_in(self, team: TeamRuntime, at: Point) -> None:
        rng = self.rng("actions")
        self.carrier = self._pick(team, at, allow_gk=False)
        inward = rng.uniform(4, 14)
        target = (
            clamp(at[0] + rng.uniform(-6, 12), 1, 104),
            at[1] + (inward if at[1] < 34 else -inward),
        )
        self._pass(PassKind.THROW_IN, target, PassHeight.LOW, 0.82)

    def _goal_kick(self, team: TeamRuntime, at: Point) -> None:
        rng = self.rng("actions")
        keeper = team.goalkeeper
        self.carrier = keeper if keeper is not None else self._pick(team, at)
        if rng.random() < 0.2 + 0.6 * team.state.directness:
            target = (rng.uniform(40, 70), rng.uniform(10, 58))
            self._pass(PassKind.GOAL_KICK, target, PassHeight.HIGH, 0.5)
        else:
            target = (rng.uniform(12, 28), rng.uniform(8, 60))
            self._pass(PassKind.GOAL_KICK, target, PassHeight.GROUND, 0.96)

    def _corner(self, team: TeamRuntime, at: Point) -> None:
        rng = self.rng("actions")
        self.carrier = self._pick(team, (95.0, at[1]), allow_gk=False)
        target = (rng.uniform(94, 101), rng.uniform(27, 41))
        self._pass(PassKind.CORNER, target, PassHeight.HIGH, 0.32)

    def _free_kick(self, team: TeamRuntime, at: Point) -> None:
        rng = self.rng("actions")
        self.carrier = self._pick(team, at, allow_gk=at[0] < 20)
        x, y = at
        if x >= 76 and abs(y - 34) < 18 and rng.random() < 0.4:
            self._shot(ShotKind.FREE_KICK, pressured=False)
        elif x >= 68:
            target = (rng.uniform(92, 101), rng.uniform(26, 42))
            self._pass(PassKind.FREE_KICK, target, PassHeight.HIGH, 0.36)
        else:
            target, height = self._pass_target(team, pressured=False)
            success = self._pass_success(team, target, height, pressured=False) + 0.05
            self._pass(PassKind.FREE_KICK, target, height, success)

    def _penalty(self, team: TeamRuntime) -> None:
        outfield = [p for p in team.on_pitch if team.positions[p] is not Position.GK]
        self.carrier = max(outfield, key=lambda p: (self._attrs(team, p).finishing, p))
        self.ball = PENALTY_SPOT
        self._shot(ShotKind.PENALTY, pressured=False)

    # ------------------------------------------------------------------ open play

    def _open_play(self) -> None:
        attack = self.team(self.attacking)
        defence = self.opponent(attack.team_id)
        x, y = self.ball
        goal_steer = self._goal_steer()
        steering = goal_steer is not None and goal_steer.team_id == attack.team_id
        red_steer = self._red_steer()

        if goal_steer is not None and self.steer_started_ms is None:
            self.steer_started_ms = self.clock_ms
        if goal_steer is None:
            self.steer_started_ms = None

        if red_steer is not None and red_steer.team_id == defence.team_id and x < 85:
            self._forced_red(defence, attack, red_steer)
            return

        pressured = False
        if not steering:
            factor = self._press_zone_factor(defence, mirror(self.ball))
            p_press = (0.04 + 0.52 * defence.state.press_intensity) * factor
            if self.rng("actions").random() < p_press:
                pressured = True
                if self._press(attack, defence):
                    return

        if steering:
            self.steer_actions += 1
            if x >= 84 and abs(y - 34) < 18:
                self._shot(ShotKind.OPEN_PLAY, pressured=False, forced_goal=goal_steer)
            elif self.steer_actions >= 4:
                rng = self.rng("scripted")
                target = (rng.uniform(90, 99), rng.uniform(27, 41))
                self._pass(PassKind.OPEN_PLAY, target, PassHeight.LOW, 1.0)
            else:
                rng = self.rng("scripted")
                target = (clamp(x + rng.uniform(8, 22), 1, 103), clamp(y + rng.gauss(0, 8), 4, 64))
                self._pass(PassKind.OPEN_PLAY, target, PassHeight.GROUND, 1.0)
            return

        state = attack.state
        rng = self.rng("actions")
        p_shot = 0.0
        if in_box(self.ball):
            p_shot = 0.13 + 0.10 * state.risk_appetite
        elif x >= 75 and abs(y - 34) < 22:
            p_shot = 0.02 + 0.03 * state.risk_appetite
        if rng.random() < p_shot:
            self._shot(ShotKind.OPEN_PLAY, pressured=pressured)
            return

        roll = rng.random()
        p_take_on = 0.025 + 0.04 * state.risk_appetite + (0.03 if x > 70 else 0.0)
        if roll < p_take_on:
            self._take_on(attack, defence, pressured)
        elif roll < p_take_on + 0.21:
            self._carry(attack, pressured)
        else:
            target, height = self._pass_target(attack, pressured)
            success = self._pass_success(attack, target, height, pressured)
            if goal_steer is not None:
                elapsed = self.clock_ms - (self.steer_started_ms or self.clock_ms)
                success = 0.0 if elapsed > 20_000 else success * 0.55
            self._pass(PassKind.OPEN_PLAY, target, height, success)

    def _press_zone_factor(self, defence: TeamRuntime, ball_in_defence_frame: Point) -> float:
        """A team engages the ball only up to its line of engagement; beyond it, it drops off.
        A deep block (low `defensive_line`) therefore leaves the opponent's half unpressed."""
        depth = ball_in_defence_frame[0]
        engage = 25 + 60 * defence.state.defensive_line
        if depth <= engage:
            return 1.0
        return clamp(1 - (depth - engage) / 15, 0.08, 1.0)

    def _press(self, attack: TeamRuntime, defence: TeamRuntime) -> bool:
        """Emit a pressure; return True if possession changed or play stopped."""
        at = mirror(self.ball)
        presser = self._pick(defence, at, allow_gk=False)
        self._emit(
            Pressure,
            team_id=defence.team_id,
            player_id=presser,
            location=to_location(at),
            pressured_player_id=self.carrier,
        )
        self._advance(self.rng("timing").uniform(0.4, 1.0))
        if self.rng("discipline").random() < 0.045 * self._box_caution():
            self._foul(defence, presser, attack, self.carrier, self.ball)
            return True
        outcomes = self.rng("outcomes")
        if outcomes.random() < 0.08 + 0.20 * defence.state.press_intensity:
            presser_attrs = self._attrs(defence, presser)
            carrier_attrs = self._attrs(attack, self.carrier)
            p_win = clamp(
                0.5
                + 0.9 * (defence.state.execution - 0.5)
                + 0.35 * (presser_attrs.pressing - carrier_attrs.composure),
                0.15,
                0.85,
            )
            won = outcomes.random() < p_win
            self._emit(
                Tackle,
                team_id=defence.team_id,
                player_id=presser,
                location=to_location(at),
                outcome=DuelOutcome.WON if won else DuelOutcome.LOST,
                opponent_id=self.carrier,
            )
            self._advance(self.rng("timing").uniform(0.5, 1.5))
            if won:
                self._gain(defence, presser, at)
                return True
        return False

    def _pass_target(self, team: TeamRuntime, pressured: bool) -> tuple[Point, PassHeight]:
        """Pressed players recycle possession; players given time look forward."""
        rng = self.rng("actions")
        state = team.state
        x, y = self.ball
        p_back = 0.30 - 0.18 * state.directness + (0.10 if pressured else -0.06)
        if rng.random() < p_back:
            dx = -rng.uniform(2, 14)
        else:
            reach = 4 + 16 * state.directness + 4 * state.risk_appetite + (0 if pressured else 3)
            dx = min(55.0, rng.expovariate(1 / reach))
        if rng.random() < 0.08 + 0.30 * state.width:
            ty = rng.uniform(3, 14) if rng.random() < 0.5 else rng.uniform(54, 65)
        else:
            ty = y + rng.gauss(0, 7 + 6 * state.width)
        target = (clamp(x + dx, 1, 101), clamp(ty, 1.5, 66.5))
        length = math.dist(self.ball, target)
        if length > 32:
            height = PassHeight.HIGH if rng.random() < 0.7 else PassHeight.LOW
        elif length > 18:
            height = PassHeight.LOW if rng.random() < 0.6 else PassHeight.GROUND
        else:
            height = PassHeight.GROUND
        return target, height

    def _pass_success(
        self, team: TeamRuntime, target: Point, height: PassHeight, pressured: bool
    ) -> float:
        defence = self.opponent(team.team_id)
        tx, ty = target
        if ty < 0.5 or ty > 67.5 or tx > 104.5:
            return 0.0
        length = math.dist(self.ball, target)
        p = 0.955 - 0.005 * max(0.0, length - 12)
        if height is PassHeight.HIGH:
            p -= 0.08
        if pressured:
            p -= 0.03 + 0.12 * defence.state.execution + 0.10 * defence.state.press_intensity
        if tx > 75:
            p -= 0.04 + 0.06 * (1 - defence.state.defensive_line)
        if tx < self.ball[0]:
            p += 0.03
        p += 0.25 * (self._attrs(team, self.carrier).passing - 0.65)
        p += 0.20 * (team.state.execution - 0.5)
        p -= 0.06 * (defence.state.press_intensity - 0.5)
        if len(team.on_pitch) < len(defence.on_pitch):
            p -= 0.03
        red_steer = self._red_steer()
        if red_steer is not None and red_steer.team_id == team.team_id:
            p *= 0.6
        if self._cluster_for(team.team_id) is not None:
            noise = self.rng("noise")
            p *= 0.6 if noise.random() < 0.9 else 1.0
        return clamp(p, 0.25, 0.985)

    def _pass(self, kind: PassKind, target: Point, height: PassHeight, p_success: float) -> None:
        team = self.team(self.attacking)
        if self.rng("outcomes").random() < p_success:
            self._complete_pass(team, target, height, kind)
        else:
            self._failed_pass(team, target, height, kind)

    def _complete_pass(
        self, team: TeamRuntime, target: Point, height: PassHeight, kind: PassKind
    ) -> None:
        passer = self.carrier
        target = (clamp(target[0], 1, 104), clamp(target[1], 1, 67))
        recipient = self._pick(team, target, exclude=passer)
        self._emit(
            Pass,
            team_id=team.team_id,
            player_id=passer,
            location=to_location(self.ball),
            end_location=to_location(target),
            outcome=PassOutcome.COMPLETE,
            recipient_id=recipient,
            height=height,
            kind=kind,
        )
        self._advance((1.5 + math.dist(self.ball, target) / 15) * self._scale(team))
        self.last_was_cross = height is PassHeight.HIGH and in_box(target)
        self.carrier = recipient
        self.ball = target
        if kind is PassKind.CORNER and self.rng("actions").random() < 0.55:
            self._shot(ShotKind.OPEN_PLAY, pressured=True)

    def _failed_pass(
        self, team: TeamRuntime, target: Point, height: PassHeight, kind: PassKind
    ) -> None:
        defence = self.opponent(team.team_id)
        outcomes = self.rng("outcomes")
        origin = self.ball
        tx, ty = target
        goal_steer = self._goal_steer()
        forced_turnover = goal_steer is not None and goal_steer.team_id == defence.team_id
        crossed_line = ty < 0.5 or ty > 67.5 or tx > 104.5
        roll = outcomes.random()

        def emit(outcome: PassOutcome, end: Point) -> str:
            event_id = self._emit(
                Pass,
                team_id=team.team_id,
                player_id=self.carrier,
                location=to_location(origin),
                end_location=to_location(end),
                outcome=outcome,
                height=height,
                kind=kind,
            )
            cluster = self._cluster_for(team.team_id)
            if cluster is not None:
                self._resolve(cluster.ref, event_id)
            self._advance((1.5 + math.dist(origin, end) / 15) * self._scale(team))
            return event_id

        offside_possible = kind in (PassKind.OPEN_PLAY, PassKind.FREE_KICK) and tx > 70
        if (
            not forced_turnover
            and not crossed_line
            and offside_possible
            and tx - origin[0] > 12
            and roll < 0.12
        ):
            emit(PassOutcome.OFFSIDE, target)
            spot = mirror((clamp(tx - 2, 1, 104), clamp(ty, 1, 67)))
            self._set_restart(Restart.FREE_KICK, defence, spot, self.rng("timing").uniform(12, 25))
            return

        if not forced_turnover and (crossed_line or roll < 0.30):
            if not crossed_line:
                if tx > 98:
                    tx = 105.0
                else:
                    ty = 0.0 if ty < 34 else 68.0
            end = (clamp(tx, 0, 105), clamp(ty, 0, 68))
            emit(PassOutcome.OUT_OF_PLAY, end)
            if end[0] >= 104.5:
                side = 34 + (9.16 if self.rng("actions").random() < 0.5 else -9.16)
                self._set_restart(
                    Restart.GOAL_KICK, defence, (5.5, side), self.rng("timing").uniform(18, 32)
                )
            else:
                spot = mirror((clamp(end[0], 1, 104), end[1]))
                self._set_restart(
                    Restart.THROW_IN, defence, spot, self.rng("timing").uniform(8, 18)
                )
            return

        end = (clamp(tx, 1, 104), clamp(ty, 1, 67))
        emit(PassOutcome.INCOMPLETE, end)
        into_box = kind is PassKind.CORNER or (height is PassHeight.HIGH and in_box(end))
        if into_box and not forced_turnover and outcomes.random() < 0.015:
            self._own_goal(defence, mirror(end))
            return
        self._contest(team, defence, origin, end, forced_turnover=forced_turnover)

    def _own_goal(self, team: TeamRuntime, at: Point) -> None:
        player = self._pick(team, at, allow_gk=False)
        self._emit(OwnGoal, team_id=team.team_id, player_id=player, location=to_location(at))
        self._advance(self.rng("timing").uniform(1.0, 2.0))
        self._goal_scored(self.opponent(team.team_id))

    def _contest(
        self,
        team: TeamRuntime,
        defence: TeamRuntime,
        origin: Point,
        end: Point,
        *,
        forced_turnover: bool = False,
    ) -> None:
        """Resolve a ball that failed to reach a teammate."""
        outcomes = self.rng("outcomes")
        roll = outcomes.random()
        at = mirror(end)
        if at[0] < 18 and roll < 0.45 and not forced_turnover:
            clearer = self._pick(defence, at)
            self._emit(
                Clearance, team_id=defence.team_id, player_id=clearer, location=to_location(at)
            )
            self._advance(self.rng("timing").uniform(1.5, 3.0))
            landing = (outcomes.uniform(30, 60), outcomes.uniform(5, 63))
            if outcomes.random() < 0.55:
                self._recovery(defence, landing)
            else:
                self._recovery(team, mirror(landing))
            return
        if roll < 0.55 or forced_turnover:
            fraction = outcomes.uniform(0.45, 0.9)
            point = mirror(
                (
                    origin[0] + fraction * (end[0] - origin[0]),
                    origin[1] + fraction * (end[1] - origin[1]),
                )
            )
            interceptor = self._pick(defence, point)
            self._emit(
                Interception,
                team_id=defence.team_id,
                player_id=interceptor,
                location=to_location(point),
            )
            self._advance(self.rng("timing").uniform(0.5, 1.5))
            self._gain(defence, interceptor, point)
        elif roll < 0.85:
            self._recovery(defence, at)
        else:
            self._recovery(team, end)

    def _recovery(self, team: TeamRuntime, at: Point) -> None:
        player = self._pick(team, at)
        self._emit(BallRecovery, team_id=team.team_id, player_id=player, location=to_location(at))
        self._advance(self.rng("timing").uniform(0.8, 2.0))
        self._gain(team, player, at)

    def _carry(self, team: TeamRuntime, pressured: bool) -> None:
        rng = self.rng("actions")
        x, y = self.ball
        space = 0.0 if pressured else 4.0
        dx = max(-3.0, rng.gauss(2 + space + 4 * team.state.tempo, 4))
        end = (clamp(x + dx, 1, 101), clamp(y + rng.gauss(0, 3), 1, 67))
        self._emit(
            Carry,
            team_id=team.team_id,
            player_id=self.carrier,
            location=to_location(self.ball),
            end_location=to_location(end),
        )
        self._advance((0.8 + math.dist(self.ball, end) / 6) * self._scale(team))
        self.ball = end
        self.last_was_cross = False

    def _take_on(self, attack: TeamRuntime, defence: TeamRuntime, pressured: bool) -> None:
        at = mirror(self.ball)
        opponent = self._pick(defence, at, allow_gk=False)
        carrier_attrs = self._attrs(attack, self.carrier)
        p_win = clamp(
            0.5
            + 0.6 * (carrier_attrs.pace - self._attrs(defence, opponent).pressing)
            + 0.15 * (attack.state.execution - 0.5)
            - (0.1 if pressured else 0.0),
            0.2,
            0.8,
        )
        won = self.rng("outcomes").random() < p_win
        self._emit(
            TakeOn,
            team_id=attack.team_id,
            player_id=self.carrier,
            location=to_location(self.ball),
            outcome=DuelOutcome.WON if won else DuelOutcome.LOST,
            opponent_id=opponent,
        )
        self._advance(self.rng("timing").uniform(1.0, 2.5))
        self.last_was_cross = False
        if won:
            x, y = self.ball
            self.ball = (clamp(x + self.rng("actions").uniform(3, 8), 1, 104), y)
            return
        if self.rng("discipline").random() < 0.3 * self._box_caution():
            self._foul(defence, opponent, attack, self.carrier, self.ball)
            return
        self._emit(
            Tackle,
            team_id=defence.team_id,
            player_id=opponent,
            location=to_location(at),
            outcome=DuelOutcome.WON,
            opponent_id=self.carrier,
        )
        self._gain(defence, opponent, at)

    # ------------------------------------------------------------------ discipline

    def _box_caution(self) -> float:
        """Defenders risk far fewer fouls inside their own penalty area."""
        return 0.12 if in_box(self.ball) else 1.0

    def _foul(
        self,
        offender_team: TeamRuntime,
        offender: str,
        victim_team: TeamRuntime,
        victim: str,
        at: Point,
        *,
        card: CardType | None = None,
    ) -> list[str]:
        """`at` is in the victim's frame. Returns the emitted foul (and card) event IDs."""
        timing = self.rng("timing")
        event_ids = [
            self._emit(
                Foul,
                team_id=offender_team.team_id,
                player_id=offender,
                location=to_location(mirror(at)),
                fouled_player_id=victim,
            )
        ]
        if card is None:
            roll = self.rng("discipline").random()
            if roll < 0.004:
                card = CardType.RED
            elif roll < 0.004 + 0.12:
                card = (
                    CardType.SECOND_YELLOW if offender in offender_team.booked else CardType.YELLOW
                )
        delay = timing.uniform(15, 35)
        if card is not None:
            event_ids.append(
                self._emit(Card, team_id=offender_team.team_id, player_id=offender, card=card)
            )
            delay += timing.uniform(15, 30)
            if card is CardType.YELLOW:
                offender_team.booked.add(offender)
            else:
                offender_team.on_pitch.remove(offender)
                self._refresh_state()
        if in_box(at):
            self._set_restart(Restart.PENALTY, victim_team, PENALTY_SPOT, delay + 30)
        else:
            self._set_restart(Restart.FREE_KICK, victim_team, at, delay)
        return event_ids

    def _forced_red(
        self, offender_team: TeamRuntime, victim_team: TeamRuntime, scripted: ScriptedEvent
    ) -> None:
        at = mirror(self.ball)
        offender = self._pick(offender_team, at, allow_gk=False)
        self._emit(
            Pressure,
            team_id=offender_team.team_id,
            player_id=offender,
            location=to_location(at),
            pressured_player_id=self.carrier,
        )
        self._advance(self.rng("timing").uniform(0.4, 1.0))
        event_ids = self._foul(
            offender_team, offender, victim_team, self.carrier, self.ball, card=CardType.RED
        )
        self._resolve(scripted.ref, *event_ids)
        self._refresh_state()

    # ------------------------------------------------------------------ shots

    def _goal_probability(
        self, team: TeamRuntime, kind: ShotKind, body: BodyPart, pressured: bool
    ) -> float:
        shooter = self._attrs(team, self.carrier)
        if kind is ShotKind.PENALTY:
            return clamp(0.70 + 0.1 * (shooter.composure - 0.6), 0.6, 0.85)
        if kind is ShotKind.FREE_KICK:
            return clamp(0.06 + 0.06 * (shooter.finishing - 0.6), 0.02, 0.12)
        x, y = self.ball
        distance = max(1.0, math.hypot(105 - x, 34 - y))
        base = 0.38 if distance <= 6 else 0.38 * math.exp(-(distance - 6) / 6.0)
        p = base * (1 - 0.5 * abs(y - 34) / distance)
        if body is BodyPart.HEAD:
            p *= 0.55
        p *= 0.6 + 0.6 * shooter.finishing
        p *= 0.8 + 0.4 * team.state.execution
        if pressured:
            p *= 0.75
        return clamp(p, 0.01, 0.7)

    def _shot(
        self, kind: ShotKind, *, pressured: bool, forced_goal: ScriptedEvent | None = None
    ) -> None:
        team = self.team(self.attacking)
        defence = self.opponent(team.team_id)
        rng = self.rng("outcomes")
        if kind is ShotKind.PENALTY or kind is ShotKind.FREE_KICK:
            body = BodyPart.RIGHT_FOOT if rng.random() < 0.75 else BodyPart.LEFT_FOOT
        elif self.last_was_cross and rng.random() < 0.6:
            body = BodyPart.HEAD
        else:
            body = BodyPart.RIGHT_FOOT if rng.random() < 0.72 else BodyPart.LEFT_FOOT

        p_goal = self._goal_probability(team, kind, body, pressured)
        if forced_goal is not None or rng.random() < p_goal:
            outcome = ShotOutcome.GOAL
        else:
            roll = rng.random()
            blocked = 0.0 if kind is ShotKind.PENALTY else (0.24 + (0.08 if pressured else 0.0))
            if roll < blocked:
                outcome = ShotOutcome.BLOCKED
            elif roll < blocked + (1 - blocked) * 0.45:
                outcome = ShotOutcome.SAVED
            elif roll < blocked + (1 - blocked) * 0.49:
                outcome = ShotOutcome.WOODWORK
            else:
                outcome = ShotOutcome.OFF_TARGET

        x, y = self.ball
        if outcome is ShotOutcome.BLOCKED:
            fraction = rng.uniform(0.05, 0.25)
            end = (x + fraction * (105 - x), y + fraction * (34 - y))
        elif outcome is ShotOutcome.OFF_TARGET:
            end = (105.0, clamp(34 + rng.choice((-1, 1)) * rng.uniform(4, 14), 0, 68))
        else:
            end = (105.0, 34 + rng.uniform(-3.4, 3.4))
        shot_id = self._emit(
            Shot,
            team_id=team.team_id,
            player_id=self.carrier,
            location=to_location(self.ball),
            end_location=to_location(end),
            outcome=outcome,
            kind=kind,
            body_part=body,
            goalkeeper_id=defence.goalkeeper,
            ball_speed_kmh=round(
                clamp(rng.gauss(85 if body is not BodyPart.HEAD else 50, 12), 20, 140), 1
            ),
        )
        self._advance(self.rng("timing").uniform(1.5, 3.0))
        self.last_was_cross = False

        if outcome is ShotOutcome.GOAL:
            if forced_goal is not None:
                self._resolve(forced_goal.ref, shot_id)
            self._goal_scored(team)
        elif outcome is ShotOutcome.SAVED:
            if rng.random() < 0.3:
                self._set_restart(
                    Restart.CORNER, team, self._corner_spot(), self.rng("timing").uniform(22, 38)
                )
            else:
                keeper = defence.goalkeeper or self._pick(defence, (5.0, 34.0))
                self._gain(defence, keeper, (6.0, 34.0))
        elif outcome is ShotOutcome.BLOCKED:
            at = mirror(end)
            blocker = self._pick(defence, at, allow_gk=False)
            self._emit(Block, team_id=defence.team_id, player_id=blocker, location=to_location(at))
            roll = rng.random()
            if roll < 0.3:
                self._set_restart(
                    Restart.CORNER, team, self._corner_spot(), self.rng("timing").uniform(22, 38)
                )
            elif roll < 0.7:
                self._recovery(defence, (rng.uniform(12, 25), rng.uniform(15, 53)))
            else:
                self._recovery(team, (clamp(x - 8, 60, 100), y))
        elif outcome is ShotOutcome.WOODWORK and rng.random() < 0.5:
            self._recovery(defence, (rng.uniform(4, 15), rng.uniform(20, 48)))
        else:
            side = 34 + (9.16 if rng.random() < 0.5 else -9.16)
            self._set_restart(
                Restart.GOAL_KICK, defence, (5.5, side), self.rng("timing").uniform(18, 32)
            )

    def _corner_spot(self) -> Point:
        return (104.5, 0.5) if self.rng("actions").random() < 0.5 else (104.5, 67.5)

    # ------------------------------------------------------------------ outputs

    def onsets_record(self) -> tuple[InterventionOnset, ...]:
        return tuple(
            InterventionOnset(
                intervention_id=i.intervention_id, start=self.onsets[i.intervention_id]
            )
            for i in self.spec.interventions
            if i.intervention_id in self.onsets
        )
