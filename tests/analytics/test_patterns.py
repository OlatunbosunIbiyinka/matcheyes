"""Multi-signal patterns on hand-built series."""

import pytest

from matcheyes.analytics.baselines import Direction
from matcheyes.analytics.metrics import METRIC_BY_NAME, Series
from matcheyes.analytics.patterns import (
    PATTERNS,
    PatternAssessment,
    assess_patterns,
    is_independent,
)
from tests.support.builders import AWAY, HOME, match_info
from tests.support.series import flat_series, series_from, step

SPANS = ((0, 30), (30, 45))


def _assess(
    series: dict[tuple[str, str], Series], team: str, metric: str, direction: Direction
) -> PatternAssessment | None:
    return assess_patterns(team, metric, direction, SPANS, series, match_info(), 1.5, 1.0)


@pytest.fixture
def series() -> dict[tuple[str, str], Series]:
    return flat_series((HOME, AWAY))


def test_support_from_another_family_is_independent(series: dict[tuple[str, str], Series]) -> None:
    series["high_regains", HOME] = series_from("high_regains", HOME, step(1, 3))
    series["pass_completion", AWAY] = series_from("pass_completion", AWAY, step(0.8, 0.5))
    result = _assess(series, HOME, "pressures", "up")
    assert result is not None
    assert result.pattern == "pressing_intensification"
    assert result.subject_team_id == HOME
    supported = {r.signal.metric: r.independent for r in result.supports}
    assert supported == {"high_regains": False, "pass_completion": True}
    assert result.independent_families == ("passing",)
    assert not result.contradictions
    assert not result.contradicted


def test_contradiction_is_reported_and_can_outweigh_support(
    series: dict[tuple[str, str], Series],
) -> None:
    series["pass_completion", AWAY] = series_from("pass_completion", AWAY, step(0.8, 0.5))
    # The press should make the opponent lose the ball more, not less: 2 -> 0.5 per minute.
    series["turnovers", AWAY] = series_from("turnovers", AWAY, step(2, 0.5))
    result = _assess(series, HOME, "pressures", "up")
    assert result is not None
    (contradiction,) = result.contradictions
    assert contradiction.signal.metric == "turnovers"
    assert contradiction.statistic is not None and contradiction.statistic < -1.0
    assert result.contradicted  # one contradiction against one independent support


def test_flat_signals_are_neutral(series: dict[tuple[str, str], Series]) -> None:
    result = _assess(series, HOME, "pressures", "up")
    assert result is not None
    assert {r.status for r in result.readings} == {"neutral"}
    assert result.independent_families == ()


def test_thin_windows_are_unavailable(series: dict[tuple[str, str], Series]) -> None:
    series["pass_completion", AWAY] = series_from(
        "pass_completion", AWAY, [0.5] * 60, trials=[0.0] * 60
    )
    result = _assess(series, HOME, "pressures", "up")
    assert result is not None
    reading = next(r for r in result.readings if r.signal.metric == "pass_completion")
    assert (reading.status, reading.statistic) == ("unavailable", None)


def test_mechanically_linked_metrics_only_corroborate(
    series: dict[tuple[str, str], Series],
) -> None:
    series["defensive_action_height", HOME] = series_from(
        "defensive_action_height", HOME, step(40, 55)
    )
    result = _assess(series, HOME, "high_regains", "up")
    assert result is not None
    height = next(r for r in result.readings if r.signal.metric == "defensive_action_height")
    assert height.status == "support"
    assert not height.independent
    assert result.independent_families == ()


def test_three_supporting_metrics_are_not_three_independent_witnesses(
    series: dict[tuple[str, str], Series],
) -> None:
    series["attacking_third_entries", HOME] = series_from(
        "attacking_third_entries", HOME, step(1, 3)
    )
    series["shots", HOME] = series_from("shots", HOME, step(0.2, 1.0))
    series["defensive_action_height", AWAY] = series_from(
        "defensive_action_height", AWAY, step(45, 30)
    )
    result = _assess(series, HOME, "field_tilt", "up")
    assert result is not None
    assert result.pattern == "territorial_dominance"
    assert len(result.supports) == 3
    assert result.independent_families == ("defending",)


def test_pattern_can_describe_the_opponent(series: dict[tuple[str, str], Series]) -> None:
    series["high_regains", AWAY] = series_from("high_regains", AWAY, step(2, 0.3))
    result = _assess(series, HOME, "field_tilt", "up")
    assert result is not None
    assert (result.pattern, result.subject_team_id) == ("deeper_defending", AWAY)
    assert result.independent_families == ("pressing",)


def test_signal_without_a_pattern(series: dict[tuple[str, str], Series]) -> None:
    assert _assess(series, HOME, "wide_share", "down") is None


def test_independence_rules() -> None:
    assert is_independent("pressures", "pass_completion")
    assert not is_independent("pressures", "high_regains")  # same family
    assert not is_independent("high_regains", "defensive_action_height")  # shared events
    assert not is_independent("turnovers", "pass_completion")


def test_pattern_catalogue_is_well_formed() -> None:
    names = [p.name for p in PATTERNS]
    assert len(names) == len(set(names))
    for pattern in PATTERNS:
        keys = [(s.side, s.metric) for s in pattern.signals]
        assert len(keys) == len(set(keys)), pattern.name
        for signal in pattern.signals:
            spec = METRIC_BY_NAME[signal.metric]
            if spec.complementary:
                assert signal.direction == "up", f"{pattern.name}: {signal.metric}"
