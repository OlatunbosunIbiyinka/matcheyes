"""Storyline reconciliation: fold one snapshot's evaluation into the lifecycle state. Pure.

For an evaluated snapshot:

1. each insight continues a storyline (`identity.match_storylines`): a new revision is appended
   when the verified insight or its audit differs from the storyline's latest, and a withdrawn
   storyline is reinstated;
2. each remaining insight starts a storyline; if an open storyline with the opposite direction
   sits at the same place, that storyline is withdrawn and the new one is linked to it;
3. every open storyline with no insight on this snapshot is withdrawn.

Change kinds compare verified truth fields, never prose. An invalid or failed snapshot is recorded
and changes no storyline: its earlier revisions stay as history, and the feed reports the
current truth as unavailable instead of falling back to them.
"""

import json
from collections.abc import Sequence

from matcheyes.agents.contracts import FinalInsight
from matcheyes.domain.entities import Identifier
from matcheyes.lifecycle.contracts import (
    GENESIS,
    ChangeKind,
    LifecycleState,
    Revision,
    SnapshotHeader,
    SnapshotOutcome,
    SnapshotRecord,
    Storyline,
    StorylineState,
    WithdrawalReason,
    chain_fingerprint,
)
from matcheyes.lifecycle.evaluate import InsightOutcome, SnapshotEvaluation
from matcheyes.lifecycle.identity import match_storylines, opposite_storyline, storyline_id
from matcheyes.personalization.contracts import fingerprint


def evidence_signature(final: FinalInsight) -> tuple[object, ...]:
    """What the insight rests on, independent of positional evidence IDs and of wording."""
    items = sorted(
        (e.tool.value, json.dumps(e.facts, sort_keys=True), e.event_ids, e.team_id or "")
        for e in final.evidence
    )
    return (tuple(items), final.event_ids, final.quarantined)


def change_kinds(old: FinalInsight, new: FinalInsight) -> tuple[ChangeKind, ...]:
    kinds = []
    if old.candidate_id != new.candidate_id:
        kinds.append(ChangeKind.REANCHORED)
    if old.verdict is not new.verdict:
        kinds.append(ChangeKind.VERDICT_CHANGED)
    if old.strength is not new.strength:
        kinds.append(ChangeKind.STRENGTH_CHANGED)
    if (old.leading, old.alternatives) != (new.leading, new.alternatives):
        kinds.append(ChangeKind.EXPLANATION_CHANGED)
    if old.evidence_integrity is not new.evidence_integrity:
        kinds.append(ChangeKind.INTEGRITY_CHANGED)
    if evidence_signature(old) != evidence_signature(new):
        kinds.append(ChangeKind.EVIDENCE_CHANGED)
    return tuple(kinds)


def _revision(
    sid: Identifier,
    number: int,
    header: SnapshotHeader,
    previous: str,
    kinds: Sequence[ChangeKind],
    insight: InsightOutcome | None = None,
    reason: WithdrawalReason | None = None,
) -> Revision:
    state = StorylineState.OPEN if insight is not None else StorylineState.WITHDRAWN
    final = insight.final if insight is not None else None
    fp = fingerprint(final) if final is not None else None
    return Revision(
        storyline_id=sid,
        number=number,
        snapshot_id=header.snapshot_id,
        watermark=header.watermark,
        as_of=header.as_of,
        state=state,
        change_kinds=tuple(kinds),
        candidate_id=final.candidate_id if final else None,
        investigation_id=final.investigation_id if final else None,
        anchor_bin=insight.anchor_bin if insight else None,
        final=final,
        verification=insight.verification if insight else None,
        insight_fingerprint=fp,
        evidence_integrity=final.evidence_integrity if final else None,
        audit_findings=insight.audit_findings if insight else (),
        withdrawal_reason=reason,
        previous_chain_fingerprint=previous,
        chain_fingerprint=chain_fingerprint(previous, header.snapshot_id, fp, state),
    )


class _Builder:
    def __init__(self, state: LifecycleState, evaluation: SnapshotEvaluation) -> None:
        self.state = state
        self.header = evaluation.header
        self.storylines: dict[Identifier, Storyline] = {s.storyline_id: s for s in state.storylines}
        self.touched: set[Identifier] = set()
        self.established: list[Identifier] = []

    def _append(self, s: Storyline, revision: Revision | None, **update: object) -> None:
        revisions = s.revisions if revision is None else (*s.revisions, revision)
        state = revisions[-1].state
        self.storylines[s.storyline_id] = Storyline.model_validate(
            {**dict(s), "revisions": revisions, "state": state, **update}
        )
        self.touched.add(s.storyline_id)

    def revise(self, sid: Identifier, insight: InsightOutcome) -> None:
        s = self.storylines[sid]
        previous = s.latest_insight
        kinds = list(change_kinds(s.latest_final, insight.final))
        reinstated = s.state is StorylineState.WITHDRAWN
        if reinstated:
            kinds.insert(0, ChangeKind.REINSTATED)
        unchanged = (
            fingerprint(insight.final) == previous.insight_fingerprint
            and insight.audit_findings == previous.audit_findings
        )
        revision = None
        if reinstated or not unchanged:
            revision = _revision(
                sid,
                len(s.revisions) + 1,
                self.header,
                s.current.chain_fingerprint,
                kinds,
                insight,
            )
        self._append(
            s, revision, anchor=insight.anchor_bin, verified_through=self.header.snapshot_id
        )
        self.established.append(sid)

    def withdraw(self, sid: Identifier, reason: WithdrawalReason) -> None:
        s = self.storylines[sid]
        revision = _revision(
            sid,
            len(s.revisions) + 1,
            self.header,
            s.current.chain_fingerprint,
            (ChangeKind.WITHDRAWN,),
            reason=reason,
        )
        self._append(s, revision, verified_through=None)

    def create(self, insight: InsightOutcome, suppression_bins: int) -> None:
        current = list(self.storylines.values())
        replaced = opposite_storyline(current, insight, suppression_bins, self.touched)
        if replaced is not None:
            self.withdraw(replaced.storyline_id, WithdrawalReason.OPPOSITE_DIRECTION)
        sid = storyline_id(
            self.header.match_id,
            insight.final.team_id,
            insight.metric,
            insight.direction,
            insight.anchor_bin,
            self.storylines,
        )
        revision = _revision(sid, 1, self.header, GENESIS, (ChangeKind.CREATED,), insight)
        self.storylines[sid] = Storyline(
            storyline_id=sid,
            match_id=self.header.match_id,
            team_id=insight.final.team_id,
            metric=insight.metric,
            direction=insight.direction,
            first_anchor=insight.anchor_bin,
            ordinal=len(current) + 1,
            linked_to=replaced.storyline_id if replaced is not None else None,
            state=StorylineState.OPEN,
            anchor=insight.anchor_bin,
            verified_through=self.header.snapshot_id,
            revisions=(revision,),
        )
        self.touched.add(sid)
        self.established.append(sid)


def _record(evaluation: SnapshotEvaluation, established: Sequence[Identifier]) -> SnapshotRecord:
    return SnapshotRecord(
        header=evaluation.header,
        outcome=evaluation.outcome,
        failure=evaluation.failure,
        insights=len(evaluation.insights),
        storyline_ids=tuple(established),
    )


def reconcile(state: LifecycleState, evaluation: SnapshotEvaluation) -> LifecycleState:
    header = evaluation.header
    if header.match_id != state.match_id:
        raise ValueError("snapshot belongs to another match")
    last = state.last_snapshot
    if last is not None and header.watermark <= last.header.watermark:
        raise ValueError("snapshots must advance the watermark")
    if evaluation.outcome is not SnapshotOutcome.EVALUATED:
        return LifecycleState(
            match_id=state.match_id,
            storylines=state.storylines,
            snapshots=(*state.snapshots, _record(evaluation, ())),
        )
    if evaluation.suppression_bins is None:
        raise ValueError("an evaluated snapshot records its suppression distance")
    builder = _Builder(state, evaluation)
    insights = evaluation.insights
    assigned = match_storylines(state.storylines, insights, evaluation.suppression_bins)
    for index, insight in enumerate(insights):
        if index in assigned:
            builder.revise(assigned[index], insight)
    for index, insight in enumerate(insights):
        if index not in assigned:
            builder.create(insight, evaluation.suppression_bins)
    for s in state.storylines:
        if s.state is StorylineState.OPEN and s.storyline_id not in builder.touched:
            builder.withdraw(s.storyline_id, WithdrawalReason.NOT_DETECTED)
    return LifecycleState(
        match_id=state.match_id,
        storylines=tuple(builder.storylines.values()),
        snapshots=(*state.snapshots, _record(evaluation, builder.established)),
    )
