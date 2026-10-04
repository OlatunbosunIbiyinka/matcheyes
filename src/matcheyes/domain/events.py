"""Observable match events: the complete event vocabulary MatchEyes may consume.

Deliberately absent: possession IDs, xG, momentum, pass difficulty and any tactical state.
Those are either derived by deterministic analytics (Stage 2) or hidden ground truth that
MatchEyes must infer, never receive.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.pitch import Location
from matcheyes.domain.time import MatchInstant, Period

MAX_BALL_SPEED_KMH = 150.0


class PassOutcome(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    OUT_OF_PLAY = "out_of_play"
    OFFSIDE = "offside"


class PassHeight(StrEnum):
    GROUND = "ground"
    LOW = "low"
    HIGH = "high"


class PassKind(StrEnum):
    OPEN_PLAY = "open_play"
    KICK_OFF = "kick_off"
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    CORNER = "corner"
    FREE_KICK = "free_kick"


class ShotOutcome(StrEnum):
    GOAL = "goal"
    SAVED = "saved"
    OFF_TARGET = "off_target"
    BLOCKED = "blocked"
    WOODWORK = "woodwork"


class ShotKind(StrEnum):
    OPEN_PLAY = "open_play"
    FREE_KICK = "free_kick"
    PENALTY = "penalty"
    CORNER = "corner"


class BodyPart(StrEnum):
    RIGHT_FOOT = "right_foot"
    LEFT_FOOT = "left_foot"
    HEAD = "head"
    OTHER = "other"


class DuelOutcome(StrEnum):
    WON = "won"
    LOST = "lost"


class CardType(StrEnum):
    YELLOW = "yellow"
    SECOND_YELLOW = "second_yellow"
    RED = "red"


class _EventBase(DomainModel):
    event_id: Identifier = Field(min_length=1)
    match_id: Identifier = Field(min_length=1)
    sequence: int = Field(ge=1, description="Canonical producer order; contiguous from 1.")
    period: Period
    clock_ms: int = Field(ge=0, description="Milliseconds since this period's kick-off.")

    @property
    def instant(self) -> MatchInstant:
        return MatchInstant(period=self.period, clock_ms=self.clock_ms)


class _PlayerEvent(_EventBase):
    team_id: Identifier = Field(min_length=1)
    player_id: Identifier = Field(min_length=1)


class _OnPitchAction(_PlayerEvent):
    location: Location


class PeriodStart(_EventBase):
    type: Literal["period_start"] = "period_start"


class PeriodEnd(_EventBase):
    type: Literal["period_end"] = "period_end"


class Pass(_OnPitchAction):
    type: Literal["pass"] = "pass"
    end_location: Location
    outcome: PassOutcome
    recipient_id: Identifier | None = None
    height: PassHeight
    kind: PassKind
    ball_speed_kmh: float | None = Field(default=None, ge=0.0, le=MAX_BALL_SPEED_KMH)


class Carry(_OnPitchAction):
    type: Literal["carry"] = "carry"
    end_location: Location


class TakeOn(_OnPitchAction):
    type: Literal["take_on"] = "take_on"
    outcome: DuelOutcome
    opponent_id: Identifier | None = None


class Shot(_OnPitchAction):
    type: Literal["shot"] = "shot"
    end_location: Location
    outcome: ShotOutcome
    kind: ShotKind
    body_part: BodyPart
    goalkeeper_id: Identifier | None = None
    ball_speed_kmh: float | None = Field(default=None, ge=0.0, le=MAX_BALL_SPEED_KMH)


class Pressure(_OnPitchAction):
    """A defender closing down the ball carrier. `location` is in the pressing team's frame."""

    type: Literal["pressure"] = "pressure"
    pressured_player_id: Identifier


class Tackle(_OnPitchAction):
    type: Literal["tackle"] = "tackle"
    outcome: DuelOutcome
    opponent_id: Identifier


class Interception(_OnPitchAction):
    type: Literal["interception"] = "interception"


class BallRecovery(_OnPitchAction):
    type: Literal["ball_recovery"] = "ball_recovery"


class Clearance(_OnPitchAction):
    type: Literal["clearance"] = "clearance"


class Block(_OnPitchAction):
    type: Literal["block"] = "block"


class Foul(_OnPitchAction):
    type: Literal["foul"] = "foul"
    fouled_player_id: Identifier


class OwnGoal(_OnPitchAction):
    """`team_id` / `player_id` are the player who put the ball into their own net."""

    type: Literal["own_goal"] = "own_goal"


class Card(_PlayerEvent):
    type: Literal["card"] = "card"
    card: CardType


class Substitution(_PlayerEvent):
    """`player_id` leaves the pitch; `replacement_id` comes on."""

    type: Literal["substitution"] = "substitution"
    replacement_id: Identifier = Field(min_length=1)


class FormationChange(_EventBase):
    type: Literal["formation_change"] = "formation_change"
    team_id: Identifier = Field(min_length=1)
    formation: str = Field(pattern=r"^[1-9](-[1-9]){2,4}$")


MatchEvent = Annotated[
    PeriodStart
    | PeriodEnd
    | Pass
    | Carry
    | TakeOn
    | Shot
    | Pressure
    | Tackle
    | Interception
    | BallRecovery
    | Clearance
    | Block
    | Foul
    | OwnGoal
    | Card
    | Substitution
    | FormationChange,
    Field(discriminator="type"),
]

ON_BALL_TYPES: tuple[type[_OnPitchAction], ...] = (Pass, Carry, TakeOn, Shot)
"""Actions by the team in possession; used for restart rules."""
