"""Contextual match state, hand-computed on the minimal fixture and a scripted stream."""

import pytest

from matcheyes.analytics.context import (
    GameState,
    MatchContext,
    Phase,
    TransitionKind,
    build_context,
    phase_of,
)
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.events import (
    BodyPart,
    Card,
    CardType,
    OwnGoal,
    PeriodEnd,
    PeriodStart,
    Shot,
    ShotKind,
    ShotOutcome,
    Substitution,
)
from tests.support.builders import AWAY, HOME, EventStream, at, match_info, minimal_match, pid


@pytest.fixture(scope="module")
def minimal() -> MatchContext:
    match = minimal_match()
    return build_context(match.events, Timeline(match.events), match.info)


@pytest.fixture(scope="module")
def scripted() -> MatchContext:
    """Own goal (away scores) 5', home red 10', home goal 20', away second yellow 30'."""
    s = EventStream()
    s.add(PeriodStart, 1, 0)
    s.add(OwnGoal, 1, 300, team_id=HOME, player_id=pid(HOME, 4), location=at(5, 34))
    s.add(Card, 1, 600, team_id=HOME, player_id=pid(HOME, 5), card=CardType.RED)
    s.add(
        Shot,
        1,
        1200,
        team_id=HOME,
        player_id=pid(HOME, 9),
        location=at(95, 34),
        end_location=at(105, 34),
        outcome=ShotOutcome.GOAL,
        kind=ShotKind.OPEN_PLAY,
        body_part=BodyPart.HEAD,
    )
    s.add(Card, 1, 1800, team_id=AWAY, player_id=pid(AWAY, 6), card=CardType.SECOND_YELLOW)
    s.add(PeriodEnd, 1, 2400)
    return build_context(s.events, Timeline(s.events), match_info())


def test_minimal_goal_changes_state_from_the_next_bin(minimal: MatchContext) -> None:
    assert minimal.bins[0].goals == (0, 0)
    assert minimal.bins[1].goals == (0, 1)
    assert minimal.game_state(HOME, 0) is GameState.DRAWING
    assert minimal.game_state(HOME, 1) is GameState.TRAILING
    assert minimal.game_state(AWAY, 1) is GameState.LEADING
    assert minimal.goal_difference(AWAY, 50) == 1
    assert minimal.bins[0].minutes_since_goal is None
    assert minimal.bins[1].minutes_since_goal == 1
    assert minimal.bins[60].minutes_since_goal == 60


def test_minimal_transitions_and_key_events(minimal: MatchContext) -> None:
    match = minimal_match()
    goal = next(e for e in match.events if isinstance(e, Shot) and e.outcome is ShotOutcome.GOAL)
    sub = next(e for e in match.events if isinstance(e, Substitution))
    (transition,) = minimal.transitions
    assert (transition.kind, transition.team_id, transition.bin_index) == (
        TransitionKind.GOAL,
        AWAY,
        0,
    )
    assert transition.event_id == goal.event_id
    assert minimal.bins[0].key_event_ids == (goal.event_id,)
    assert minimal.bins[47].key_event_ids == (sub.event_id,)
    assert all(not b.key_event_ids for b in minimal.bins[1:47])  # the yellow card is not key


def test_minimal_substitutions_and_period_boundary(minimal: MatchContext) -> None:
    assert minimal.bins[47].period == 2
    assert minimal.bins[47].minute == 0
    assert minimal.bins[47].phase is Phase.OPENING
    assert minimal.bins[47].substitutions == (0, 0)
    assert minimal.bins[48].substitutions == (1, 0)
    assert minimal.bins[48].minutes_since_key_event == 1
    assert minimal.bins[48].players == (11, 11)


@pytest.mark.parametrize(
    ("minute", "phase"),
    [(0, Phase.OPENING), (14, Phase.OPENING), (15, Phase.MIDDLE), (29, Phase.MIDDLE),
     (30, Phase.CLOSING), (47, Phase.CLOSING)],
)  # fmt: skip
def test_phase_boundaries(minute: int, phase: Phase) -> None:
    assert phase_of(minute) is phase


def test_own_goal_is_credited_to_the_opponent(scripted: MatchContext) -> None:
    first = scripted.transitions[0]
    assert (first.kind, first.team_id, first.bin_index) == (TransitionKind.GOAL, AWAY, 5)
    assert scripted.bins[6].goals == (0, 1)


def test_dismissals_reduce_numbers_from_the_next_bin(scripted: MatchContext) -> None:
    assert scripted.bins[10].players == (11, 11)
    assert scripted.bins[11].players == (10, 11)
    assert scripted.bins[31].players == (10, 10)
    kinds = [(t.kind, t.team_id, t.bin_index) for t in scripted.transitions]
    assert kinds == [
        (TransitionKind.GOAL, AWAY, 5),
        (TransitionKind.DISMISSAL, HOME, 10),
        (TransitionKind.GOAL, HOME, 20),
        (TransitionKind.DISMISSAL, AWAY, 30),
    ]


def test_score_transitions_and_game_state(scripted: MatchContext) -> None:
    assert scripted.game_state(HOME, 15) is GameState.TRAILING
    assert scripted.game_state(AWAY, 15) is GameState.LEADING
    assert scripted.game_state(HOME, 21) is GameState.DRAWING
    assert scripted.bins[11].minutes_since_key_event == 1
    assert scripted.players(HOME, 15) == 10


def test_regime_is_the_state_at_the_end_of_each_bin(scripted: MatchContext) -> None:
    assert scripted.regime(4) == (0, 11, 11)
    assert scripted.regime(5) == (-1, 11, 11)
    assert scripted.regime(10) == (-1, 10, 11)
    assert scripted.regime(20) == (0, 10, 11)
    assert scripted.regime(30) == (0, 10, 10)


def test_regime_spans(scripted: MatchContext) -> None:
    assert scripted.regime_before(0, 20) == (10, 20)
    assert scripted.regime_before(15, 20) == (15, 20)
    assert scripted.regime_after(20, 40) == (20, 30)
    assert scripted.regime_after(32, 36) == (32, 36)
    assert scripted.regime_after(32, 99) == (32, 41)


def test_transitions_within_excludes_span_edges(scripted: MatchContext) -> None:
    assert [t.bin_index for t in scripted.transitions_within(5, 20)] == [10]
    assert [t.bin_index for t in scripted.transitions_within(4, 21)] == [5, 10, 20]
