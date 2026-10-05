"""Contextual match state, minute by minute: score, numbers, substitutions, recent key events.

Everything here is bookkeeping over observable events. Nothing is inferred about intent, tactics
or physical condition. Definitions, assumptions and limitations: docs/contextual-evidence.md.

Two views of each minute bin are kept:

* `ContextBin` is the state at the *start* of the bin: what was known when the minute began.
* `regime(index)` is the state at the *end* of the bin. A goal scored during minute g therefore
  puts bin g in the post-goal regime; the minute itself is mixed, which is the 1-minute
  resolution limit of the timeline.
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field

from matcheyes.analytics.summary import DISMISSALS
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier, MatchInfo
from matcheyes.domain.events import (
    Card,
    FormationChange,
    MatchEvent,
    OwnGoal,
    Shot,
    ShotOutcome,
    Substitution,
)
from matcheyes.domain.time import MatchInstant, Period

PHASE_OPENING_END = 15
PHASE_CLOSING_START = 30
"""Each period is split into opening (0-15'), middle (15-30') and closing (30'+, including
stoppage) phases. Fifteen-minute thirds are the conventional broadcast split, not a tuned value."""

STARTING_PLAYERS = 11


class GameState(StrEnum):
    LEADING = "leading"
    DRAWING = "drawing"
    TRAILING = "trailing"


class Phase(StrEnum):
    OPENING = "opening"
    MIDDLE = "middle"
    CLOSING = "closing"


class TransitionKind(StrEnum):
    GOAL = "goal"
    DISMISSAL = "dismissal"


class Transition(DomainModel):
    """A goal or dismissal. Goals that leave the score sign unchanged (2-0 to 3-0) do not change
    the regime, but still count as transitions for coincidence and alignment."""

    event_id: Identifier
    kind: TransitionKind
    team_id: Identifier = Field(description="Scoring team for goals; dismissed team otherwise.")
    bin_index: int = Field(ge=0)
    at: MatchInstant


class ContextBin(DomainModel):
    """Match state at the start of one minute bin. Pairs are (home, away)."""

    index: int = Field(ge=0)
    period: Period
    minute: int = Field(ge=0)
    phase: Phase
    goals: tuple[int, int]
    players: tuple[int, int]
    substitutions: tuple[int, int]
    minutes_since_goal: int | None = Field(description="Bins since the last goal's bin.")
    minutes_since_key_event: int | None = Field(
        description="Bins since the last goal, dismissal, substitution or formation change."
    )
    key_event_ids: tuple[Identifier, ...] = Field(description="Key events inside this bin.")


Regime = tuple[int, int, int]
"""(sign of the home goal difference, home players, away players) at the end of a bin."""


def phase_of(minute: int) -> Phase:
    if minute < PHASE_OPENING_END:
        return Phase.OPENING
    if minute < PHASE_CLOSING_START:
        return Phase.MIDDLE
    return Phase.CLOSING


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


class MatchContext(DomainModel):
    team_ids: tuple[Identifier, Identifier]
    bins: tuple[ContextBin, ...]
    transitions: tuple[Transition, ...]
    end_goals: tuple[tuple[int, int], ...] = Field(description="Score at the end of each bin.")
    end_players: tuple[tuple[int, int], ...] = Field(description="Players at the end of each bin.")

    def _side(self, team_id: Identifier) -> int:
        return self.team_ids.index(team_id)

    def goal_difference(self, team_id: Identifier, index: int) -> int:
        side = self._side(team_id)
        goals = self.bins[index].goals
        return goals[side] - goals[1 - side]

    def game_state(self, team_id: Identifier, index: int) -> GameState:
        diff = self.goal_difference(team_id, index)
        if diff > 0:
            return GameState.LEADING
        return GameState.TRAILING if diff < 0 else GameState.DRAWING

    def players(self, team_id: Identifier, index: int) -> int:
        return self.bins[index].players[self._side(team_id)]

    def regime(self, index: int) -> Regime:
        home, away = self.end_goals[index]
        return (_sign(home - away), *self.end_players[index])

    def transitions_within(self, lo: int, hi: int) -> tuple[Transition, ...]:
        """Transitions that split the span [lo, hi): bins before and after them differ."""
        return tuple(t for t in self.transitions if lo < t.bin_index < hi)

    def regime_before(self, lo: int, t: int) -> tuple[int, int]:
        """The longest span ending at t, starting no earlier than lo, in the regime of bin t-1."""
        target = self.regime(t - 1)
        start = t - 1
        while start > lo and self.regime(start - 1) == target:
            start -= 1
        return (start, t)

    def regime_after(self, t: int, hi: int) -> tuple[int, int]:
        """The longest span starting at t, ending no later than hi, in the regime of bin t."""
        target = self.regime(t)
        end = t + 1
        while end < min(hi, len(self.bins)) and self.regime(end) == target:
            end += 1
        return (t, end)


def _scoring_team(event: MatchEvent, info: MatchInfo) -> Identifier | None:
    if isinstance(event, Shot) and event.outcome is ShotOutcome.GOAL:
        return event.team_id
    if isinstance(event, OwnGoal):
        return info.opponent_of(event.team_id)
    return None


def build_context(
    events: Sequence[MatchEvent], timeline: Timeline, info: MatchInfo
) -> MatchContext:
    team_ids = info.team_ids
    n = len(timeline)
    goals = [[0, 0] for _ in range(n)]
    dismissed = [[0, 0] for _ in range(n)]
    subs = [[0, 0] for _ in range(n)]
    key_ids: list[list[Identifier]] = [[] for _ in range(n)]
    goal_bins: set[int] = set()
    transitions: list[Transition] = []

    for event in events:
        b = timeline.index_of_event(event)
        scorer = _scoring_team(event, info)
        if scorer is not None:
            goals[b][team_ids.index(scorer)] += 1
            goal_bins.add(b)
            key_ids[b].append(event.event_id)
            transitions.append(
                Transition(
                    event_id=event.event_id,
                    kind=TransitionKind.GOAL,
                    team_id=scorer,
                    bin_index=b,
                    at=event.instant,
                )
            )
        elif isinstance(event, Card) and event.card in DISMISSALS:
            dismissed[b][team_ids.index(event.team_id)] += 1
            key_ids[b].append(event.event_id)
            transitions.append(
                Transition(
                    event_id=event.event_id,
                    kind=TransitionKind.DISMISSAL,
                    team_id=event.team_id,
                    bin_index=b,
                    at=event.instant,
                )
            )
        elif isinstance(event, Substitution):
            subs[b][team_ids.index(event.team_id)] += 1
            key_ids[b].append(event.event_id)
        elif isinstance(event, FormationChange):
            key_ids[b].append(event.event_id)

    bins: list[ContextBin] = []
    end_goals: list[tuple[int, int]] = []
    end_players: list[tuple[int, int]] = []
    score = [0, 0]
    players = [STARTING_PLAYERS, STARTING_PLAYERS]
    made = [0, 0]
    last_goal: int | None = None
    last_key: int | None = None
    for minute_bin in timeline.bins:
        b = minute_bin.index
        bins.append(
            ContextBin(
                index=b,
                period=minute_bin.period,
                minute=minute_bin.minute,
                phase=phase_of(minute_bin.minute),
                goals=(score[0], score[1]),
                players=(players[0], players[1]),
                substitutions=(made[0], made[1]),
                minutes_since_goal=None if last_goal is None else b - last_goal,
                minutes_since_key_event=None if last_key is None else b - last_key,
                key_event_ids=tuple(key_ids[b]),
            )
        )
        for side in (0, 1):
            score[side] += goals[b][side]
            players[side] -= dismissed[b][side]
            made[side] += subs[b][side]
        end_goals.append((score[0], score[1]))
        end_players.append((players[0], players[1]))
        if b in goal_bins:
            last_goal = b
        if key_ids[b]:
            last_key = b

    return MatchContext(
        team_ids=team_ids,
        bins=tuple(bins),
        transitions=tuple(transitions),
        end_goals=tuple(end_goals),
        end_players=tuple(end_players),
    )
