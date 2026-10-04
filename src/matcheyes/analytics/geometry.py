"""Pitch zones and spatial predicates, all in the acting team's attacking frame.

Zone boundaries follow standard pitch markings and common analytics conventions, not anything
about how a particular data provider generates events.
"""

import math
from enum import StrEnum

from matcheyes.domain.pitch import PITCH_LENGTH_M, PITCH_WIDTH_M, Location

DEFENSIVE_THIRD_END_X = PITCH_LENGTH_M / 3
ATTACKING_THIRD_START_X = 2 * PITCH_LENGTH_M / 3
PENALTY_BOX_START_X = PITCH_LENGTH_M - 16.5
PENALTY_BOX_HALF_WIDTH = 20.16
HIGH_REGAIN_MIN_X = PITCH_LENGTH_M - 40.0
"""Regains within 40 m of the opponent's goal line: the usual "high turnover" zone."""

WIDE_CHANNEL_HALF_WIDTH = PENALTY_BOX_HALF_WIDTH
"""Wide channels are the strips outside the penalty box's width, touchline to box edge."""

GOAL_CENTRE = Location(x=PITCH_LENGTH_M, y=PITCH_WIDTH_M / 2)
HALFWAY_X = PITCH_LENGTH_M / 2


class Third(StrEnum):
    DEFENSIVE = "defensive"
    MIDDLE = "middle"
    ATTACKING = "attacking"


def third(location: Location) -> Third:
    if location.x < DEFENSIVE_THIRD_END_X:
        return Third.DEFENSIVE
    if location.x < ATTACKING_THIRD_START_X:
        return Third.MIDDLE
    return Third.ATTACKING


def in_attacking_third(location: Location) -> bool:
    return location.x >= ATTACKING_THIRD_START_X


def in_penalty_box(location: Location) -> bool:
    return (
        location.x >= PENALTY_BOX_START_X
        and abs(location.y - PITCH_WIDTH_M / 2) <= PENALTY_BOX_HALF_WIDTH
    )


def is_wide(location: Location) -> bool:
    return abs(location.y - PITCH_WIDTH_M / 2) > WIDE_CHANNEL_HALF_WIDTH


def distance_to_goal(location: Location) -> float:
    return math.hypot(GOAL_CENTRE.x - location.x, GOAL_CENTRE.y - location.y)


def is_progressive(start: Location, end: Location) -> bool:
    """Moves the ball materially closer to goal; the required gain shrinks nearer goal.

    30 m when both points are in the own half, 15 m when crossing halfway, 10 m when both
    are in the opponent's half - a widely used public definition of a progressive action.
    """
    gain = distance_to_goal(start) - distance_to_goal(end)
    start_own = start.x < HALFWAY_X
    end_own = end.x < HALFWAY_X
    if start_own and end_own:
        return gain >= 30.0
    if start_own != end_own:
        return gain >= 15.0
    return gain >= 10.0


def enters_attacking_third(start: Location, end: Location) -> bool:
    return not in_attacking_third(start) and in_attacking_third(end)


def enters_penalty_box(start: Location, end: Location) -> bool:
    return not in_penalty_box(start) and in_penalty_box(end)
