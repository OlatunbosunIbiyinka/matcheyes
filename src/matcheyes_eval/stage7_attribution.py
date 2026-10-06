"""Stage 7 attribution: does the lifecycle lose an insight the pipeline actually produced?

Evaluation only. Three analyses:

1. Preservation (every case). The recorded snapshot evaluations are folded through `reconcile`
   one snapshot at a time. After each, every insight the pipeline produced on that snapshot must
   be held by exactly one storyline with the same key, verified through that snapshot, whose
   latest insight has the same fingerprint. An insight not held is a Stage 7 lifecycle bug: it
   was available to the lifecycle and lost. The fold must reproduce the engine's final state
   byte for byte, so the check is on the state the engine really built.

2. Attribution (planted variants; expected insights with a metric mechanism, as in Stages 3, 4
   and 7). For each expected insight, every snapshot from the start of its detection window is
   classified:

     F continued   - a storyline that has held a matching insight is open on this snapshot
     B lost        - a matching insight was produced on this snapshot and no storyline holds it
     C withdrawn   - such a storyline is withdrawn and nothing matching was produced here
     D no candidate- nothing matching was produced here, and no storyline has held one yet
     E unavailable - the snapshot is invalid or failed

   An expected insight with no matching insight on any snapshot is an upstream detection
   limitation (A). It is broken down by the furthest the pipeline got with it on any snapshot: a
   Stage 3 candidate below the investigation level, a Stage 2 shift that did not become a
   candidate, or no matching Stage 2 shift at all. "Matching" is the Stage 3/4/7 rule (`_matches`:
   team side, metric and direction of a scored mechanism, onset inside the detection window).

3. Revision churn (every case). For every revision with no change kind, which `FinalInsight`
   fields and whether the audit findings differ from the storyline's previous insight. Wording-
   only differences there are exactly what the change-kind rules deliberately do not notice.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from matcheyes.agents.contracts import FinalInsight
from matcheyes.analytics.analysis import analyse_match
from matcheyes.analytics.contextual import analyse_contextual
from matcheyes.analytics.evidence import MetricShiftEvidence
from matcheyes.domain.time import MatchInstant
from matcheyes.ingestion.invariants import validate_prefix
from matcheyes.lifecycle.contracts import (
    LifecycleState,
    SnapshotOutcome,
    StorylineState,
)
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.evaluate import InsightOutcome, SnapshotEvaluation, evaluate_snapshot
from matcheyes.lifecycle.reconcile import reconcile
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.orchestration.investigation import InvestigationConfig
from matcheyes.personalization.contracts import fingerprint
from matcheyes_eval.scoring import Clock, detection_window
from matcheyes_eval.stage2 import Case, Rate
from matcheyes_eval.stage3 import _has_metric_mechanism, _matches
from matcheyes_synth.truth import ExpectedInsight

Reconcile = Callable[[LifecycleState, SnapshotEvaluation], LifecycleState]
CATEGORIES = ("F continued", "B lost", "C withdrawn", "D no candidate", "E unavailable")
UPSTREAM = (
    "Stage 3 candidate below the investigation level",
    "Stage 2 shift, no Stage 3 candidate",
    "no matching Stage 2 shift",
)


@dataclass(frozen=True)
class Upstream:
    """What Stages 2 and 3 produced on one snapshot, as (team, metric, direction, at) keys."""

    shifts: tuple[tuple[str, str, str, MatchInstant], ...]
    candidates: tuple[tuple[str, str, str, MatchInstant, int], ...]
    """Also the Stage 3 level rank."""


class RecordingEvaluator:
    """The real evaluator, recording every evaluation in schedule order; optionally also what
    Stages 2 and 3 produced on each snapshot (for upstream attribution)."""

    def __init__(self, upstream: bool = False) -> None:
        self.evaluations: list[SnapshotEvaluation] = []
        self.upstream: dict[str, Upstream] = {}
        self._with_upstream = upstream

    def __call__(self, snapshot: Snapshot) -> SnapshotEvaluation:
        evaluation = evaluate_snapshot(snapshot)
        self.evaluations.append(evaluation)
        if self._with_upstream and validate_prefix(snapshot.match).ok:
            stage2 = analyse_match(snapshot.match)
            stage3 = analyse_contextual(snapshot.match, stage2)
            self.upstream[snapshot.header.snapshot_id] = Upstream(
                shifts=tuple(
                    (e.team_id, e.metric, e.direction, e.at)
                    for e in stage2.evidence
                    if isinstance(e, MetricShiftEvidence)
                ),
                candidates=tuple(
                    (c.team_id, c.metric, c.direction, c.at, c.level.rank)
                    for c in stage3.candidates
                ),
            )
        return evaluation


def _held(state: LifecycleState, insight: InsightOutcome, snapshot_id: str) -> str | None:
    """The storyline holding `insight` as established on `snapshot_id`, if any."""
    fp = fingerprint(insight.final)
    key = (insight.final.team_id, insight.metric, insight.direction)
    holders = [
        s.storyline_id
        for s in state.storylines
        if s.state is StorylineState.OPEN
        and s.verified_through == snapshot_id
        and (s.team_id, s.metric, s.direction) == key
        and s.latest_insight.insight_fingerprint == fp
    ]
    return holders[0] if len(holders) == 1 else None


@dataclass
class Fold:
    """The state after each snapshot, and which storyline held each produced insight."""

    states: list[LifecycleState]
    holder: dict[tuple[int, int], str | None]
    """(snapshot index, insight index) -> storyline ID, None when lost."""


def fold(
    match_id: str, evaluations: Sequence[SnapshotEvaluation], step: Reconcile = reconcile
) -> Fold:
    state = LifecycleState.empty(match_id)
    states: list[LifecycleState] = []
    holder: dict[tuple[int, int], str | None] = {}
    for k, evaluation in enumerate(evaluations):
        state = step(state, evaluation)
        states.append(state)
        if evaluation.outcome is SnapshotOutcome.EVALUATED:
            for i, insight in enumerate(evaluation.insights):
                holder[k, i] = _held(state, insight, evaluation.header.snapshot_id)
    return Fold(states, holder)


@dataclass
class InsightAttribution:
    case: str
    insight_id: str
    snapshots: Counter[str] = field(default_factory=Counter)
    first_available: str | None = None
    first_held: str | None = None
    storylines: tuple[str, ...] = ()
    upstream: str | None = None
    final: str = ""


@dataclass
class AttributionResults:
    split: str
    seeds: int
    matches: int = 0
    snapshots: int = 0
    produced: int = 0
    preserved: int = 0
    wrong_key: int = 0
    fold_equals_engine: Rate = field(default_factory=Rate)
    one_storyline_per_insight: Rate = field(default_factory=Rate)
    insights: list[InsightAttribution] = field(default_factory=list)
    churn_revisions: int = 0
    churn_fields: Counter[str] = field(default_factory=Counter)
    revisions: int = 0

    @property
    def lost(self) -> int:
        return self.produced - self.preserved


def _upstream_reason(
    insight: ExpectedInsight, clock: Clock, recorder: RecordingEvaluator, min_rank: int
) -> str:
    candidates = shifts = False
    for up in recorder.upstream.values():
        for team, metric, direction, at, rank in up.candidates:
            if rank < min_rank and _matches(insight, clock, team, metric, direction, at):
                candidates = True
        for team, metric, direction, at in up.shifts:
            if _matches(insight, clock, team, metric, direction, at):
                shifts = True
    if candidates:
        return UPSTREAM[0]
    return UPSTREAM[1] if shifts else UPSTREAM[2]


def _attribute(
    case: Case,
    insight: ExpectedInsight,
    evaluations: Sequence[SnapshotEvaluation],
    folded: Fold,
    recorder: RecordingEvaluator,
) -> InsightAttribution:
    clock = Clock(case.match)
    row = InsightAttribution(
        case=f"{case.spec.scenario_id}/s{case.seed}", insight_id=insight.insight_id
    )
    lo, _ = detection_window(insight, clock)
    followed: set[str] = set()
    for k, evaluation in enumerate(evaluations):
        header = evaluation.header
        if clock.seconds(header.as_of) < lo:
            continue
        label = header.label
        if evaluation.outcome is not SnapshotOutcome.EVALUATED:
            row.snapshots["E unavailable"] += 1
            continue
        matching = [
            (i, o)
            for i, o in enumerate(evaluation.insights)
            if _matches(insight, clock, o.final.team_id, o.metric, o.direction, o.final.at)
        ]
        lost = False
        for i, _o in matching:
            row.first_available = row.first_available or label
            sid = folded.holder[k, i]
            if sid is None:
                lost = True
            else:
                followed.add(sid)
                row.first_held = row.first_held or label
        state = folded.states[k]
        tracked = [s for s in state.storylines if s.storyline_id in followed]
        if lost:
            row.snapshots["B lost"] += 1
        elif any(s.state is StorylineState.OPEN for s in tracked):
            row.snapshots["F continued"] += 1
        elif tracked:
            row.snapshots["C withdrawn"] += 1
        else:
            row.snapshots["D no candidate"] += 1
    row.storylines = tuple(sorted(followed))
    if row.first_available is None:
        row.upstream = _upstream_reason(
            insight, clock, recorder, InvestigationConfig().min_level.rank
        )
        row.final = f"A upstream: {row.upstream}"
    elif row.snapshots["B lost"]:
        row.final = "B Stage 7 lifecycle bug"
    else:
        open_now = [
            s
            for s in folded.states[-1].storylines
            if s.storyline_id in followed and s.state is StorylineState.OPEN
        ]
        row.final = "F open at the end" if open_now else "C withdrawn by the end"
    return row


def _churn(state: LifecycleState, r: AttributionResults) -> None:
    for s in state.storylines:
        r.revisions += len(s.revisions)
        for index, rev in enumerate(s.revisions):
            if rev.change_kinds or rev.final is None or index == 0:
                continue
            r.churn_revisions += 1
            previous = [p for p in s.revisions[:index] if p.final is not None][-1]
            earlier = [p.final for p in s.revisions[:index] if p.final is not None][-1]
            for name in _differing(earlier, rev.final):
                r.churn_fields[name] += 1
            if previous.audit_findings != rev.audit_findings:
                r.churn_fields["(audit findings)"] += 1


def _differing(old: FinalInsight, new: FinalInsight) -> list[str]:
    names = []
    for name in FinalInsight.model_fields:
        if name == "evidence":
            continue
        if getattr(old, name) != getattr(new, name):
            names.append(name)
    if old.evidence != new.evidence:
        parts = set()
        for a, b in zip(old.evidence, new.evidence, strict=False):
            parts |= {f for f in type(a).model_fields if getattr(a, f) != getattr(b, f)}
        if len(old.evidence) != len(new.evidence):
            parts.add("count")
        names += [f"evidence.{p}" for p in sorted(parts)]
    return names


def attribute_case(case: Case, r: AttributionResults, step: Reconcile = reconcile) -> None:
    """`step` is the reconciliation under test (the real one by default)."""
    expected = [i for i in case.spec.expected_insights if _has_metric_mechanism(i)]
    upstream = case.variant == "planted" and bool(expected)
    recorder = RecordingEvaluator(upstream=upstream)
    engine = replay(case.match.info, case.match.events, recorder)
    evaluations = recorder.evaluations
    folded = fold(case.match.info.match_id, evaluations, step)
    r.matches += 1
    r.snapshots += len(evaluations)
    r.fold_equals_engine.add(
        bool(folded.states)
        and folded.states[-1].model_dump_json() == engine.state.model_dump_json()
    )
    for k, evaluation in enumerate(evaluations):
        established = folded.states[k].snapshots[-1].storyline_ids
        if evaluation.outcome is SnapshotOutcome.EVALUATED:
            r.one_storyline_per_insight.add(
                len(established) == len(set(established)) == len(evaluation.insights)
            )
        for i, insight in enumerate(evaluation.insights):
            r.produced += 1
            sid = folded.holder.get((k, i))
            if sid is not None:
                r.preserved += 1
                s = folded.states[k].storyline(sid)
                key = (insight.final.team_id, insight.metric, insight.direction)
                r.wrong_key += int((s.team_id, s.metric, s.direction) != key)
    _churn(engine.state, r)
    if upstream:
        r.insights += [_attribute(case, i, evaluations, folded, recorder) for i in expected]


def evaluate_attribution(cases: Sequence[Case], split: str, seeds: int) -> AttributionResults:
    results = AttributionResults(split=split, seeds=seeds)
    for case in cases:
        attribute_case(case, results)
    return results


def format_attribution(r: AttributionResults) -> str:
    lines = [
        f"Stage 7 attribution - {r.split} split, {r.seeds} seed(s) per scenario, {r.matches} "
        f"matches, {r.snapshots} snapshots",
        "",
        "preservation (every insight the pipeline produced, on the snapshot it was produced):",
        f"  produced {r.produced}; held by a storyline {r.preserved}; lost {r.lost}; held under "
        f"another key {r.wrong_key}",
        f"  one storyline per insight on every evaluated snapshot {r.one_storyline_per_insight}",
        f"  snapshot-by-snapshot fold reproduces the engine state {r.fold_equals_engine}",
        "",
        "planted expected insights (metric mechanisms), per snapshot from the window start:",
        f"  {'insight':<52} {'F':>4} {'B':>3} {'C':>4} {'D':>4} {'E':>3}  first produced / "
        "first held; outcome",
    ]
    for row in r.insights:
        c = row.snapshots
        lines.append(
            f"  {row.case + '/' + row.insight_id:<52} {c['F continued']:>4} {c['B lost']:>3} "
            f"{c['C withdrawn']:>4} {c['D no candidate']:>4} {c['E unavailable']:>3}  "
            f"{row.first_available or '-'} / {row.first_held or '-'}; {row.final}"
            + (f" ({len(row.storylines)} storylines)" if len(row.storylines) > 1 else "")
        )
    outcomes = Counter(row.final for row in r.insights)
    lines += [
        "",
        f"  snapshot totals: {per_category(r.insights)}",
        f"  outcomes: {dict(sorted(outcomes.items()))}",
    ]
    lines += [
        "",
        f"revision churn: {r.churn_revisions} of {r.revisions} revisions record no change kind; "
        "differing fields:",
        f"  {dict(r.churn_fields.most_common()) or 'none'}",
    ]
    return "\n".join(lines)


def per_category(rows: Sequence[InsightAttribution]) -> dict[str, int]:
    total: dict[str, int] = defaultdict(int)
    for row in rows:
        for name in CATEGORIES:
            total[name] += row.snapshots[name]
    return dict(total)
