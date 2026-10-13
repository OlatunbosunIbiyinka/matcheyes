"""Snapshot evaluation: the existing pipeline, unchanged, on one snapshot and nothing else.

    snapshot prefix -> Stage 2 analysis -> Stage 3 candidates -> Stage 4 investigation with
    Stage 5 verification -> Stage 5 claim audit and lineage -> verified FinalInsights

The workspace is built from the snapshot's own events, so no investigation, verification or audit
can see an event after the snapshot. Each evaluation starts from scratch: nothing from an earlier
snapshot (evidence, verification, audit) is reused.

A prefix that breaks a data invariant is INVALID and is not evaluated. A pipeline error makes the
snapshot FAILED; it never falls back to earlier results. Traces stay out of the result: they carry
wall-clock latencies and belong to observability, not to canonical state.

`evaluate_and_analyse` also hands back what was established about the prefix before any
reasoning (its validity and Stage 2 team analytics), so a caller can reuse that work for the same
snapshot. It is not part of `SnapshotEvaluation`, which is unchanged.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from pydantic import Field

from matcheyes.agents.contracts import FinalInsight, VerificationResult
from matcheyes.agents.reasoning import ReasoningModel, RuleBasedReasoner
from matcheyes.agents.tools import MatchWorkspace, ToolBox
from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.contextual import analyse_contextual
from matcheyes.analytics.metrics import Series
from matcheyes.analytics.summary import TeamSummary
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.ingestion.invariants import ValidationReport, validate_prefix
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


@dataclass(frozen=True)
class SnapshotAnalytics:
    """The validity of one snapshot's prefix and, if it was valid and Stage 2 completed, its team
    summaries and series. `summaries` and `series` are None when Stage 2 did not run or failed."""

    snapshot_id: str
    log_digest: str
    validity: ValidationReport
    summaries: tuple[TeamSummary, ...] | None = None
    series: Mapping[tuple[str, Identifier], Series] | None = None


@dataclass(frozen=True)
class AnalysedEvaluation:
    evaluation: SnapshotEvaluation
    analytics: SnapshotAnalytics


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
    return evaluate_and_analyse(snapshot, model, config, toolbox).evaluation


def evaluate_and_analyse(
    snapshot: Snapshot,
    model: ReasoningModel | None = None,
    config: InvestigationConfig | None = None,
    toolbox: ToolBoxFactory | None = None,
) -> AnalysedEvaluation:
    """`evaluate_snapshot`, plus the validity and Stage 2 team analytics it computed on this
    snapshot's prefix. The analytics are kept when reasoning fails after Stage 2."""
    header = snapshot.header
    violations = validate_prefix(snapshot.match)
    analytics = SnapshotAnalytics(header.snapshot_id, header.log_digest, violations)
    if not violations.ok:
        codes = sorted({v.code.value for v in violations.violations})
        evaluation = SnapshotEvaluation(
            header=header, outcome=SnapshotOutcome.INVALID, failure=", ".join(codes)
        )
        return AnalysedEvaluation(evaluation, analytics)
    try:
        reasoner, settings = model or RuleBasedReasoner(), config or InvestigationConfig()
        stage2 = analyse_match(snapshot.match)
        analytics = _with_stage2(analytics, stage2)
        evaluation = _evaluate(snapshot, stage2, reasoner, settings, toolbox)
    except Exception as error:  # any pipeline failure: no truth is established on this snapshot
        evaluation = SnapshotEvaluation(
            header=header, outcome=SnapshotOutcome.FAILED, failure=type(error).__name__
        )
    return AnalysedEvaluation(evaluation, analytics)


def _with_stage2(analytics: SnapshotAnalytics, stage2: MatchAnalysis) -> SnapshotAnalytics:
    try:
        series = {(s.metric, s.team_id): s for s in stage2.series}
        return replace(analytics, summaries=stage2.summaries, series=series)
    except Exception:  # never let keeping the analytics change the evaluation
        return analytics


def _evaluate(
    snapshot: Snapshot,
    stage2: MatchAnalysis,
    model: ReasoningModel,
    config: InvestigationConfig,
    toolbox: ToolBoxFactory | None,
) -> SnapshotEvaluation:
    match = snapshot.match
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
