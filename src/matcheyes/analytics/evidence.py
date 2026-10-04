"""Evidence objects: the structured, traceable output of deterministic analytics.

Every evidence object carries a provenance label (FACT: read directly from events; ANALYSIS:
computed from them), a claim strength that can never exceed ASSOCIATED - deterministic
analytics observe and co-locate changes, they do not establish causes - and the event IDs a
reader can follow to check it.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from matcheyes.domain.base import DomainModel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.entities import Identifier
from matcheyes.domain.time import MatchInstant

ANALYTICS_CLAIM_CEILING = ClaimStrength.ASSOCIATED


class EvidenceLabel(StrEnum):
    FACT = "fact"
    ANALYSIS = "analysis"


class KeyEventType(StrEnum):
    GOAL = "goal"
    DISMISSAL = "dismissal"
    SUBSTITUTION = "substitution"
    FORMATION_CHANGE = "formation_change"


class _Evidence(DomainModel):
    evidence_id: Identifier
    label: EvidenceLabel
    strength: ClaimStrength
    team_id: Identifier
    at: MatchInstant
    statement: str = Field(min_length=1)
    event_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _within_ceiling(self) -> Self:
        if self.strength.rank > ANALYTICS_CLAIM_CEILING.rank:
            raise ValueError(
                f"{self.evidence_id}: deterministic analytics cannot claim {self.strength}"
            )
        return self


class MetricShiftEvidence(_Evidence):
    kind: Literal["metric_shift"] = "metric_shift"
    metric: str
    family: str
    direction: Literal["up", "down"]
    before_start: MatchInstant
    after_end: MatchInstant
    before_value: float
    after_value: float
    before_sample: float
    after_sample: float
    statistic: float


class KeyEventEvidence(_Evidence):
    kind: Literal["key_event"] = "key_event"
    event_type: KeyEventType
    player_id: Identifier | None
    related_player_id: Identifier | None = None
    detail: str | None = None


class RunOfPlayEvidence(_Evidence):
    """The balance of play in the minutes before a goal, from the scoring team's side."""

    kind: Literal["run_of_play"] = "run_of_play"
    goal_evidence_id: Identifier
    window_minutes: int = Field(ge=1)
    field_tilt: float | None
    on_ball_share: float | None
    shots_for: int = Field(ge=0)
    shots_against: int = Field(ge=0)
    against_run_of_play: bool


class PlayerInvolvementEvidence(_Evidence):
    """Involvement of a substitute compared with the player they replaced."""

    kind: Literal["player_involvement"] = "player_involvement"
    player_id: Identifier
    replaced_player_id: Identifier
    window_minutes: int = Field(ge=1)
    actions: int = Field(ge=0)
    share: float | None
    replaced_actions: int = Field(ge=0)
    replaced_share: float | None


Evidence = Annotated[
    MetricShiftEvidence | KeyEventEvidence | RunOfPlayEvidence | PlayerInvolvementEvidence,
    Field(discriminator="kind"),
]


class MomentKind(StrEnum):
    METRIC_SHIFTS = "metric_shifts"
    KEY_EVENT = "key_event"


class CandidateMoment(DomainModel):
    """A point in the match worth explaining, with the evidence that flagged it.

    `context_evidence_ids` lists key events close in time. They are temporal context only:
    proximity is never presented as cause.
    """

    moment_id: Identifier
    kind: MomentKind
    team_id: Identifier
    at: MatchInstant
    label: Literal[EvidenceLabel.ANALYSIS] = EvidenceLabel.ANALYSIS
    strength: ClaimStrength
    headline: str = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    families: tuple[str, ...]
    score: float = Field(ge=0)
    context_evidence_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _within_ceiling(self) -> Self:
        if self.strength.rank > ANALYTICS_CLAIM_CEILING.rank:
            raise ValueError(f"{self.moment_id}: deterministic analytics cannot claim causes")
        return self
