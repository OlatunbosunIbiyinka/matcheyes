"""Observable workload proxies, hand-computed on the minimal fixture."""

from matcheyes.analytics.timeline import Timeline
from matcheyes.analytics.workload import WorkloadSnapshot, build_workload
from matcheyes.domain.events import Card, CardType, PeriodEnd, PeriodStart
from tests.support.builders import AWAY, HOME, EventStream, match_info, minimal_match, pid


def _minimal() -> dict[str, tuple[WorkloadSnapshot, ...]]:
    match = minimal_match()
    return build_workload(match.events, Timeline(match.events), match.info)


def test_goalkeepers_are_excluded_and_minutes_start_at_zero() -> None:
    first = _minimal()[HOME][0]
    assert first.outfield_players == 10
    assert first.mean_outfield_minutes == 0
    assert first.recent_minutes == 0
    assert first.recent_actions_per_player == 0


def test_minutes_accumulate_and_a_substitute_starts_from_zero() -> None:
    home = _minimal()[HOME]
    assert home[47].mean_outfield_minutes == 47  # every starter played bins 0-46
    # h10 replaced by h15 in bin 47: nine starters on 48, the substitute on 0.
    assert home[48].mean_outfield_minutes == 9 * 48 / 10
    assert home[48].outfield_players == 10


def test_recent_activity_per_outfield_player() -> None:
    workload = _minimal()
    away_1, away_2 = workload[AWAY][1], workload[AWAY][2]
    # Bin 0: away pressure, tackle, pass, carry, shot. Bin 1: away foul and interception.
    assert (away_1.recent_actions_per_player, away_1.recent_pressures_per_player) == (0.5, 0.1)
    assert away_2.recent_actions_per_player == 0.7
    assert workload[HOME][1].recent_actions_per_player == 0.2  # two home passes in bin 0


def test_recent_window_length_is_respected() -> None:
    match = minimal_match()
    workload = build_workload(match.events, Timeline(match.events), match.info, window_bins=1)
    assert workload[AWAY][2].recent_minutes == 1
    assert workload[AWAY][2].recent_actions_per_player == 0.2  # bin 1 only


def test_dismissal_removes_an_outfield_player() -> None:
    s = EventStream()
    s.add(PeriodStart, 1, 0)
    s.add(Card, 1, 600, team_id=HOME, player_id=pid(HOME, 5), card=CardType.RED)
    s.add(Card, 1, 660, team_id=AWAY, player_id=pid(AWAY, 6), card=CardType.YELLOW)
    s.add(PeriodEnd, 1, 1200)
    workload = build_workload(s.events, Timeline(s.events), match_info())
    assert workload[HOME][10].outfield_players == 10
    assert workload[HOME][11].outfield_players == 9
    assert workload[AWAY][12].outfield_players == 10  # a yellow card changes nothing
