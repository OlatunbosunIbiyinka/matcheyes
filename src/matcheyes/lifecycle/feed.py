"""Lifecycle feed: current truth, history and change notices, then audience views.

The feed separates:

* current - the latest revision of each open storyline, freshly established on the latest
  snapshot;
* withdrawn - storylines the latest evidence no longer supports, kept visible with their
  withdrawal, never silently dropped;
* notices - material changes established on the latest snapshot (evidence-only changes are
  history, not notices).

Status says why there may be no current insight: AWAITING_SNAPSHOT (no minute has closed),
NO_CANDIDATE (evaluated, nothing open), UNAVAILABLE (the latest snapshot failed or was invalid -
earlier revisions are history and are not shown as current). Data availability (DATA_INCOMPLETE)
comes from the event log and is reported alongside.

Stage 6 personalizes only the current revisions, against the latest snapshot's workspace, so a
view can never present an older revision as current; a view whose source is not a current
revision is stale.
"""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from matcheyes.agents.contracts import FinalInsight
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.time import MatchInstant
from matcheyes.ingestion.log import DataStatus, LogStatus
from matcheyes.lifecycle.contracts import (
    MATERIAL,
    ChangeKind,
    LifecycleState,
    Revision,
    SnapshotOutcome,
    StorylineState,
    WithdrawalReason,
)
from matcheyes.orchestration.investigation import InvestigationRecord
from matcheyes.personalization.contracts import (
    AudienceFeed,
    PersonalizationProfile,
    PersonalizedInsight,
    factual_claim,
    fingerprint,
)
from matcheyes.personalization.feed import build_feed


class CurrentStatus(StrEnum):
    CURRENT = "current"
    NO_CANDIDATE = "no_candidate"
    UNAVAILABLE = "unavailable"
    AWAITING_SNAPSHOT = "awaiting_snapshot"


class Notice(DomainModel):
    storyline_id: Identifier
    revision: int = Field(ge=1)
    change_kinds: tuple[ChangeKind, ...] = Field(min_length=1)
    text: str = Field(min_length=1)


class LifecycleFeed(DomainModel):
    match_id: Identifier
    data_status: DataStatus
    watermark: int = Field(ge=0)
    buffered: int = Field(ge=0, description="Events received beyond a gap, not yet usable.")
    status: CurrentStatus
    snapshot_id: Identifier | None
    as_of: MatchInstant | None
    unavailable_reason: str | None = None
    current: tuple[Revision, ...]
    unavailable: tuple[Identifier, ...] = Field(
        description="Open storylines with no current truth because the latest snapshot failed."
    )
    withdrawn: tuple[Revision, ...]
    notices: tuple[Notice, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.status is CurrentStatus.CURRENT) != bool(self.current):
            raise ValueError("current insights exist exactly when the status is current")
        if self.unavailable and self.status is not CurrentStatus.UNAVAILABLE:
            raise ValueError("only an unavailable feed lists unavailable storylines")
        if any(r.state is not StorylineState.OPEN for r in self.current):
            raise ValueError("a current insight must be an open revision")
        if any(r.state is not StorylineState.WITHDRAWN for r in self.withdrawn):
            raise ValueError("a withdrawn entry must be a withdrawal")
        return self


def _through(as_of: MatchInstant) -> str:
    end = MatchInstant(period=as_of.period, clock_ms=max(0, as_of.clock_ms - 60_000))
    return f"as of {end.display_minute}"


def explanation_changes(old: FinalInsight, new: FinalInsight) -> list[str]:
    """What `explanation_changed` recorded, field by field: the leading explanation only when it
    differs, and each alternative whose assessment differs. Nothing unchanged is named."""
    parts = []
    if old.leading != new.leading:
        before = old.leading.value if old.leading else "none"
        after = new.leading.value if new.leading else "none"
        parts.append(f"leading explanation {before} -> {after}")
    if old.alternatives != new.alternatives:
        before_alt, after_alt = dict(old.alternatives), dict(new.alternatives)
        kinds = [k for k, _ in old.alternatives]
        kinds += [k for k, _ in new.alternatives if k not in before_alt]
        changed = []
        for kind in kinds:
            if kind not in after_alt:
                changed.append(f"{kind.value} no longer listed")
            elif kind not in before_alt:
                changed.append(f"{kind.value} now listed ({after_alt[kind].value})")
            elif before_alt[kind] is not after_alt[kind]:
                changed.append(f"{kind.value} {before_alt[kind].value} -> {after_alt[kind].value}")
        parts.append(
            f"alternative explanations: {', '.join(changed)}"
            if changed
            else "alternative explanations reordered"
        )
    return parts


def notice_text(state: LifecycleState, revision: Revision) -> str:
    """A factual, templated description of a lifecycle change. It names what changed in the
    verified record; it never explains why."""
    when = _through(revision.as_of)
    storyline = state.storyline(revision.storyline_id)
    if revision.state is StorylineState.WITHDRAWN:
        if revision.withdrawal_reason is WithdrawalReason.OPPOSITE_DIRECTION:
            successor = next(
                s.storyline_id for s in state.storylines if s.linked_to == storyline.storyline_id
            )
            return f"Withdrawn {when}: replaced by an opposite-direction change ({successor})."
        return f"Withdrawn {when}: the change is no longer detected in the current data."
    final = revision.final
    if final is None:
        raise ValueError("an open revision holds an insight")
    if ChangeKind.CREATED in revision.change_kinds:
        return f"New {when}: {factual_claim(final).text}"
    old = next(
        r.final for r in reversed(storyline.revisions[: revision.number - 1]) if r.final is not None
    )
    parts = []
    if ChangeKind.REINSTATED in revision.change_kinds:
        parts.append("detected again")
    if ChangeKind.REANCHORED in revision.change_kinds:
        parts.append(f"onset now {final.at.display_minute} (was {old.at.display_minute})")
    if ChangeKind.VERDICT_CHANGED in revision.change_kinds:
        parts.append(f"verdict {old.verdict.value} -> {final.verdict.value}")
    if ChangeKind.STRENGTH_CHANGED in revision.change_kinds:
        parts.append(f"strength {old.strength.value} -> {final.strength.value}")
    if ChangeKind.EXPLANATION_CHANGED in revision.change_kinds:
        parts += explanation_changes(old, final)
    if ChangeKind.INTEGRITY_CHANGED in revision.change_kinds:
        parts.append(
            f"evidence integrity {old.evidence_integrity.value} -> {final.evidence_integrity.value}"
        )
    return f"Revised {when}: {'; '.join(parts)}."


def current_revisions(state: LifecycleState) -> tuple[Revision, ...]:
    """Revisions that are current truth: open storylines verified on the latest snapshot, and
    only when that snapshot was evaluated."""
    last = state.last_snapshot
    if last is None or last.outcome is not SnapshotOutcome.EVALUATED:
        return ()
    sid = last.header.snapshot_id
    return tuple(
        s.current
        for s in state.storylines
        if s.state is StorylineState.OPEN and s.verified_through == sid
    )


def lifecycle_feed(state: LifecycleState, log: LogStatus) -> LifecycleFeed:
    if log.match_id != state.match_id:
        raise ValueError("log status belongs to another match")
    last = state.last_snapshot
    open_ids = tuple(s.storyline_id for s in state.storylines if s.state is StorylineState.OPEN)
    current = current_revisions(state)
    if last is None:
        status = CurrentStatus.AWAITING_SNAPSHOT
    elif last.outcome is not SnapshotOutcome.EVALUATED:
        status = CurrentStatus.UNAVAILABLE
    else:
        status = CurrentStatus.CURRENT if current else CurrentStatus.NO_CANDIDATE
    notices = []
    if last is not None:
        for s in state.storylines:
            for r in s.revisions:
                if r.snapshot_id == last.header.snapshot_id and MATERIAL & set(r.change_kinds):
                    notices.append(
                        Notice(
                            storyline_id=s.storyline_id,
                            revision=r.number,
                            change_kinds=r.change_kinds,
                            text=notice_text(state, r),
                        )
                    )
    unavailable = status is CurrentStatus.UNAVAILABLE
    return LifecycleFeed(
        match_id=state.match_id,
        data_status=log.data_status,
        watermark=log.watermark,
        buffered=log.buffered,
        status=status,
        snapshot_id=last.header.snapshot_id if last else None,
        as_of=last.header.as_of if last else None,
        unavailable_reason=(
            f"{last.outcome.value}: {last.failure}" if unavailable and last is not None else None
        ),
        current=current,
        unavailable=open_ids if unavailable else (),
        withdrawn=tuple(s.current for s in state.storylines if s.state is StorylineState.WITHDRAWN),
        notices=tuple(notices),
    )


def audience_feed(
    state: LifecycleState, profile: PersonalizationProfile, ws: MatchWorkspace
) -> AudienceFeed:
    """Stage 6 feed of the current revisions. `ws` must be the latest snapshot's workspace."""
    last = state.last_snapshot
    if last is not None and len(ws.events) != last.header.watermark:
        raise ValueError("the workspace is not the latest snapshot's")
    records = [
        InvestigationRecord(final=r.final, verification=r.verification, trace=())
        for r in current_revisions(state)
        if r.final is not None
    ]
    return build_feed(records, profile, ws)


def view_is_current(view: PersonalizedInsight, state: LifecycleState) -> bool:
    """False for a stale view: its source is not a current revision (or was altered)."""
    if view.source_fingerprint != fingerprint(view.source):
        return False
    return view.source_fingerprint in {r.insight_fingerprint for r in current_revisions(state)}
