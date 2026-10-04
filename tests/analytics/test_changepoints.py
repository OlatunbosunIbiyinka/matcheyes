import math

import pytest

from matcheyes.analytics.changepoints import (
    DetectionConfig,
    _clustered_variance,
    compare,
    detect_shifts,
    is_material,
    scan,
)
from matcheyes.analytics.metrics import METRIC_BY_NAME, Series


def series(metric: str, num: list[float], den: list[float] | None = None) -> Series:
    den = den or [1.0] * len(num)
    return Series(
        metric=metric,
        team_id="t",
        numerators=tuple(num),
        denominators=tuple(den),
        squares=tuple(0.0 for _ in num),
        event_ids=tuple(() for _ in num),
    )


def test_count_statistic_uses_poisson_floor() -> None:
    s = series("shots", [1.0] * 10 + [4.0] * 10)
    shift = compare(s, METRIC_BY_NAME["shots"], 10, 10)
    assert shift is not None
    # sample variances are 0, so each window falls back to its mean: (4 - 1) / sqrt(1/10 + 4/10)
    assert shift.statistic == pytest.approx(3 / math.sqrt(0.5))
    assert (shift.before.value, shift.after.value, shift.delta) == (1.0, 4.0, 3.0)
    assert shift.direction == "up"


def test_clustered_variance_by_hand() -> None:
    # p = 0.5; residuals (1-1)^2 + (3-1)^2 = 4; m = 2, mean n = 2 -> 4 / (2 * 1 * 4)
    assert _clustered_variance([1, 3], [2, 2], 0.5) == pytest.approx(0.5)
    assert _clustered_variance([1], [2], 0.5) == 0.0


def test_proportion_uses_the_larger_of_binomial_and_clustered_variance() -> None:
    spec = METRIC_BY_NAME["pass_completion"]
    steady = compare(series("pass_completion", [8.0] * 20, [10.0] * 20), spec, 10, 10)
    assert steady is not None and steady.statistic == 0
    jump = series("pass_completion", [7.0] * 10 + [9.0] * 10, [10.0] * 20)
    shift = compare(jump, spec, 10, 10)
    assert shift is not None
    # minute values are constant within each window, so the binomial variance applies
    expected = 0.2 / math.sqrt(0.7 * 0.3 / 100 + 0.9 * 0.1 / 100)
    assert shift.statistic == pytest.approx(expected)


def test_thin_windows_are_skipped() -> None:
    spec = METRIC_BY_NAME["pass_completion"]  # needs 40 trials per window
    thin = series("pass_completion", [1.0] * 20, [2.0] * 20)
    assert compare(thin, spec, 10, 10) is None


def test_material_effect_floors() -> None:
    shots = METRIC_BY_NAME["shots"]  # relative: 50 % of the larger window value
    big = compare(series("shots", [0.2] * 10 + [0.5] * 10), shots, 10, 10)
    small = compare(series("shots", [0.4] * 10 + [0.5] * 10), shots, 10, 10)
    assert big is not None and is_material(shots, big)
    assert small is not None and not is_material(shots, small)


def test_detect_shifts_reports_one_peak_per_change() -> None:
    s = series("shots", [0.0] * 20 + [1.0] * 20)
    config = DetectionConfig(window_bins=10, baseline_bins=10, z_threshold=2.0)
    # at t = 20 the statistic is 1 / sqrt(1/10) = 3.16; neighbours are lower (t = 19: 3.0)
    assert [x.bin_index for x in detect_shifts(s, config)] == [20]
    assert len([x for x in scan(s, config) if abs(x.statistic) >= 2.0]) > 1


def test_all_zero_windows_carry_no_information() -> None:
    assert scan(series("shots", [0.0] * 40), DetectionConfig()) == []


def test_baseline_shrinks_near_kick_off_but_not_below_the_window() -> None:
    config = DetectionConfig(window_bins=10, baseline_bins=20)
    boundaries = scan(series("shots", [1.0] * 40), config)
    assert [x.bin_index for x in boundaries] == list(range(10, 31))
    assert (boundaries[0].before.start_bin, boundaries[0].before.end_bin) == (0, 10)
    assert (boundaries[-1].before.start_bin, boundaries[-1].before.end_bin) == (10, 30)


def test_complementary_metrics_report_only_the_gaining_team() -> None:
    falling = series("field_tilt", [6.0] * 10 + [2.0] * 10, [10.0] * 20)
    rising = series("field_tilt", [4.0] * 10 + [8.0] * 10, [10.0] * 20)
    config = DetectionConfig(window_bins=10, baseline_bins=10, z_threshold=2.0)
    assert detect_shifts(falling, config) == []
    assert len(detect_shifts(rising, config)) == 1
