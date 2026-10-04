"""Match-level data invariants (docs/data-model.md#invariants).

Schema-level rules (types, ranges, closed schemas, team-sheet shape) are enforced by the
domain models. This module checks rules that span events: ordering, period structure,
who is on the pitch, cross-references, discipline and restarts.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from matcheyes.domain.entities import Identifier, MatchInfo
from matcheyes.domain.events import (
    ON_BALL_TYPES,
    Card,
    CardType,
    Foul,
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
    TakeOn,
)
from matcheyes.domain.match import ObservableMatch

MAX_SUBSTITUTIONS = 5


class ViolationCode(StrEnum):
    MATCH_ID_MISMATCH = "match_id_mismatch"
    DUPLICATE_EVENT_ID = "duplicate_event_id"
    SEQUENCE_GAP_OR_DUPLICATE = "sequence_gap_or_duplicate"
    SEQUENCE_ORDER = "sequence_order"
    PERIOD_STRUCTURE = "period_structure"
    EVENT_OUTSIDE_PERIOD = "event_outside_period"
    CLOCK_NOT_MONOTONIC = "clock_not_monotonic"
    UNKNOWN_TEAM = "unknown_team"
    PLAYER_NOT_ON_PITCH = "player_not_on_pitch"
    OPPONENT_REFERENCE_INVALID = "opponent_reference_invalid"
    PASS_RECIPIENT_INVALID = "pass_recipient_invalid"
    SUBSTITUTION_INVALID = "substitution_invalid"
    SUBSTITUTION_LIMIT = "substitution_limit"
    CARD_SEQUENCE_INVALID = "card_sequence_invalid"
    RESTART_INVALID = "restart_invalid"


@dataclass(frozen=True)
class Violation:
    code: ViolationCode
    message: str
    sequence: int | None = None


@dataclass(frozen=True)
class ValidationReport:
    violations: tuple[Violation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations

    def codes(self) -> set[ViolationCode]:
        return {v.code for v in self.violations}


def validate_match(match: ObservableMatch) -> ValidationReport:
    checks = (_identity, _sequence, _periods, _participation, _restarts)
    return ValidationReport(tuple(v for check in checks for v in check(match)))


def _identity(match: ObservableMatch) -> Iterator[Violation]:
    seen: set[Identifier] = set()
    for event in match.events:
        if event.match_id != match.info.match_id:
            yield Violation(
                ViolationCode.MATCH_ID_MISMATCH,
                f"event {event.event_id} belongs to {event.match_id}",
                event.sequence,
            )
        if event.event_id in seen:
            yield Violation(
                ViolationCode.DUPLICATE_EVENT_ID, f"duplicate {event.event_id}", event.sequence
            )
        seen.add(event.event_id)


def _sequence(match: ObservableMatch) -> Iterator[Violation]:
    sequences = [event.sequence for event in match.events]
    expected = list(range(1, len(sequences) + 1))
    if sorted(sequences) != expected:
        yield Violation(
            ViolationCode.SEQUENCE_GAP_OR_DUPLICATE,
            "sequence numbers must be unique and contiguous from 1",
        )
    elif sequences != expected:
        yield Violation(ViolationCode.SEQUENCE_ORDER, "events must be stored in sequence order")


def _periods(match: ObservableMatch) -> Iterator[Violation]:
    open_period: int | None = None
    started: set[int] = set()
    ended: set[int] = set()
    last_clock: dict[int, int] = {}

    for event in match.events:
        if event.clock_ms < last_clock.get(event.period, 0):
            yield Violation(
                ViolationCode.CLOCK_NOT_MONOTONIC,
                f"clock goes backwards in period {event.period}",
                event.sequence,
            )
        last_clock[event.period] = max(last_clock.get(event.period, 0), event.clock_ms)

        if isinstance(event, PeriodStart):
            out_of_order = event.period == 2 and 1 not in ended
            if open_period is not None or event.period in started or out_of_order:
                yield Violation(
                    ViolationCode.PERIOD_STRUCTURE,
                    f"unexpected start of period {event.period}",
                    event.sequence,
                )
            if event.clock_ms != 0:
                yield Violation(
                    ViolationCode.PERIOD_STRUCTURE, "periods start at clock 0", event.sequence
                )
            open_period = event.period
            started.add(event.period)
        elif isinstance(event, PeriodEnd):
            if open_period != event.period:
                yield Violation(
                    ViolationCode.PERIOD_STRUCTURE,
                    f"end of period {event.period} without matching start",
                    event.sequence,
                )
            open_period = None
            ended.add(event.period)
        elif open_period != event.period:
            yield Violation(
                ViolationCode.EVENT_OUTSIDE_PERIOD,
                f"{event.type} outside an open period {event.period}",
                event.sequence,
            )

    if started != {1, 2} or ended != {1, 2}:
        yield Violation(ViolationCode.PERIOD_STRUCTURE, "a match has exactly periods 1 and 2")


@dataclass
class _PitchState:
    info: MatchInfo
    on_pitch: dict[Identifier, set[Identifier]] = field(init=False)
    unused_bench: dict[Identifier, set[Identifier]] = field(init=False)
    substitutions: dict[Identifier, int] = field(init=False)
    booked: set[Identifier] = field(default_factory=set)

    def __post_init__(self) -> None:
        sheets = (self.info.home, self.info.away)
        self.on_pitch = {s.team_id: {p.player_id for p in s.starting_xi} for s in sheets}
        self.unused_bench = {s.team_id: {p.player_id for p in s.bench} for s in sheets}
        self.substitutions = {s.team_id: 0 for s in sheets}


def _opponent_reference(event: MatchEvent) -> tuple[str, Identifier | None] | None:
    if isinstance(event, Pressure):
        return ("pressured_player_id", event.pressured_player_id)
    if isinstance(event, TakeOn | Tackle):
        return ("opponent_id", event.opponent_id)
    if isinstance(event, Foul):
        return ("fouled_player_id", event.fouled_player_id)
    if isinstance(event, Shot):
        return ("goalkeeper_id", event.goalkeeper_id)
    return None


def _participation(match: ObservableMatch) -> Iterator[Violation]:
    state = _PitchState(match.info)
    for event in match.events:
        team_id = getattr(event, "team_id", None)
        if team_id is None:
            continue
        if team_id not in state.on_pitch:
            yield Violation(ViolationCode.UNKNOWN_TEAM, f"unknown team {team_id}", event.sequence)
            continue
        opponent = match.info.opponent_of(team_id)
        player_id: Identifier | None = getattr(event, "player_id", None)

        if player_id is not None and player_id not in state.on_pitch[team_id]:
            yield Violation(
                ViolationCode.PLAYER_NOT_ON_PITCH,
                f"{player_id} is not on the pitch for {team_id}",
                event.sequence,
            )

        reference = _opponent_reference(event)
        if reference is not None:
            name, ref = reference
            if ref is not None and ref not in state.on_pitch[opponent]:
                yield Violation(
                    ViolationCode.OPPONENT_REFERENCE_INVALID,
                    f"{name}={ref} is not an opponent on the pitch",
                    event.sequence,
                )

        if isinstance(event, Pass):
            yield from _check_recipient(event, state.on_pitch[team_id])
        elif isinstance(event, Substitution):
            yield from _apply_substitution(event, state)
        elif isinstance(event, Card):
            yield from _apply_card(event, state)


def _check_recipient(event: Pass, teammates: set[Identifier]) -> Iterator[Violation]:
    if event.outcome is PassOutcome.COMPLETE:
        valid = event.recipient_id in teammates and event.recipient_id != event.player_id
        if not valid:
            yield Violation(
                ViolationCode.PASS_RECIPIENT_INVALID,
                "a complete pass needs a different teammate on the pitch as recipient",
                event.sequence,
            )
    elif event.recipient_id is not None:
        yield Violation(
            ViolationCode.PASS_RECIPIENT_INVALID,
            f"a {event.outcome} pass cannot have a recipient",
            event.sequence,
        )


def _apply_substitution(event: Substitution, state: _PitchState) -> Iterator[Violation]:
    team = event.team_id
    if event.replacement_id not in state.unused_bench[team]:
        yield Violation(
            ViolationCode.SUBSTITUTION_INVALID,
            f"{event.replacement_id} is not an unused substitute for {team}",
            event.sequence,
        )
    state.substitutions[team] += 1
    if state.substitutions[team] > MAX_SUBSTITUTIONS:
        yield Violation(
            ViolationCode.SUBSTITUTION_LIMIT,
            f"{team} exceeded {MAX_SUBSTITUTIONS} substitutions",
            event.sequence,
        )
    state.on_pitch[team].discard(event.player_id)
    state.unused_bench[team].discard(event.replacement_id)
    state.on_pitch[team].add(event.replacement_id)


def _apply_card(event: Card, state: _PitchState) -> Iterator[Violation]:
    already_booked = event.player_id in state.booked
    if event.card is CardType.YELLOW:
        if already_booked:
            yield Violation(
                ViolationCode.CARD_SEQUENCE_INVALID,
                "a second booking must be recorded as second_yellow",
                event.sequence,
            )
        state.booked.add(event.player_id)
        return
    if event.card is CardType.SECOND_YELLOW and not already_booked:
        yield Violation(
            ViolationCode.CARD_SEQUENCE_INVALID,
            "second_yellow without a previous yellow",
            event.sequence,
        )
    state.on_pitch[event.team_id].discard(event.player_id)


def _restarts(match: ObservableMatch) -> Iterator[Violation]:
    """Each period opens with a kick-off; after a goal the conceding team kicks off."""
    expected_kicker: Identifier | None = None
    any_team = "*"
    for event in match.events:
        if isinstance(event, PeriodStart):
            expected_kicker = any_team
            continue
        if isinstance(event, ON_BALL_TYPES):
            is_kick_off = isinstance(event, Pass) and event.kind is PassKind.KICK_OFF
            if expected_kicker is not None:
                right_team = expected_kicker in (any_team, event.team_id)
                if not (is_kick_off and right_team):
                    yield Violation(
                        ViolationCode.RESTART_INVALID,
                        f"expected a kick-off by {expected_kicker}",
                        event.sequence,
                    )
                expected_kicker = None
            elif is_kick_off:
                yield Violation(
                    ViolationCode.RESTART_INVALID, "unexpected kick-off", event.sequence
                )
        if isinstance(event, Shot) and event.outcome is ShotOutcome.GOAL:
            expected_kicker = match.info.opponent_of(event.team_id)
        elif isinstance(event, OwnGoal):
            expected_kicker = event.team_id
