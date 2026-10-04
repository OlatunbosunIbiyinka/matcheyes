import math

import pytest

from matcheyes.analytics.geometry import (
    Third,
    distance_to_goal,
    enters_attacking_third,
    enters_penalty_box,
    in_penalty_box,
    is_progressive,
    is_wide,
    third,
)
from tests.support.builders import at


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (0, Third.DEFENSIVE),
        (34.9, Third.DEFENSIVE),
        (35, Third.MIDDLE),
        (69.9, Third.MIDDLE),
        (70, Third.ATTACKING),
        (105, Third.ATTACKING),
    ],
)
def test_thirds_split_the_pitch_at_35_and_70_metres(x: float, expected: Third) -> None:
    assert third(at(x, 34)) is expected


def test_penalty_box_is_16_5_m_deep_and_40_32_m_wide() -> None:
    assert in_penalty_box(at(88.5, 34))
    assert in_penalty_box(at(100, 34 + 20.16))
    assert not in_penalty_box(at(88.4, 34))
    assert not in_penalty_box(at(100, 34 + 20.2))


def test_wide_channels_lie_outside_the_box_width() -> None:
    assert is_wide(at(60, 5))
    assert is_wide(at(60, 63))
    assert not is_wide(at(60, 34))
    assert not is_wide(at(60, 34 - 20.16))


def test_distance_to_goal_measures_to_the_goal_centre() -> None:
    assert distance_to_goal(at(105, 34)) == 0
    assert distance_to_goal(at(93, 29)) == pytest.approx(13.0)
    assert distance_to_goal(at(0, 34)) == 105


def test_progressive_threshold_tightens_towards_goal() -> None:
    # own half to own half: 30 m needed (gain 29 vs 31)
    assert not is_progressive(at(10, 34), at(39, 34))
    assert is_progressive(at(10, 34), at(41, 34))
    # crossing halfway: 15 m
    assert not is_progressive(at(45, 34), at(59, 34))
    assert is_progressive(at(45, 34), at(60.5, 34))
    # opponent half: 10 m
    assert not is_progressive(at(60, 34), at(69, 34))
    assert is_progressive(at(60, 34), at(70, 34))


def test_progressive_uses_distance_not_x_gain() -> None:
    start, end = at(60, 0), at(75, 0)
    gain = distance_to_goal(start) - distance_to_goal(end)
    assert gain == pytest.approx(math.hypot(45, 34) - math.hypot(30, 34))
    assert is_progressive(start, end) is (gain >= 10)


def test_entries_require_starting_outside() -> None:
    assert enters_attacking_third(at(60, 34), at(75, 34))
    assert not enters_attacking_third(at(72, 34), at(80, 34))
    assert enters_penalty_box(at(80, 34), at(95, 34))
    assert not enters_penalty_box(at(90, 34), at(95, 34))
