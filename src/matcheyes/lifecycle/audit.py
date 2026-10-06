"""Independent lifecycle audit: re-derive a lifecycle state from the event log and check it.

The audit trusts nothing in the state. It rebuilds every snapshot from the log, re-runs the
Stage 5 claim audit and lineage of every revision against that revision's own snapshot, re-checks
anchoring, fingerprints, the revision chain and the change kinds (written here again, not called
from reconciliation), and re-evaluates the latest snapshot to confirm that what the state calls
current is what the pipeline establishes now.

Checks, each finding formatted "check: detail":

* snapshot       - snapshot headers re-derive from the log, in schedule order; records list real
                   storylines, one per insight.
* numbering      - revisions numbered from 1, one per snapshot, on evaluated snapshots only.
* chain          - each chain fingerprint recomputes from its predecessor (genesis first).
* fingerprint    - each insight fingerprint is the SHA-256 of the insight's canonical JSON.
* identity       - storyline IDs derive from key and first anchor; insights concern the key;
                   consecutive anchors stay within the detector's suppression distance.
* transitions    - created first, withdrawal only of an open storyline, reinstatement only of a
                   withdrawn one; storyline state and verification follow the last revision.
* change_kinds   - recorded kinds equal the kinds recomputed from consecutive insights.
* linkage        - opposite-direction withdrawals and linked successors correspond.
* anchoring      - every event an insight cites is in its own snapshot's prefix.
* revision_audit - the Stage 5 audit of each revision on its own snapshot equals the record.
* integrity      - recorded evidence integrity equals the insight's and the verifier's.
* reproduction   - (when `reproduce`) every evaluated snapshot re-evaluates to the same number
                   of insights, and every recorded insight is one of them; an invalid snapshot is
                   invalid again. A failed snapshot is not compared: it established nothing.
* current        - current revisions are exactly the fresh evaluation of the latest snapshot:
                   the same insights, with the same audit findings.

Reproduction costs one pipeline run per snapshot - a full replay - so callers that audit often
pass an evaluator that caches by snapshot ID (snapshot IDs are content addresses).
"""

import json
import re
from collections.abc import Sequence

from matcheyes.agents.contracts import FinalInsight
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.moments import AnalysisConfig
from matcheyes.domain.entities import MatchInfo
from matcheyes.domain.events import MatchEvent
from matcheyes.lifecycle.contracts import (
    GENESIS,
    ChangeKind,
    LifecycleState,
    Revision,
    SnapshotHeader,
    SnapshotOutcome,
    Storyline,
    StorylineState,
    WithdrawalReason,
    chain_fingerprint,
)
from matcheyes.lifecycle.evaluate import (
    Evaluator,
    SnapshotEvaluation,
    audit_findings,
    evaluate_snapshot,
)
from matcheyes.lifecycle.snapshot import Snapshot, build_snapshot, prefix_digest, snapshot_schedule
from matcheyes.orchestration.investigation import InvestigationRecord
from matcheyes.personalization.contracts import fingerprint

LIFECYCLE_CHECKS = (
    "snapshot",
    "numbering",
    "chain",
    "fingerprint",
    "identity",
    "transitions",
    "change_kinds",
    "linkage",
    "anchoring",
    "revision_audit",
    "integrity",
    "reproduction",
    "current",
)
SUPPRESSION_BINS = AnalysisConfig().detection.suppression_bins
"""The pipeline analyses every snapshot with the default configuration."""


def _evidence(final: FinalInsight) -> tuple[object, ...]:
    items = sorted(
        (e.tool.value, json.dumps(e.facts, sort_keys=True), e.event_ids, e.team_id or "")
        for e in final.evidence
    )
    return (tuple(items), final.event_ids, final.quarantined)


def _expected_kinds(old: FinalInsight, new: FinalInsight, reinstated: bool) -> set[ChangeKind]:
    pairs = (
        (ChangeKind.REINSTATED, reinstated),
        (ChangeKind.REANCHORED, old.candidate_id != new.candidate_id),
        (ChangeKind.VERDICT_CHANGED, old.verdict != new.verdict),
        (ChangeKind.STRENGTH_CHANGED, old.strength != new.strength),
        (
            ChangeKind.EXPLANATION_CHANGED,
            old.leading != new.leading or old.alternatives != new.alternatives,
        ),
        (ChangeKind.INTEGRITY_CHANGED, old.evidence_integrity != new.evidence_integrity),
        (ChangeKind.EVIDENCE_CHANGED, _evidence(old) != _evidence(new)),
    )
    return {kind for kind, changed in pairs if changed}


class LifecycleAuditor:
    def __init__(
        self,
        info: MatchInfo,
        events: Sequence[MatchEvent],
        evaluator: Evaluator = evaluate_snapshot,
        reproduce: bool = True,
        workspaces: dict[str, MatchWorkspace] | None = None,
    ) -> None:
        """`events`: the log's contiguous prefix in sequence order. `workspaces` may be shared
        between auditors of the same log (they are pure functions of the snapshot)."""
        self.info = info
        self.events = tuple(events)
        self.evaluator = evaluator
        self.reproduce = reproduce
        self.headers: list[SnapshotHeader] = []
        self.snapshots: dict[str, Snapshot] = {}
        for entry in snapshot_schedule(self.events):
            digest = prefix_digest(self.events[: entry.watermark])
            snap = build_snapshot(info, self.events, entry, digest)
            self.headers.append(snap.header)
            self.snapshots[snap.header.snapshot_id] = snap
        self._workspaces = {} if workspaces is None else workspaces
        self.findings: list[str] = []

    def add(self, check: str, detail: str) -> None:
        self.findings.append(f"{check}: {detail}")

    @property
    def workspaces(self) -> dict[str, MatchWorkspace]:
        return self._workspaces

    def workspace(self, snapshot_id: str) -> MatchWorkspace:
        if snapshot_id not in self._workspaces:
            self._workspaces[snapshot_id] = MatchWorkspace.build(self.snapshots[snapshot_id].match)
        return self._workspaces[snapshot_id]

    def audit(self, state: LifecycleState) -> list[str]:
        self.findings = []
        if state.match_id != self.info.match_id:
            self.add("snapshot", "state belongs to another match")
            return self.findings
        self._snapshots(state)
        evaluated = {
            r.header.snapshot_id: r.header
            for r in state.snapshots
            if r.outcome is SnapshotOutcome.EVALUATED
        }
        ids = [s.storyline_id for s in state.storylines]
        if len(ids) != len(set(ids)):
            self.add("identity", "duplicate storyline IDs")
        if [s.ordinal for s in state.storylines] != list(range(1, len(ids) + 1)):
            self.add("identity", "storyline ordinals are not the creation order")
        for s in state.storylines:
            self._storyline(state, s, evaluated)
        if self.reproduce:
            self._reproduction(state)
        self._current(state)
        return self.findings

    def _fresh(self, snapshot_id: str) -> SnapshotEvaluation:
        return self.evaluator(self.snapshots[snapshot_id])

    def _reproduction(self, state: LifecycleState) -> None:
        recorded: dict[str, set[str | None]] = {}
        for s in state.storylines:
            for r in s.revisions:
                if r.final is not None:
                    recorded.setdefault(r.snapshot_id, set()).add(r.insight_fingerprint)
        for record in state.snapshots:
            sid = record.header.snapshot_id
            if sid not in self.snapshots or record.outcome is SnapshotOutcome.FAILED:
                continue
            fresh = self._fresh(sid)
            if record.outcome is SnapshotOutcome.INVALID:
                if fresh.outcome is not SnapshotOutcome.INVALID:
                    self.add("reproduction", f"{sid}: recorded invalid, but the prefix is valid")
                continue
            if fresh.outcome is not SnapshotOutcome.EVALUATED:
                self.add("reproduction", f"{sid}: recorded evaluated, now {fresh.outcome.value}")
                continue
            if len(fresh.insights) != record.insights:
                self.add(
                    "reproduction", f"{sid}: {len(fresh.insights)} insights, not {record.insights}"
                )
            expected = {fingerprint(i.final) for i in fresh.insights}
            if not recorded.get(sid, set()) <= expected:
                self.add(
                    "reproduction", f"{sid}: a recorded insight is not what the pipeline gives"
                )

    def _snapshots(self, state: LifecycleState) -> None:
        recorded = [r.header for r in state.snapshots]
        if recorded != self.headers[: len(recorded)]:
            self.add("snapshot", "snapshot headers do not re-derive from the log")
        known = {s.storyline_id for s in state.storylines}
        for record in state.snapshots:
            missing = set(record.storyline_ids) - known
            if missing:
                self.add("snapshot", f"{record.header.snapshot_id} lists unknown {sorted(missing)}")
            if record.outcome is SnapshotOutcome.EVALUATED and record.insights != len(
                record.storyline_ids
            ):
                self.add("snapshot", f"{record.header.snapshot_id}: insights without a storyline")

    def _storyline(
        self, state: LifecycleState, s: Storyline, evaluated: dict[str, SnapshotHeader]
    ) -> None:
        sid = s.storyline_id
        base = f"sl-{s.match_id}-{s.team_id}-{s.metric}-{s.direction}-{s.first_anchor}"
        if s.match_id != state.match_id or not re.fullmatch(re.escape(base) + r"(-\d+)?", sid):
            self.add("identity", f"{sid} does not derive from its key and first anchor")
        previous_chain = GENESIS
        previous: Revision | None = None
        for index, r in enumerate(s.revisions, start=1):
            if r.number != index or r.storyline_id != sid:
                self.add("numbering", f"{sid}: revision {index} is numbered {r.number}")
            header = evaluated.get(r.snapshot_id)
            if header is None or (header.watermark, header.as_of) != (r.watermark, r.as_of):
                self.add("numbering", f"{sid} r{r.number}: not on an evaluated snapshot")
            if previous is not None and r.watermark <= previous.watermark:
                self.add("numbering", f"{sid} r{r.number}: not after r{previous.number}")
            if r.previous_chain_fingerprint != previous_chain or r.chain_fingerprint != (
                chain_fingerprint(previous_chain, r.snapshot_id, r.insight_fingerprint, r.state)
            ):
                self.add("chain", f"{sid} r{r.number}: chain fingerprint does not recompute")
            self._transition(s, r, previous)
            if r.final is not None and header is not None:
                self._insight(s, r, previous)
            previous_chain, previous = r.chain_fingerprint, r
        if s.state is not s.revisions[-1].state:
            self.add("transitions", f"{sid}: state differs from its last revision")
        if s.state is StorylineState.WITHDRAWN and s.verified_through is not None:
            self.add("transitions", f"{sid}: a withdrawn storyline claims current verification")
        self._linkage(state, s)

    def _transition(self, s: Storyline, r: Revision, previous: Revision | None) -> None:
        sid, kinds = s.storyline_id, set(r.change_kinds)
        if previous is None:
            if r.state is not StorylineState.OPEN or kinds != {ChangeKind.CREATED}:
                self.add("transitions", f"{sid}: does not start with a created open revision")
            if r.anchor_bin != s.first_anchor:
                self.add("identity", f"{sid}: first anchor differs from its first revision")
            return
        if ChangeKind.CREATED in kinds:
            self.add("transitions", f"{sid} r{r.number}: created twice")
        if r.state is StorylineState.WITHDRAWN:
            if previous.state is not StorylineState.OPEN or r.final is not None:
                self.add("transitions", f"{sid} r{r.number}: withdrawal of a non-open storyline")
        elif (ChangeKind.REINSTATED in kinds) != (previous.state is StorylineState.WITHDRAWN):
            self.add("transitions", f"{sid} r{r.number}: reinstatement does not follow withdrawal")

    def _insight(self, s: Storyline, r: Revision, previous: Revision | None) -> None:
        final, sid = r.final, s.storyline_id
        if final is None:
            return
        if r.insight_fingerprint != fingerprint(final):
            self.add("fingerprint", f"{sid} r{r.number}: insight fingerprint does not recompute")
        verified = r.verification.evidence_integrity if r.verification else final.evidence_integrity
        if r.evidence_integrity is not final.evidence_integrity or verified is not (
            final.evidence_integrity
        ):
            self.add("integrity", f"{sid} r{r.number}: integrity differs from the verified record")
        snap = self.snapshots.get(r.snapshot_id)
        if snap is None:
            self.add("snapshot", f"{sid} r{r.number}: snapshot does not re-derive from the log")
            return
        known = {e.event_id for e in snap.match.events}
        cited = set(final.event_ids) | {i for e in final.evidence for i in e.event_ids}
        if not cited <= known:
            self.add("anchoring", f"{sid} r{r.number}: cites events after its snapshot")
            return
        ws = self.workspace(r.snapshot_id)
        candidate = ws.candidates.get(final.candidate_id)
        if candidate is None or (
            candidate.team_id,
            candidate.metric,
            candidate.direction,
            candidate.bin_index,
        ) != (s.team_id, s.metric, s.direction, r.anchor_bin):
            self.add("identity", f"{sid} r{r.number}: insight is not about this storyline")
        record = InvestigationRecord(final=final, verification=r.verification, trace=())
        if audit_findings(ws, record) != r.audit_findings:
            self.add("revision_audit", f"{sid} r{r.number}: audit differs on its own snapshot")
        earlier = [
            (p.final, p.anchor_bin) for p in s.revisions[: r.number - 1] if p.final is not None
        ]
        if not earlier:
            return
        before_final, before_anchor = earlier[-1]
        if (
            before_anchor is not None
            and r.anchor_bin is not None
            and abs(r.anchor_bin - before_anchor) > SUPPRESSION_BINS
        ):
            self.add("identity", f"{sid} r{r.number}: anchor moved beyond suppression")
        reinstated = previous is not None and previous.state is StorylineState.WITHDRAWN
        if set(r.change_kinds) != _expected_kinds(before_final, final, reinstated):
            self.add("change_kinds", f"{sid} r{r.number}: kinds do not match the insights")

    def _linkage(self, state: LifecycleState, s: Storyline) -> None:
        by_id = {x.storyline_id: x for x in state.storylines}
        if s.linked_to is not None:
            target = by_id.get(s.linked_to)
            created = s.revisions[0].snapshot_id
            valid = target is not None and (
                (target.team_id, target.metric) == (s.team_id, s.metric)
                and target.direction != s.direction
                and any(
                    r.withdrawal_reason is WithdrawalReason.OPPOSITE_DIRECTION
                    and r.snapshot_id == created
                    for r in target.revisions
                )
            )
            if not valid:
                self.add("linkage", f"{s.storyline_id}: link does not match a replaced storyline")
        for r in s.revisions:
            if r.withdrawal_reason is WithdrawalReason.OPPOSITE_DIRECTION:
                successors = [
                    x
                    for x in state.storylines
                    if x.linked_to == s.storyline_id and x.revisions[0].snapshot_id == r.snapshot_id
                ]
                if len(successors) != 1:
                    self.add("linkage", f"{s.storyline_id} r{r.number}: no linked successor")

    def _current(self, state: LifecycleState) -> None:
        last = state.last_snapshot
        if last is None:
            return
        sid = last.header.snapshot_id
        claimed = {s.storyline_id: s for s in state.storylines if s.verified_through == sid}
        if last.outcome is not SnapshotOutcome.EVALUATED:
            if claimed:
                self.add("current", "storylines claim verification on a failed snapshot")
            return
        stale = [
            s.storyline_id
            for s in state.storylines
            if s.state is StorylineState.OPEN and s.verified_through != sid
        ]
        if stale:
            self.add("current", f"open storylines not verified on the latest snapshot: {stale}")
        if sid not in self.snapshots:
            return
        fresh = {fingerprint(i.final): i.audit_findings for i in self._fresh(sid).insights}
        shown = {s.current.insight_fingerprint: s.current.audit_findings for s in claimed.values()}
        if set(shown) != set(fresh):
            self.add("current", "current revisions differ from a fresh evaluation")
        elif shown != fresh:
            self.add("current", "current audit findings differ from a fresh audit")


def audit_lifecycle(
    info: MatchInfo,
    events: Sequence[MatchEvent],
    state: LifecycleState,
    evaluator: Evaluator = evaluate_snapshot,
    reproduce: bool = True,
) -> list[str]:
    return LifecycleAuditor(info, events, evaluator, reproduce).audit(state)
