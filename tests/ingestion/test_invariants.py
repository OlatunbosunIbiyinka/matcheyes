from typing import Any

import pytest

from matcheyes.domain.events import (
    Card,
    CardType,
    Pass,
    PassKind,
    PassOutcome,
    PeriodEnd,
    Pressure,
    Substitution,
)
from matcheyes.domain.match import MatchEvent, ObservableMatch
from matcheyes.ingestion.invariants import ViolationCode, validate_match
from tests.support.builders import AWAY, HOME, minimal_match, pid

V = ViolationCode


def _replace(match: ObservableMatch, index: int, **updates: Any) -> ObservableMatch:
    events = list(match.events)
    events[index] = events[index].model_copy(update=updates)
    return match.model_copy(update={"events": tuple(events)})


def _with_events(match: ObservableMatch, events: list[MatchEvent]) -> ObservableMatch:
    resequenced = [
        e.model_copy(update={"sequence": i, "event_id": f"{match.info.match_id}-x{i:05d}"})
        for i, e in enumerate(events, start=1)
    ]
    return match.model_copy(update={"events": tuple(resequenced)})


def _index(match: ObservableMatch, predicate: Any) -> int:
    return next(i for i, e in enumerate(match.events) if predicate(e))


@pytest.fixture
def match() -> ObservableMatch:
    return minimal_match()


def test_minimal_fixture_is_valid(match: ObservableMatch) -> None:
    report = validate_match(match)
    assert report.ok, report.violations


def test_foreign_match_id(match: ObservableMatch) -> None:
    assert V.MATCH_ID_MISMATCH in validate_match(_replace(match, 3, match_id="other")).codes()


def test_duplicate_event_id(match: ObservableMatch) -> None:
    bad = _replace(match, 3, event_id=match.events[2].event_id)
    assert V.DUPLICATE_EVENT_ID in validate_match(bad).codes()


def test_sequence_gap(match: ObservableMatch) -> None:
    bad = _replace(match, 3, sequence=999)
    assert V.SEQUENCE_GAP_OR_DUPLICATE in validate_match(bad).codes()


def test_sequence_stored_out_of_order(match: ObservableMatch) -> None:
    events = list(match.events)
    events[2], events[3] = events[3], events[2]
    bad = match.model_copy(update={"events": tuple(events)})
    assert V.SEQUENCE_ORDER in validate_match(bad).codes()


def test_clock_going_backwards(match: ObservableMatch) -> None:
    bad = _replace(match, 4, clock_ms=500)
    assert V.CLOCK_NOT_MONOTONIC in validate_match(bad).codes()


def test_missing_period_end(match: ObservableMatch) -> None:
    events = [e for e in match.events if not (isinstance(e, PeriodEnd) and e.period == 2)]
    assert V.PERIOD_STRUCTURE in validate_match(_with_events(match, events)).codes()


def test_event_between_periods(match: ObservableMatch) -> None:
    end_first = _index(match, lambda e: isinstance(e, PeriodEnd) and e.period == 1)
    events = list(match.events)
    stray = events[end_first - 1].model_copy(update={"clock_ms": 2_770_000})
    events.insert(end_first + 1, stray)
    assert V.EVENT_OUTSIDE_PERIOD in validate_match(_with_events(match, events)).codes()


def test_unknown_team(match: ObservableMatch) -> None:
    assert V.UNKNOWN_TEAM in validate_match(_replace(match, 2, team_id="nobody")).codes()


def test_bench_player_cannot_act(match: ObservableMatch) -> None:
    bad = _replace(match, 2, player_id=pid(HOME, 13))
    assert V.PLAYER_NOT_ON_PITCH in validate_match(bad).codes()


def test_substituted_player_cannot_act_again(match: ObservableMatch) -> None:
    after_sub = _index(match, lambda e: isinstance(e, Substitution)) + 2
    bad = _replace(match, after_sub, player_id=pid(HOME, 10))
    assert V.PLAYER_NOT_ON_PITCH in validate_match(bad).codes()


def test_pressure_must_target_an_opponent(match: ObservableMatch) -> None:
    i = _index(match, lambda e: isinstance(e, Pressure))
    bad = _replace(match, i, pressured_player_id=pid(AWAY, 9))
    assert V.OPPONENT_REFERENCE_INVALID in validate_match(bad).codes()


@pytest.mark.parametrize(
    ("outcome", "recipient"),
    [
        (PassOutcome.COMPLETE, None),
        (PassOutcome.COMPLETE, pid(HOME, 7)),  # the passer
        (PassOutcome.COMPLETE, pid(AWAY, 3)),  # an opponent
        (PassOutcome.INCOMPLETE, pid(HOME, 9)),
    ],
)
def test_pass_recipient_rules(
    match: ObservableMatch, outcome: PassOutcome, recipient: str | None
) -> None:
    bad = _replace(match, 2, outcome=outcome, recipient_id=recipient)
    assert V.PASS_RECIPIENT_INVALID in validate_match(bad).codes()


def test_substitute_must_come_from_unused_bench(match: ObservableMatch) -> None:
    i = _index(match, lambda e: isinstance(e, Substitution))
    bad = _replace(match, i, replacement_id=pid(HOME, 3))
    assert V.SUBSTITUTION_INVALID in validate_match(bad).codes()


def test_substitution_limit(match: ObservableMatch) -> None:
    i = _index(match, lambda e: isinstance(e, Substitution))
    template = match.events[i]
    events = list(match.events)
    extra = [
        template.model_copy(
            update={
                "player_id": pid(HOME, off),
                "replacement_id": pid(HOME, on),
                "clock_ms": 31_000,
            }
        )
        for off, on in ((3, 12), (4, 13), (5, 14), (6, 16), (7, 17))
    ]
    events[i + 1 : i + 1] = extra
    assert V.SUBSTITUTION_LIMIT in validate_match(_with_events(match, events)).codes()


def test_second_yellow_requires_first(match: ObservableMatch) -> None:
    i = _index(match, lambda e: isinstance(e, Card))
    bad = _replace(match, i, card=CardType.SECOND_YELLOW)
    assert V.CARD_SEQUENCE_INVALID in validate_match(bad).codes()


def test_two_plain_yellows_are_invalid(match: ObservableMatch) -> None:
    i = _index(match, lambda e: isinstance(e, Card))
    events = list(match.events)
    events.insert(i + 1, events[i].model_copy(update={"clock_ms": 75_000}))
    assert V.CARD_SEQUENCE_INVALID in validate_match(_with_events(match, events)).codes()


def test_dismissed_player_cannot_act(match: ObservableMatch) -> None:
    i = _index(match, lambda e: isinstance(e, Card))
    events = list(match.events)
    events[i] = events[i].model_copy(update={"card": CardType.RED})
    interception = i + 2
    events[interception] = events[interception].model_copy(update={"player_id": pid(AWAY, 4)})
    assert V.PLAYER_NOT_ON_PITCH in validate_match(_with_events(match, events)).codes()


def test_conceding_team_must_kick_off(match: ObservableMatch) -> None:
    restart = _index(
        match, lambda e: isinstance(e, Pass) and e.kind is PassKind.KICK_OFF and e.clock_ms > 0
    )
    bad = _replace(match, restart, kind=PassKind.OPEN_PLAY)
    assert V.RESTART_INVALID in validate_match(bad).codes()


def test_unexpected_kick_off(match: ObservableMatch) -> None:
    assert V.RESTART_INVALID in validate_match(_replace(match, 2, kind=PassKind.KICK_OFF)).codes()
