"""Whole-match team summaries and player involvement."""

from collections import Counter, defaultdict
from collections.abc import Sequence

from pydantic import Field

from matcheyes.analytics.geometry import (
    HALFWAY_X,
    Third,
    enters_penalty_box,
    is_progressive,
    third,
)
from matcheyes.analytics.metrics import ON_PITCH, Series, is_regain
from matcheyes.analytics.possessions import ON_BALL, EndReason, Possession
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier, MatchInfo
from matcheyes.domain.events import (
    Card,
    CardType,
    Carry,
    Foul,
    Interception,
    MatchEvent,
    OwnGoal,
    Pass,
    PassKind,
    PassOutcome,
    PeriodEnd,
    PeriodStart,
    Pressure,
    Shot,
    ShotOutcome,
    Substitution,
    Tackle,
)
from matcheyes.domain.pitch import PITCH_LENGTH_M

PPDA_MIN_X = 0.4 * PITCH_LENGTH_M
"""PPDA only counts the defending team's actions from 40 % of the pitch length upwards, i.e.
in the opponent's first 60 %: the conventional pressing zone."""

DISMISSALS = frozenset({CardType.RED, CardType.SECOND_YELLOW})


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def series_total(series: Series) -> float | None:
    return ratio(sum(series.numerators), sum(series.denominators))


class TeamSummary(DomainModel):
    team_id: Identifier
    goals: int = Field(ge=0)
    possessions: int = Field(ge=0)
    on_ball_share: float | None
    field_tilt: float | None
    open_play_passes: int = Field(ge=0)
    pass_completion: float | None
    progressive_actions: int = Field(ge=0)
    attacking_third_entries: int = Field(ge=0)
    box_entries: int = Field(ge=0)
    shots: int = Field(ge=0)
    regains_by_third: dict[Third, int]
    high_regains: int = Field(ge=0)
    pressures: int = Field(ge=0)
    pressure_regain_rate: float | None
    ppda: float | None = Field(description="Opponent open-play passes per defensive action.")
    defensive_action_height: float | None
    turnovers: int = Field(ge=0)
    own_half_turnovers: int = Field(ge=0)
    mean_possession_s: float | None
    possessions_reaching_attacking_third: float | None
    passes_per_possession: float | None


class PlayerInvolvement(DomainModel):
    player_id: Identifier
    team_id: Identifier
    minutes_played: float = Field(ge=0)
    on_ball_actions: int = Field(ge=0)
    share_of_team_actions: float | None = Field(
        description="Player's on-ball actions over the team's on-ball actions while on the pitch."
    )
    passes: int = Field(ge=0)
    pass_completion: float | None
    progressive_actions: int = Field(ge=0)
    shots: int = Field(ge=0)
    regains: int = Field(ge=0)
    pressures: int = Field(ge=0)


def goals_for(events: Sequence[MatchEvent], team: Identifier, opponent: Identifier) -> int:
    return sum(
        1
        for e in events
        if (isinstance(e, Shot) and e.team_id == team and e.outcome is ShotOutcome.GOAL)
        or (isinstance(e, OwnGoal) and e.team_id == opponent)
    )


def _ball_moves(events: Sequence[MatchEvent], team: Identifier) -> list[Pass | Carry]:
    """Completed passes and carries: actions that relocate the ball under control."""
    return [
        e
        for e in events
        if (isinstance(e, Carry) and e.team_id == team)
        or (isinstance(e, Pass) and e.team_id == team and e.outcome is PassOutcome.COMPLETE)
    ]


def team_summaries(
    events: Sequence[MatchEvent],
    possessions: Sequence[Possession],
    series: dict[tuple[str, Identifier], Series],
    info: MatchInfo,
) -> tuple[TeamSummary, ...]:
    summaries = []
    for team in info.team_ids:
        opponent = info.opponent_of(team)
        own = [p for p in possessions if p.team_id == team]
        lost = [p for p in own if p.end_reason is EndReason.LOST_IN_PLAY]
        moves = _ball_moves(events, team)
        regains = Counter(
            third(e.location)
            for e in events
            if isinstance(e, ON_PITCH) and e.team_id == team and is_regain(e)
        )
        opponent_passes = sum(
            1
            for e in events
            if isinstance(e, Pass)
            and e.team_id == opponent
            and e.kind is PassKind.OPEN_PLAY
            and e.location.x <= PITCH_LENGTH_M - PPDA_MIN_X
        )
        defensive_actions = sum(
            1
            for e in events
            if isinstance(e, Tackle | Interception | Foul)
            and e.team_id == team
            and e.location.x >= PPDA_MIN_X
        )
        summaries.append(
            TeamSummary(
                team_id=team,
                goals=goals_for(events, team, opponent),
                possessions=len(own),
                on_ball_share=series_total(series["on_ball_share", team]),
                field_tilt=series_total(series["field_tilt", team]),
                open_play_passes=int(sum(series["pass_completion", team].denominators)),
                pass_completion=series_total(series["pass_completion", team]),
                progressive_actions=sum(
                    1 for e in moves if is_progressive(e.location, e.end_location)
                ),
                attacking_third_entries=int(
                    sum(series["attacking_third_entries", team].numerators)
                ),
                box_entries=sum(1 for e in moves if enters_penalty_box(e.location, e.end_location)),
                shots=int(sum(series["shots", team].numerators)),
                regains_by_third={t: regains.get(t, 0) for t in Third},
                high_regains=int(sum(series["high_regains", team].numerators)),
                pressures=int(sum(series["pressures", team].numerators)),
                pressure_regain_rate=series_total(series["pressure_regain_rate", team]),
                ppda=ratio(opponent_passes, defensive_actions),
                defensive_action_height=series_total(series["defensive_action_height", team]),
                turnovers=len(lost),
                own_half_turnovers=sum(1 for p in lost if p.end_location.x < HALFWAY_X),
                mean_possession_s=ratio(sum(p.duration_ms for p in own) / 1000, len(own)),
                possessions_reaching_attacking_third=ratio(
                    sum(1 for p in own if p.reached_attacking_third), len(own)
                ),
                passes_per_possession=ratio(sum(p.passes for p in own), len(own)),
            )
        )
    return tuple(summaries)


class _PlayerTally:
    def __init__(self) -> None:
        self.ms_played = 0
        self.on_since: int | None = None
        self.on_ball = 0
        self.team_actions_while_on = 0
        self.passes = 0
        self.completed = 0
        self.progressive = 0
        self.shots = 0
        self.regains = 0
        self.pressures = 0


def player_involvement(
    events: Sequence[MatchEvent], info: MatchInfo
) -> tuple[PlayerInvolvement, ...]:
    tallies: dict[Identifier, _PlayerTally] = defaultdict(_PlayerTally)
    team_of: dict[Identifier, Identifier] = {}
    on_pitch: dict[Identifier, set[Identifier]] = {}
    for sheet in (info.home, info.away):
        on_pitch[sheet.team_id] = {p.player_id for p in sheet.starting_xi}
        for member in sheet.squad:
            team_of[member.player_id] = sheet.team_id
            tallies[member.player_id] = _PlayerTally()

    def leave(player: Identifier, clock_ms: int) -> None:
        tally = tallies[player]
        if tally.on_since is not None:
            tally.ms_played += clock_ms - tally.on_since
            tally.on_since = None

    for event in events:
        if isinstance(event, PeriodStart):
            for players in on_pitch.values():
                for player in players:
                    tallies[player].on_since = event.clock_ms
        elif isinstance(event, PeriodEnd):
            for players in on_pitch.values():
                for player in players:
                    leave(player, event.clock_ms)
        elif isinstance(event, Substitution):
            leave(event.player_id, event.clock_ms)
            on_pitch[event.team_id].discard(event.player_id)
            on_pitch[event.team_id].add(event.replacement_id)
            tallies[event.replacement_id].on_since = event.clock_ms
        elif isinstance(event, Card) and event.card in DISMISSALS:
            leave(event.player_id, event.clock_ms)
            on_pitch[event.team_id].discard(event.player_id)
        if not isinstance(event, ON_PITCH):
            continue
        tally = tallies[event.player_id]
        if isinstance(event, ON_BALL):
            tally.on_ball += 1
            for player in on_pitch[event.team_id]:
                tallies[player].team_actions_while_on += 1
        if isinstance(event, Pass) and event.kind is PassKind.OPEN_PLAY:
            tally.passes += 1
            tally.completed += int(event.outcome is PassOutcome.COMPLETE)
        moved = isinstance(event, Carry) or (
            isinstance(event, Pass) and event.outcome is PassOutcome.COMPLETE
        )
        if moved and isinstance(event, Pass | Carry):
            tally.progressive += int(is_progressive(event.location, event.end_location))
        tally.shots += int(isinstance(event, Shot))
        tally.regains += int(is_regain(event))
        tally.pressures += int(isinstance(event, Pressure))

    return tuple(
        PlayerInvolvement(
            player_id=player,
            team_id=team_of[player],
            minutes_played=round(t.ms_played / 60_000, 2),
            on_ball_actions=t.on_ball,
            share_of_team_actions=ratio(t.on_ball, t.team_actions_while_on),
            passes=t.passes,
            pass_completion=ratio(t.completed, t.passes),
            progressive_actions=t.progressive,
            shots=t.shots,
            regains=t.regains,
            pressures=t.pressures,
        )
        for player, t in tallies.items()
        if t.ms_played > 0
    )
