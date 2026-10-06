"""Snapshot evaluation: the existing pipeline, unchanged, on one snapshot and nothing else.

    snapshot prefix -> Stage 2 analysis -> Stage 3 candidates -> Stage 4 investigation with
    Stage 5 verification -> Stage 5 claim audit and lineage -> verified FinalInsights

The workspace is built from the snapshot's own events, so no investigation, verification or audit
can see an event after the snapshot. Each evaluation starts from scratch: nothing from an earlier
snapshot (evidence, verification, audit) is reused.

A prefix that breaks a data invariant is INVALID and is not evaluated. A pipeline error makes the
snapshot FAILED; it never falls back to earlier results. Traces stay out of the result: they carry
wall-clock latencies and belong to observability, not to canonical state.
"""

from collections.abc import Callable

from pydantic import Field

from matcheyes.agents.contracts import FinalInsight, VerificationResult
from matcheyes.agents.reasoning import ReasoningModel, RuleBasedReasoner
from matcheyes.agents.tools import MatchWorkspace, ToolBox
from matcheyes.analytics.analysis import analyse_match
from matcheyes.analytics.contextual import analyse_contextual
from matcheyes.domain.base import DomainModel
from matcheyes.ingestion.invariants import validate_prefix
from matcheyes.lifecycle.contracts import Direction, SnapshotHeader, SnapshotOutcome
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.orchestration.audit import audit_insight
from matcheyes.orchestration.investigation import (
    InvestigationConfig,
    InvestigationRecord,
    Orchestrator,
)
from matcheyes.orchestration.lineage import audit_lineage


class InsightOutcome(DomainModel):
    """One verified insight of a snapshot, with the Stage 3 identity of what it is about."""

    final: FinalInsight
    verification: VerificationResult | None
    metric: str
    direction: Direction
    anchor_bin: int = Field(ge=0)
    audit_findings: tuple[str, ...]


class SnapshotEvaluation(DomainModel):
    header: SnapshotHeader
    outcome: SnapshotOutcome
    failure: str | None = None
    suppression_bins: int | None = Field(
        default=None, description="Stage 2 suppression distance used on this snapshot."
    )
    insights: tuple[InsightOutcome, ...] = ()


Evaluator = Callable[[Snapshot], SnapshotEvaluation]
ToolBoxFactory = Callable[[MatchWorkspace], ToolBox]


def audit_findings(ws: MatchWorkspace, record: InvestigationRecord) -> tuple[str, ...]:
    """Stage 5 claim audit and lineage of one investigation, against `ws`."""
    report = audit_insight(ws, record.final, record.verification)
    found = [f"{f.check}: {f.detail}" for f in report.findings]
    found += [f"lineage: {b.node} {b.reason}" for b in audit_lineage(ws, record).broken]
    return tuple(found)


def evaluate_snapshot(
    snapshot: Snapshot,
    model: ReasoningModel | None = None,
    config: InvestigationConfig | None = None,
    toolbox: ToolBoxFactory | None = None,
) -> SnapshotEvaluation:
    header = snapshot.header
    violations = validate_prefix(snapshot.match)
    if not violations.ok:
        codes = sorted({v.code.value for v in violations.violations})
        return SnapshotEvaluation(
            header=header, outcome=SnapshotOutcome.INVALID, failure=", ".join(codes)
        )
    try:
        return _evaluate(
            snapshot, model or RuleBasedReasoner(), config or InvestigationConfig(), toolbox
        )
    except Exception as error:  # any pipeline failure: no truth is established on this snapshot
        return SnapshotEvaluation(
            header=header, outcome=SnapshotOutcome.FAILED, failure=type(error).__name__
        )


def _evaluate(
    snapshot: Snapshot,
    model: ReasoningModel,
    config: InvestigationConfig,
    toolbox: ToolBoxFactory | None,
) -> SnapshotEvaluation:
    match = snapshot.match
    stage2 = analyse_match(match)
    stage3 = analyse_contextual(match, stage2)
    ws = MatchWorkspace.build(match, stage2, stage3)
    orchestrator = Orchestrator(ws, model, config)
    if toolbox is not None:
        orchestrator.toolbox = toolbox(ws)
    insights = []
    for candidate in stage3.candidates:
        if candidate.level.rank < config.min_level.rank:
            continue
        record = orchestrator.investigate(candidate.candidate_id)
        insights.append(
            InsightOutcome(
                final=record.final,
                verification=record.verification,
                metric=candidate.metric,
                direction=candidate.direction,
                anchor_bin=candidate.bin_index,
                audit_findings=audit_findings(ws, record),
            )
        )
    return SnapshotEvaluation(
        header=snapshot.header,
        outcome=SnapshotOutcome.EVALUATED,
        suppression_bins=stage2.config.detection.suppression_bins,
        insights=tuple(insights),
    )
