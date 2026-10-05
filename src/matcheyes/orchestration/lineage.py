"""Evidence lineage: an explicit graph from every final claim down to source event IDs.

    final claim -> verification verdict -> assertion checks -> evidence items
      -> tool request (replayed) -> Stage 3 candidate -> Stage 2 shift evidence
      -> metric series -> observed events

Each layer is linked by identifiers that already exist in the pipeline; this module adds no new
state, it re-derives the links and reports any that do not resolve. A clean investigation has
no broken links (docs/evidence-audit.md).
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from matcheyes.agents.contracts import (
    ClaimType,
    EvidenceItem,
    EvidenceRequest,
    HypothesisKind,
    VerifiedClaim,
)
from matcheyes.agents.tools import MatchWorkspace, ToolBox, ToolError
from matcheyes.analytics.evidence import MetricShiftEvidence
from matcheyes.orchestration.investigation import InvestigationRecord

Layer = Literal["claim", "verdict", "evidence", "candidate", "stage2", "series", "event", "request"]


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str


@dataclass(frozen=True)
class BrokenLink:
    node: str
    reason: str


@dataclass
class LineageReport:
    nodes: dict[str, Layer] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    broken: list[BrokenLink] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.broken

    def add(self, node: str, layer: Layer) -> str:
        self.nodes.setdefault(node, layer)
        return node

    def link(self, source: str, target: str, relation: str) -> None:
        self.edges.append(Edge(source, target, relation))

    def fail(self, node: str, reason: str) -> None:
        self.broken.append(BrokenLink(node, reason))

    def reaches_events(self, node: str) -> bool:
        """Whether a path from `node` ends at an observed event."""
        out: dict[str, list[str]] = {}
        for e in self.edges:
            out.setdefault(e.source, []).append(e.target)
        seen, stack = set(), [node]
        while stack:
            current = stack.pop()
            if self.nodes.get(current) == "event":
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(out.get(current, ()))
        return False


def _events(report: LineageReport, ws: MatchWorkspace, source: str, ids: tuple[str, ...]) -> None:
    for eid in ids:
        if eid not in ws.events:
            report.fail(source, f"event {eid} is not in the match")
            continue
        report.link(source, report.add(f"event:{eid}", "event"), "observed")


def _candidate(report: LineageReport, ws: MatchWorkspace, candidate_id: str) -> str:
    node = report.add(f"candidate:{candidate_id}", "candidate")
    c = ws.candidates.get(candidate_id)
    if c is None:
        report.fail(node, "candidate is not in Stage 3")
        return node
    _events(report, ws, node, c.event_ids)
    shift = ws.stage2_evidence.get(c.shift_evidence_id)
    s2 = report.add(f"stage2:{c.shift_evidence_id}", "stage2")
    report.link(node, s2, "derived_from")
    if not isinstance(shift, MetricShiftEvidence):
        report.fail(s2, "Stage 2 shift evidence is missing")
        return node
    if (shift.metric, shift.team_id) != (c.metric, c.team_id):
        report.fail(s2, "Stage 2 evidence describes another metric or team")
    series = ws.series.get((shift.metric, shift.team_id))
    sn = report.add(f"series:{shift.metric}/{shift.team_id}", "series")
    report.link(s2, sn, "computed_from")
    if series is None:
        report.fail(sn, "metric series is missing")
        return node
    counted = set(series.events_between(0, len(series.event_ids)))
    stray = [e for e in shift.event_ids if e not in counted]
    if stray:
        report.fail(s2, f"{len(stray)} event(s) not counted by the series")
    _events(report, ws, s2, shift.event_ids)
    _events(report, ws, sn, tuple(sorted(counted & set(shift.event_ids))))
    return node


def _evidence(
    report: LineageReport, ws: MatchWorkspace, toolbox: ToolBox, item: EvidenceItem
) -> str:
    node = report.add(f"evidence:{item.evidence_id}", "evidence")
    request = report.add(f"request:{item.request_id}", "request")
    report.link(node, request, "produced_by")
    try:
        replay = toolbox.run(
            EvidenceRequest(
                request_id=item.request_id,
                tool=item.tool,
                arguments=item.arguments,
                hypothesis=HypothesisKind.NATURAL_VARIATION,
            ),
            item.evidence_id,
        )
    except (ToolError, ValidationError):
        report.fail(request, "request does not replay on this match")
    else:
        if replay != item:
            report.fail(request, "replay differs from the recorded result")
    candidate_id = item.arguments.get("candidate_id")
    if isinstance(candidate_id, str):
        report.link(request, _candidate(report, ws, candidate_id), "about")
    _events(report, ws, node, item.event_ids)
    return node


def _claim(
    report: LineageReport,
    index: int,
    claim: VerifiedClaim,
    pool: set[str],
    candidate_node: str,
) -> str:
    node = report.add(f"claim:{index}", "claim")
    for eid in claim.supporting_evidence_ids + claim.contradicting_evidence_ids:
        if eid not in pool:
            report.fail(node, f"cites {eid}, which is not in the verified evidence pool")
            continue
        report.link(node, f"evidence:{eid}", "cites")
    if claim.claim_type is ClaimType.FACTUAL:
        report.link(node, candidate_node, "states")
    return node


def audit_lineage(ws: MatchWorkspace, record: InvestigationRecord) -> LineageReport:
    """Re-derive every link of one investigation and report those that do not resolve."""
    report = LineageReport()
    final, verification = record.final, record.verification
    toolbox = ToolBox(ws)
    candidate_node = _candidate(report, ws, final.candidate_id)
    pool = {e.evidence_id for e in final.evidence}
    for item in final.evidence:
        _evidence(report, ws, toolbox, item)
    for index, claim in enumerate(final.claims):
        node = _claim(report, index, claim, pool, candidate_node)
        if claim.hypothesis is None:
            continue
        if verification is None:
            report.fail(node, "explanation without a verification result")
            continue
        verdict = next((v for v in verification.hypotheses if v.kind is claim.hypothesis), None)
        vnode = report.add(f"verdict:{claim.hypothesis.value}", "verdict")
        report.link(node, vnode, "verified_by")
        if verdict is None:
            report.fail(vnode, "no verdict for the claimed explanation")
            continue
        if claim.status is not verdict.verified:
            report.fail(node, f"claim status {claim.status.value} != verified {verdict.verified}")
        if set(claim.supporting_evidence_ids) != set(verdict.supporting_evidence_ids):
            report.fail(node, "claim support differs from the verdict's support")
        accepted = {
            chk.assertion.evidence_id
            for chk in verdict.checks
            if chk.accepted and chk.role == "supporting" and chk.reason == "true and material"
        }
        for eid in verdict.supporting_evidence_ids:
            if eid not in accepted:
                report.fail(vnode, f"support {eid} has no accepted material assertion")
            report.link(vnode, f"evidence:{eid}", "supported_by")
        explains = claim.hypothesis is not HypothesisKind.NATURAL_VARIATION
        support = claim.supporting_evidence_ids
        if explains and not any(report.reaches_events(f"evidence:{e}") for e in support):
            report.fail(node, "explanation does not resolve to any observed event")
    if verification is not None:
        verified = {v.kind: v.verified for v in verification.hypotheses}
        for kind, status in final.alternatives:
            node = report.add(f"verdict:{kind.value}", "verdict")
            if verified.get(kind) is not status:
                report.fail(
                    node,
                    f"alternative {kind.value} shown as {status.value}, verified "
                    f"{verified[kind].value if kind in verified else 'never'}",
                )
        for eid in verification.quarantined:
            if eid in pool:
                report.fail(f"evidence:{eid}", "quarantined evidence reached the final insight")
        if set(final.quarantined) != set(verification.quarantined):
            report.fail(
                f"final:{final.investigation_id}",
                "the insight does not disclose the evidence the verifier quarantined",
            )
        if final.evidence_integrity is not verification.evidence_integrity:
            report.fail(
                f"final:{final.investigation_id}",
                f"insight integrity {final.evidence_integrity.value} differs from the verified "
                f"{verification.evidence_integrity.value}",
            )
    for eid in final.event_ids:
        if eid not in ws.events:
            report.fail(f"final:{final.investigation_id}", f"event {eid} is not in the match")
    return report
