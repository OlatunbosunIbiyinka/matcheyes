"""Lifecycle contracts: canonical snapshots, storylines and their append-only revisions.

A snapshot is the authoritative match state as of one contiguous event prefix. A storyline is
one observed phenomenon for one team, followed across snapshots. Each revision records what the
existing pipeline established on one snapshot: a complete verified and audited `FinalInsight`,
or a withdrawal. Revisions are never edited; each is chained to its predecessor by fingerprint.

Nothing here carries wall-clock time or trace data, so the canonical state of a replay is
byte-identical to the original.
"""

import hashlib
import json
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from matcheyes.agents.contracts import EvidenceIntegrity, FinalInsight, VerificationResult
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.time import MatchInstant, Period
from matcheyes.personalization.contracts import fingerprint

LIFECYCLE_VERSION = "0.1.0"
SCHEDULE: Final = "closed_minute"
GENESIS = "0" * 64
"""Chain fingerprint before a storyline's first revision."""

Direction = Literal["up", "down"]
SHA256 = r"^[0-9a-f]{64}$"


class SnapshotHeader(DomainModel):
    """Identity of a canonical snapshot: the contiguous prefix ending at `watermark`, taken when
    match minute `minute` of `period` closed. `as_of` is the end of that minute."""

    snapshot_id: Identifier
    match_id: Identifier
    watermark: int = Field(ge=1)
    period: Period
    minute: int = Field(ge=0)
    as_of: MatchInstant
    log_digest: str = Field(pattern=SHA256)
    info_digest: str = Field(pattern=SHA256)

    @property
    def label(self) -> str:
        return MatchInstant(period=self.period, clock_ms=self.minute * 60_000).display_minute


class SnapshotOutcome(StrEnum):
    EVALUATED = "evaluated"
    INVALID = "invalid"
    """The prefix broke a data invariant; nothing was evaluated."""
    FAILED = "failed"
    """The pipeline did not complete; no truth was established on this snapshot."""


class SnapshotRecord(DomainModel):
    header: SnapshotHeader
    outcome: SnapshotOutcome
    failure: str | None = None
    insights: int = Field(ge=0)
    storyline_ids: tuple[Identifier, ...] = Field(
        description="Storylines established on this snapshot (matched or created)."
    )


class ChangeKind(StrEnum):
    CREATED = "created"
    REANCHORED = "reanchored"
    VERDICT_CHANGED = "verdict_changed"
    STRENGTH_CHANGED = "strength_changed"
    EXPLANATION_CHANGED = "explanation_changed"
    INTEGRITY_CHANGED = "integrity_changed"
    EVIDENCE_CHANGED = "evidence_changed"
    WITHDRAWN = "withdrawn"
    REINSTATED = "reinstated"


MATERIAL = frozenset(ChangeKind) - {ChangeKind.EVIDENCE_CHANGED}
"""Changes that warrant a notice. Evidence-only changes stay in the history."""


class StorylineState(StrEnum):
    OPEN = "open"
    WITHDRAWN = "withdrawn"


class WithdrawalReason(StrEnum):
    NOT_DETECTED = "not_detected"
    OPPOSITE_DIRECTION = "opposite_direction"


def chain_fingerprint(
    previous: str, snapshot_id: Identifier, insight_fingerprint: str | None, state: StorylineState
) -> str:
    payload = json.dumps(
        {
            "previous": previous,
            "snapshot_id": snapshot_id,
            "insight_fingerprint": insight_fingerprint,
            "state": state.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Revision(DomainModel):
    storyline_id: Identifier
    number: int = Field(ge=1)
    snapshot_id: Identifier
    watermark: int = Field(ge=1)
    as_of: MatchInstant
    state: StorylineState
    change_kinds: tuple[ChangeKind, ...]
    candidate_id: Identifier | None
    investigation_id: Identifier | None
    anchor_bin: int | None = Field(ge=0)
    final: FinalInsight | None
    verification: VerificationResult | None
    insight_fingerprint: str | None = Field(pattern=SHA256)
    evidence_integrity: EvidenceIntegrity | None
    audit_findings: tuple[str, ...] = Field(
        description="Stage 5 claim audit and lineage findings on this revision's own snapshot."
    )
    withdrawal_reason: WithdrawalReason | None = None
    previous_chain_fingerprint: str = Field(pattern=SHA256)
    chain_fingerprint: str = Field(pattern=SHA256)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        final = self.final
        if self.state is StorylineState.WITHDRAWN:
            insight = (
                final,
                self.verification,
                self.candidate_id,
                self.investigation_id,
                self.anchor_bin,
                self.insight_fingerprint,
                self.evidence_integrity,
            )
            if any(v is not None for v in insight) or self.audit_findings:
                raise ValueError("a withdrawal carries no insight")
            if self.change_kinds != (ChangeKind.WITHDRAWN,) or self.withdrawal_reason is None:
                raise ValueError("a withdrawal records only its reason")
        else:
            if final is None or self.anchor_bin is None:
                raise ValueError("an open revision holds a verified insight")
            if ChangeKind.WITHDRAWN in self.change_kinds or self.withdrawal_reason is not None:
                raise ValueError("an open revision is not a withdrawal")
            if (self.candidate_id, self.investigation_id) != (
                final.candidate_id,
                final.investigation_id,
            ):
                raise ValueError("revision identifiers differ from the insight")
            if self.insight_fingerprint != fingerprint(final):
                raise ValueError("insight fingerprint does not match the insight")
            if self.evidence_integrity is not final.evidence_integrity:
                raise ValueError("evidence integrity differs from the insight")
        expected = chain_fingerprint(
            self.previous_chain_fingerprint, self.snapshot_id, self.insight_fingerprint, self.state
        )
        if self.chain_fingerprint != expected:
            raise ValueError("chain fingerprint does not match the revision")
        return self


class Storyline(DomainModel):
    storyline_id: Identifier
    match_id: Identifier
    team_id: Identifier
    metric: str
    direction: Direction
    first_anchor: int = Field(ge=0)
    ordinal: int = Field(ge=1, description="Creation order within the match.")
    linked_to: Identifier | None = Field(
        default=None, description="Opposite-direction storyline this one replaced."
    )
    state: StorylineState
    anchor: int = Field(ge=0, description="Anchor bin of the latest insight.")
    verified_through: Identifier | None = Field(
        description="Latest snapshot on which this storyline's insight was freshly evaluated; "
        "None while withdrawn."
    )
    revisions: tuple[Revision, ...] = Field(min_length=1)

    @property
    def current(self) -> Revision:
        return self.revisions[-1]

    @property
    def latest_insight(self) -> Revision:
        return next(r for r in reversed(self.revisions) if r.final is not None)

    @property
    def latest_final(self) -> FinalInsight:
        return next(r.final for r in reversed(self.revisions) if r.final is not None)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        first = self.revisions[0]
        if first.change_kinds != (ChangeKind.CREATED,) or first.state is not StorylineState.OPEN:
            raise ValueError("a storyline starts with the creation of an open revision")
        if self.current.state is not self.state:
            raise ValueError("storyline state differs from its latest revision")
        if (self.state is StorylineState.OPEN) != (self.verified_through is not None):
            raise ValueError("only an open storyline is verified through a snapshot")
        if [r.number for r in self.revisions] != list(range(1, len(self.revisions) + 1)):
            raise ValueError("revision numbers must run from 1 without gaps")
        if any(r.storyline_id != self.storyline_id for r in self.revisions):
            raise ValueError("a revision belongs to another storyline")
        return self


class LifecycleState(DomainModel):
    lifecycle_version: str = LIFECYCLE_VERSION
    schedule: Literal["closed_minute"] = SCHEDULE
    match_id: Identifier
    storylines: tuple[Storyline, ...] = Field(description="In creation order.")
    snapshots: tuple[SnapshotRecord, ...] = Field(description="In schedule order.")

    @classmethod
    def empty(cls, match_id: Identifier) -> "LifecycleState":
        return cls(match_id=match_id, storylines=(), snapshots=())

    @property
    def last_snapshot(self) -> SnapshotRecord | None:
        return self.snapshots[-1] if self.snapshots else None

    def storyline(self, storyline_id: Identifier) -> Storyline:
        return next(s for s in self.storylines if s.storyline_id == storyline_id)
