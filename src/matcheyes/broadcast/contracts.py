"""Typed contracts for the Stage 8 broadcast cue stream (ADR-0014).

A cue is one timed, machine-readable instruction to a broadcast surface: show a factual match
moment, show or revise a verified insight, retract one, or report the feed status. Every cue
names its source, so it can be traced to the observable events or to the lifecycle revision it
presents. Cue and timeline IDs are content addresses: SHA-256 over the canonical JSON of
everything else in them. No wall-clock time, request, connection or trace data takes part.

Display semantics, which a surface applies and the evaluation re-derives:

* a cue is on screen from `show_from`;
* a moment or retraction leaves the screen at `expires_at`;
* an insight, revision or status cue stays until a later cue lists it in `supersedes`.
"""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from matcheyes.agents.contracts import EvidenceIntegrity, Verdict
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.time import MatchInstant
from matcheyes.lifecycle.contracts import ChangeKind
from matcheyes.personalization.contracts import Audience, PersonalizationProfile

BROADCAST_VERSION = "0.1.0"
SHA256 = r"^[0-9a-f]{64}$"
CUE_ID = r"^cue-[0-9a-f]{24}$"

MAX_ON_SCREEN = 3
"""Insight cards per surface. A newer insight waits for a free card; nothing is displaced."""
MOMENT_DWELL_MS = 2 * 60_000
RETRACTION_DWELL_MS = 2 * 60_000
KICK_OFF = MatchInstant(period=1, clock_ms=0)

BROADCAST_AUDIENCES = frozenset({Audience.FAN, Audience.BROADCASTER})
"""The broadcast surface serves fans and broadcasters; the analyst timeline is not on screen."""


class CueKind(StrEnum):
    MOMENT = "moment"
    INSIGHT = "insight"
    REVISION = "revision"
    RETRACTION = "retraction"
    STATUS = "status"


class BroadcastStatus(StrEnum):
    CURRENT = "current"
    NO_CANDIDATE = "no_candidate"
    UNAVAILABLE = "unavailable"
    DATA_INCOMPLETE = "data_incomplete"
    AWAITING_SNAPSHOT = "awaiting_snapshot"


class MomentKind(StrEnum):
    GOAL = "goal"
    RED_CARD = "red_card"
    SUBSTITUTION = "substitution"
    PERIOD_START = "period_start"
    PERIOD_END = "period_end"


class RetractionReason(StrEnum):
    WITHDRAWN = "withdrawn"
    """The latest snapshot no longer detects the change."""
    REPLACED = "replaced"
    """An opposite-direction change replaced it."""
    NO_VERIFIED_EXPLANATION = "no_verified_explanation"
    """Revised to a verdict without a verified explanation (off the primary surface)."""
    WITHHELD = "withheld"
    """The revised insight or its view failed audit on this snapshot."""
    UNAVAILABLE = "unavailable"
    """The latest snapshot failed or was invalid: no current truth."""


RETRACTION_TEXT = {
    RetractionReason.WITHDRAWN: "Retracted: the change is no longer detected in the current data.",
    RetractionReason.REPLACED: "Retracted: replaced by an opposite-direction change.",
    RetractionReason.NO_VERIFIED_EXPLANATION: (
        "Retracted: on the latest data this change no longer has a verified explanation."
    ),
    RetractionReason.WITHHELD: "Retracted: the revised insight did not pass audit.",
    RetractionReason.UNAVAILABLE: (
        "Retracted: current verification is unavailable, so this is no longer shown as current."
    ),
}

STATUS_TEXT = {
    BroadcastStatus.CURRENT: "Verified insights are current as of the latest snapshot.",
    BroadcastStatus.NO_CANDIDATE: "No verified insight available.",
    BroadcastStatus.UNAVAILABLE: "Current verification unavailable; earlier insights are history.",
    BroadcastStatus.DATA_INCOMPLETE: "Data incomplete: events are missing; insights cover the data "
    "received before the gap.",
    BroadcastStatus.AWAITING_SNAPSHOT: "Awaiting the first completed minute.",
}


class Score(DomainModel):
    home: int = Field(ge=0)
    away: int = Field(ge=0)


class MomentSource(DomainModel):
    source_kind: Literal["moment"] = "moment"
    moment: MomentKind
    event_ids: tuple[Identifier, ...] = Field(min_length=1)
    at: MatchInstant = Field(description="When the event happened on the match clock.")
    snapshot_id: Identifier = Field(description="The canonical snapshot that first contains it.")
    team_id: Identifier | None
    score: Score = Field(description="Score after this moment, from observable goal events.")


class InsightSource(DomainModel):
    source_kind: Literal["insight"] = "insight"
    storyline_id: Identifier
    revision: int = Field(ge=1)
    insight_fingerprint: str = Field(pattern=SHA256)
    snapshot_id: Identifier = Field(description="The snapshot on which the revision is current.")
    view_fingerprint: str = Field(pattern=SHA256, description="SHA-256 of the Stage 6 view.")
    verdict: Verdict
    evidence_integrity: EvidenceIntegrity
    change_kinds: tuple[ChangeKind, ...]


class RetractionSource(DomainModel):
    source_kind: Literal["retraction"] = "retraction"
    retracts: str = Field(pattern=CUE_ID)
    storyline_id: Identifier
    revision: int = Field(ge=1, description="The displayed revision that stopped being current.")
    cause_revision: int | None = Field(
        ge=1,
        description="The revision that ended it (withdrawal or demotion); None if unavailable.",
    )
    reason: RetractionReason
    snapshot_id: Identifier


class StatusSource(DomainModel):
    source_kind: Literal["status"] = "status"
    status: BroadcastStatus
    snapshot_id: Identifier | None
    watermark: int = Field(ge=0)
    detail: str | None = Field(default=None, max_length=200)


CueSource = Annotated[
    MomentSource | InsightSource | RetractionSource | StatusSource,
    Field(discriminator="source_kind"),
]
SOURCE_FOR = {
    CueKind.MOMENT: MomentSource,
    CueKind.INSIGHT: InsightSource,
    CueKind.REVISION: InsightSource,
    CueKind.RETRACTION: RetractionSource,
    CueKind.STATUS: StatusSource,
}


class CueSection(DomainModel):
    kind: str = Field(pattern=r"^[a-z_]{1,24}$")
    text: str = Field(min_length=1, max_length=4000)
    mandatory: bool = False


def content_id(payload: dict[str, object], prefix: str, length: int) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return prefix + hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


class Cue(DomainModel):
    cue_id: str = Field(pattern=CUE_ID)
    match_id: Identifier
    kind: CueKind
    audience: Audience | None = Field(description="None: the same cue for every audience.")
    show_from: MatchInstant
    expires_at: MatchInstant | None
    supersedes: tuple[str, ...] = ()
    priority: int = Field(ge=0, le=100)
    interrupt: bool = Field(description="Whether the surface should draw attention to it.")
    sections: tuple[CueSection, ...] = Field(min_length=1)
    snapshot_id: Identifier | None
    source: CueSource

    @staticmethod
    def identity(payload: dict[str, object]) -> str:
        return content_id({k: v for k, v in payload.items() if k != "cue_id"}, "cue-", 24)

    @classmethod
    def build(cls, **fields: Any) -> "Cue":
        payload = cls.model_construct(cue_id="", **fields).model_dump(mode="json")
        return cls.model_validate({**payload, "cue_id": cls.identity(payload)})

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.cue_id != self.identity(self.model_dump(mode="json")):
            raise ValueError("cue ID is not the content address of the cue")
        if not isinstance(self.source, SOURCE_FOR[self.kind]):
            raise ValueError(f"a {self.kind.value} cue needs a {SOURCE_FOR[self.kind].__name__}")
        if self.source.snapshot_id != self.snapshot_id:
            raise ValueError("the cue and its source name different snapshots")
        timed = self.kind in (CueKind.MOMENT, CueKind.RETRACTION)
        if timed != (self.expires_at is not None):
            raise ValueError("only moments and retractions expire; other cues are superseded")
        if self.expires_at is not None and self.expires_at.sort_key <= self.show_from.sort_key:
            raise ValueError("a cue must expire after it is shown")
        if (self.kind in (CueKind.INSIGHT, CueKind.REVISION)) != (self.audience is not None):
            raise ValueError("insight and revision cues are for one audience; others for all")
        if self.audience is not None and self.audience not in BROADCAST_AUDIENCES:
            raise ValueError("the broadcast surface serves fans and broadcasters only")
        if self.kind is CueKind.REVISION and len(self.supersedes) != 1:
            raise ValueError("a revision supersedes exactly the cue it revises")
        if self.kind is CueKind.RETRACTION and (
            not isinstance(self.source, RetractionSource)
            or self.supersedes != (self.source.retracts,)
        ):
            raise ValueError("a retraction supersedes exactly the cue it retracts")
        if self.kind in (CueKind.INSIGHT, CueKind.MOMENT) and self.supersedes:
            raise ValueError("insight and moment cues supersede nothing")
        return self


class TeamRef(DomainModel):
    team_id: Identifier
    name: str
    short_name: str


class CueTimeline(DomainModel):
    """The ordered cues of one surface (audience and favourite club) for one match."""

    broadcast_version: str = BROADCAST_VERSION
    timeline_id: str = Field(pattern=r"^tl-[0-9a-f]{32}$")
    match_id: Identifier
    home: TeamRef
    away: TeamRef
    profile: PersonalizationProfile
    watermark: int = Field(ge=0, description="Events of the contiguous prefix compiled.")
    snapshots: int = Field(ge=0, description="Canonical snapshots compiled.")
    cues: tuple[Cue, ...]

    @staticmethod
    def identity(payload: dict[str, object]) -> str:
        return content_id({k: v for k, v in payload.items() if k != "timeline_id"}, "tl-", 32)

    @classmethod
    def build(cls, **fields: Any) -> "CueTimeline":
        payload = cls.model_construct(timeline_id="", **fields).model_dump(mode="json")
        return cls.model_validate({**payload, "timeline_id": cls.identity(payload)})

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.timeline_id != self.identity(self.model_dump(mode="json")):
            raise ValueError("timeline ID is not the content address of the timeline")
        if self.profile.audience not in BROADCAST_AUDIENCES:
            raise ValueError("the broadcast surface serves fans and broadcasters only")
        ids = [c.cue_id for c in self.cues]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate cue IDs")
        keys = [c.show_from.sort_key for c in self.cues]
        if keys != sorted(keys):
            raise ValueError("cues must be ordered by match time")
        seen: set[str] = set()
        for c in self.cues:
            if c.match_id != self.match_id:
                raise ValueError("a cue belongs to another match")
            if c.audience not in (None, self.profile.audience):
                raise ValueError("a cue belongs to another audience")
            if not set(c.supersedes) <= seen:
                raise ValueError("a cue supersedes a cue that is not earlier in the timeline")
            seen.add(c.cue_id)
        return self
