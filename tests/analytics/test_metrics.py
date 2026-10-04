"""Metric series and summaries for the minimal match, hand-computed from tests/support/builders.py.

Bin 0 is P1 0:00-0:59; bin 47 is P2 0:00-0:59; bin 48 is P2 1:00-1:59.
"""

import pytest

from matcheyes.analytics.metrics import METRICS, Series, compute_series
from matcheyes.analytics.possessions import build_possessions
from matcheyes.analytics.summary import player_involvement, team_summaries
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.events import (
    BallRecovery,
    Pass,
    PassHeight,
    PassKind,
    PassOutcome,
    PeriodEnd,
    PeriodStart,
    Pressure,
)
from matcheyes.domain.match import ObservableMatch
from tests.support.builders import (
    AWAY,
    HOME,
    MATCH_ID,
    EventStream,
    at,
    match_info,
    minimal_match,
    pid,
)


def series_for(match: ObservableMatch) -> dict[tuple[str, str], Series]:
    timeline = Timeline(match.events)
    possessions = build_possessions(match.events)
    return compute_series(match.events, possessions, timeline, match.info.team_ids)


def totals(series: Series) -> tuple[float, float]:
    return sum(series.numerators), sum(series.denominators)


def eid(n: int) -> str:
    return f"{MATCH_ID}-e{n:05d}"


@pytest.fixture(scope="module")
def series() -> dict[tuple[str, str], Series]:
    return series_for(minimal_match())


def test_every_metric_has_a_series_per_team(series: dict[tuple[str, str], Series]) -> None:
    assert set(series) == {(m.name, t) for m in METRICS for t in (HOME, AWAY)}


def test_on_ball_share_in_the_first_minute(series: dict[tuple[str, str], Series]) -> None:
    # home kick-off + pass, away pass + carry + shot
    home, away = series["on_ball_share", HOME], series["on_ball_share", AWAY]
    assert (home.numerators[0], home.denominators[0]) == (2, 5)
    assert (away.numerators[0], away.denominators[0]) == (3, 5)


def test_shares_are_complementary_in_every_bin(series: dict[tuple[str, str], Series]) -> None:
    for metric in ("on_ball_share", "field_tilt"):
        home, away = series[metric, HOME], series[metric, AWAY]
        for i, den in enumerate(home.denominators):
            assert den == away.denominators[i]
            assert home.numerators[i] + away.numerators[i] == den


def test_field_tilt_counts_actions_starting_in_the_attacking_third(
    series: dict[tuple[str, str], Series],
) -> None:
    # away carry from x = 70 (inclusive) and shot from x = 88; home's second-half shot from x = 85
    assert totals(series["field_tilt", AWAY]) == (2, 3)
    assert totals(series["field_tilt", HOME]) == (1, 3)
    assert series["field_tilt", AWAY].event_ids[0] == (eid(7), eid(8))


def test_pressing_metrics(series: dict[tuple[str, str], Series]) -> None:
    # one away pressure at 6 s; the away tackle at 7 s is the next control event
    assert totals(series["pressures", AWAY])[0] == 1
    assert totals(series["pressure_regain_rate", AWAY]) == (1, 1)
    # tackle at x = 44 and interception at x = 25 are below the 65 m high-regain line
    assert totals(series["high_regains", AWAY])[0] == 0


def test_defensive_action_height_is_the_mean_x(series: dict[tuple[str, str], Series]) -> None:
    num, den = totals(series["defensive_action_height", AWAY])
    assert den == 3  # tackle 44, foul 55, interception 25
    assert num / den == pytest.approx((44 + 55 + 25) / 3)


def test_progression_and_entries(series: dict[tuple[str, str], Series]) -> None:
    # away pass 44->70 gains 27.3 m across halfway; carry 70->88 gains 18.4 m in the final half
    assert totals(series["progressive_actions", AWAY])[0] == 2
    assert totals(series["attacking_third_entries", AWAY])[0] == 1
    # home's 45->60 pass gains only 12.4 m across halfway
    assert totals(series["progressive_actions", HOME])[0] == 0


def test_passing_metrics_use_open_play_only(series: dict[tuple[str, str], Series]) -> None:
    assert totals(series["pass_completion", HOME]) == (1, 1)  # kick-offs, free kick, throw excluded
    assert totals(series["pass_completion", AWAY]) == (1, 2)  # second-half pass went out
    # completed passes ending in the opponent half, none in the wide channels
    assert totals(series["wide_share", HOME]) == (0, 1)


def test_shots_and_turnovers(series: dict[tuple[str, str], Series]) -> None:
    assert series["shots", AWAY].numerators[0] == 1
    assert series["shots", HOME].numerators[48] == 1
    turnovers = series["turnovers", HOME]
    assert turnovers.numerators[0] == 1 and turnovers.numerators[1] == 1
    assert totals(turnovers)[0] == 2
    assert all(d == 1 for d in turnovers.denominators)


def test_team_summaries() -> None:
    match = minimal_match()
    possessions = build_possessions(match.events)
    home, away = team_summaries(match.events, possessions, series_for(match), match.info)
    assert (home.goals, away.goals) == (0, 1)
    assert (home.possessions, away.possessions) == (3, 3)
    assert home.on_ball_share == pytest.approx(6 / 11)
    # PPDA: one home open-play pass in its first 60 %; away tackle (44) and foul (55) qualify
    assert away.ppda == pytest.approx(0.5)
    assert home.turnovers == 2
    # lost at x = 60 (end of the completed pass) and x = 50 (where the free kick was taken)
    assert home.own_half_turnovers == 1


def test_player_involvement_tracks_minutes_and_share() -> None:
    match = minimal_match()
    players = {p.player_id: p for p in player_involvement(match.events, match.info)}
    assert players[pid(HOME, 10)].minutes_played == pytest.approx(46.5)
    sub = players[pid(HOME, 15)]
    assert sub.minutes_played == pytest.approx(47.5)
    assert sub.on_ball_actions == 1
    assert sub.share_of_team_actions == pytest.approx(0.5)  # throw-in of {throw-in, shot}
    assert players[pid(HOME, 2)].share_of_team_actions == pytest.approx(1 / 6)
    assert pid(HOME, 12) not in players  # unused substitute


def test_pressure_regain_needs_the_ball_back_within_five_seconds() -> None:
    s = EventStream()
    s.add(PeriodStart, 1, 0)
    kick = {"height": PassHeight.GROUND, "outcome": PassOutcome.COMPLETE}
    s.add(
        Pass,
        1,
        1,
        team_id=HOME,
        player_id=pid(HOME, 9),
        location=at(52.5, 34),
        end_location=at(40, 34),
        kind=PassKind.KICK_OFF,
        **kick,
    )
    s.add(
        Pressure,
        1,
        2,
        team_id=AWAY,
        player_id=pid(AWAY, 9),
        location=at(65, 34),
        pressured_player_id=pid(HOME, 6),
    )
    s.add(BallRecovery, 1, 7.5, team_id=AWAY, player_id=pid(AWAY, 9), location=at(66, 34))
    s.add(
        Pressure,
        1,
        10,
        team_id=HOME,
        player_id=pid(HOME, 9),
        location=at(40, 34),
        pressured_player_id=pid(AWAY, 9),
    )
    s.add(BallRecovery, 1, 14, team_id=HOME, player_id=pid(HOME, 6), location=at(41, 34))
    s.add(PeriodEnd, 1, 60)
    result = series_for(ObservableMatch(info=match_info(), events=tuple(s.events)))
    assert totals(result["pressure_regain_rate", AWAY]) == (0, 1)  # 5.5 s: too slow
    assert totals(result["pressure_regain_rate", HOME]) == (1, 1)  # 4 s
    assert totals(result["high_regains", AWAY])[0] == 1  # x = 66 >= 65
