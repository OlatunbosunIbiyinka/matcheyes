"""Stage 3 evaluation: contextual candidates against the frozen Stage 2 baseline, same matches.

Only metric mechanisms are scored, for both stages, so the comparison is like for like: Stage 3
re-grades Stage 2 metric shifts and leaves key-event, run-of-play and involvement evidence as it
was. Measures per expected insight, planted and twin:

* s2_evidence - a matching Stage 2 shift exists;
* s2_moment   - a matching Stage 2 shift reached a Stage 2 candidate moment;
* s3_weak     - a matching Stage 3 candidate is at least WEAK (survives context);
* s3_moment   - a matching Stage 3 candidate reaches the moment level (MODERATE by default);
* s3_strong   - a matching Stage 3 candidate is STRONG.
"""

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from matcheyes.analytics.analysis import analyse_match
from matcheyes.analytics.baselines import BaselineKind
from matcheyes.analytics.contextual import (
    ContextualAnalysis,
    ContextualCandidate,
    ContextualConfig,
    analyse_contextual,
)
from matcheyes.analytics.evidence import ANALYTICS_CLAIM_CEILING, MetricShiftEvidence
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.match import ObservableMatch
from matcheyes.domain.time import MatchInstant
from matcheyes_eval.scoring import (
    MECHANISM_METRICS,
    Clock,
    detection_window,
    metric_mechanisms,
    score_decoy,
    shift_moment_count,
)
from matcheyes_eval.stage2 import CONTROL_SCENARIO, Case, Rate, Variant
from matcheyes_synth.truth import Decoy, ExpectedInsight

MEASURES = ("s2_evidence", "s2_moment", "s3_weak", "s3_moment", "s3_strong")
DENSITY = ("s2_shifts", "s2_moments", "s3_weak", "s3_moments")


@dataclass
class Stage3Row:
    planted: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))
    twin: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))


@dataclass
class Stage3Results:
    split: str
    seeds: int
    config: ContextualConfig
    insights: dict[str, Stage3Row] = field(default_factory=lambda: defaultdict(Stage3Row))
    density: dict[str, dict[str, list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    decoys: dict[str, dict[str, Rate]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Rate))
    )
    decoy_ceiling_violations: Counter[str] = field(default_factory=Counter)
    ceiling_violations: int = 0
    untraceable: int = 0
    candidates: int = 0
    profile: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))

    def recall(self, measure: str, variant: Variant) -> Rate:
        total = Rate()
        for row in self.insights.values():
            rate = (row.planted if variant == "planted" else row.twin)[measure]
            total.hits += rate.hits
            total.total += rate.total
        return total

    def mean_density(self, group: str, key: str) -> float:
        values = self.density[group][key]
        return statistics.fmean(values) if values else 0.0


def _has_metric_mechanism(insight: ExpectedInsight) -> bool:
    return any(m in MECHANISM_METRICS for m in insight.mechanisms)


def _matches(
    insight: ExpectedInsight,
    clock: Clock,
    team_id: str,
    metric: str,
    direction: str,
    at: MatchInstant,
) -> bool:
    lo, hi = detection_window(insight, clock)
    return lo <= clock.seconds(at) <= hi and bool(
        metric_mechanisms(insight, metric, team_id, direction)
    )


def _profile_key(c: ContextualCandidate) -> str:
    support = "support" if c.pattern and c.pattern.independent_families else "no-support"
    persistence = c.persistence.persistence.value
    if c.baseline.kind is BaselineKind.REMOVED:
        return "removed"
    if c.baseline.aligned_with is not None:
        return f"aligned/{support}"
    return f"{support}/{persistence}"


def _decoy_hit(decoy: Decoy, s3: ContextualAnalysis, clock: Clock) -> tuple[bool, int]:
    lo, hi = clock.seconds(decoy.window_start), clock.seconds(decoy.window_end)
    involved = [
        m for m in s3.moments if m.team_id == decoy.team_id and lo <= clock.seconds(m.at) <= hi
    ]
    return bool(involved), max((m.strength.rank for m in involved), default=0)


def _traceable(s3: ContextualAnalysis, match: ObservableMatch) -> int:
    known = {e.event_id for e in match.events}
    ids = s3.candidate_by_id()
    bad = sum(1 for c in s3.candidates if not c.event_ids or not set(c.event_ids) <= known)
    bad += sum(1 for m in s3.moments if not set(m.candidate_ids) <= set(ids))
    return bad


def evaluate_stage3(
    cases: Iterable[Case], split: str, seeds: int, config: ContextualConfig | None = None
) -> Stage3Results:
    config = config or ContextualConfig()
    results = Stage3Results(split=split, seeds=seeds, config=config)
    moment_rank = config.moment_level.rank
    for case in cases:
        stage2 = analyse_match(case.match)
        s3 = analyse_contextual(case.match, stage2, config)
        clock = Clock(case.match)
        sid = case.spec.scenario_id
        shifts = [e for e in stage2.evidence if isinstance(e, MetricShiftEvidence)]
        promoted = {i for m in stage2.moments for i in m.evidence_ids}

        results.candidates += len(s3.candidates)
        results.untraceable += _traceable(s3, case.match)
        strengths = [c.strength for c in s3.candidates] + [m.strength for m in s3.moments]
        results.ceiling_violations += sum(
            1 for s in strengths if s.rank > ANALYTICS_CLAIM_CEILING.rank
        )
        group = CONTROL_SCENARIO if sid == CONTROL_SCENARIO else case.variant
        density = results.density[group]
        density["s2_shifts"].append(len(shifts))
        density["s2_moments"].append(shift_moment_count(stage2))
        density["s3_weak"].append(sum(1 for c in s3.candidates if c.level.rank >= 1))
        density["s3_moments"].append(len(s3.moments))
        if sid == CONTROL_SCENARIO:
            for c in s3.candidates:
                results.profile["control (all candidates)"][_profile_key(c)] += 1

        if case.variant == "planted":
            for decoy in case.spec.decoys:
                key = f"{sid}/{decoy.decoy_id}"
                s2_score = score_decoy(decoy, stage2, case.match)
                results.decoys[key]["s2"].add(s2_score.shift_moments > 0)
                results.decoy_ceiling_violations["s2"] += int(s2_score.over_ceiling)
                hit, rank = _decoy_hit(decoy, s3, clock)
                results.decoys[key]["s3"].add(hit)
                ceiling = min(decoy.max_claim_strength.rank, ANALYTICS_CLAIM_CEILING.rank)
                results.decoy_ceiling_violations["s3"] += int(rank > ceiling)

        for insight in case.spec.expected_insights:
            if not _has_metric_mechanism(insight):
                continue
            row = results.insights[f"{sid}/{insight.insight_id}"]
            rates = row.planted if case.variant == "planted" else row.twin
            hits = [
                s
                for s in shifts
                if _matches(insight, clock, s.team_id, s.metric, s.direction, s.at)
            ]
            matching = [
                c
                for c in s3.candidates
                if _matches(insight, clock, c.team_id, c.metric, c.direction, c.at)
            ]
            rates["s2_evidence"].add(bool(hits))
            rates["s2_moment"].add(any(s.evidence_id in promoted for s in hits))
            rates["s3_weak"].add(any(c.level.rank >= 1 for c in matching))
            rates["s3_moment"].add(any(c.level.rank >= moment_rank for c in matching))
            rates["s3_strong"].add(any(c.level is EvidenceLevel.STRONG for c in matching))
            for c in matching:
                results.profile[f"{case.variant} (matching)"][_profile_key(c)] += 1
    return results


def _gap(results: Stage3Results, measure: str) -> str:
    planted, twin = results.recall(measure, "planted"), results.recall(measure, "twin")
    return f"{planted!s:>14} {twin!s:>14} {planted.value - twin.value:>+7.0%}"


def format_stage3(results: Stage3Results) -> str:
    c = results.config
    lines = [
        f"Stage 3 evaluation - {results.split} split, {results.seeds} seeds per scenario",
        f"config: coincidence {c.coincidence_bins} min, support z >= {c.support_z}, "
        f"contradiction z <= -{c.contradiction_z}, sub-window {c.sub_window_bins} min, "
        f"hold z >= {c.hold_z}, moment level {c.moment_level.value}",
        "",
        "metric mechanisms only; planted vs counterfactual twin (excludes insights without one)",
        f"{'measure':<14} {'planted':>14} {'twin':>14} {'gap':>7}",
    ]
    lines += [f"{m:<14} {_gap(results, m)}" for m in MEASURES]
    lines += ["", f"{'insight':<36} " + " ".join(f"{m:>17}" for m in MEASURES)]
    for key in sorted(results.insights):
        row = results.insights[key]
        cells = []
        for m in MEASURES:
            p, t = row.planted[m], row.twin[m]
            twin = f"{t.value:.0%}" if t.total else "n/a"
            cells.append(f"{p.value:>8.0%} / {twin:>5}")
        lines.append(f"{key:<36} " + " ".join(f"{cell:>17}" for cell in cells))
    lines += ["", "mean per match:", f"{'group':<24} " + " ".join(f"{k:>11}" for k in DENSITY)]
    for group in sorted(results.density):
        lines.append(
            f"{group:<24} " + " ".join(f"{results.mean_density(group, k):>11.1f}" for k in DENSITY)
        )
    lines += ["", "decoys (share of matches with a moment for the decoy team in the window):"]
    for key in sorted(results.decoys):
        d = results.decoys[key]
        lines.append(f"  {key:<34} stage 2 {d['s2']!s:>14}   stage 3 {d['s3']!s:>14}")
    lines += [
        f"claims above the decoy ceiling: stage 2 {results.decoy_ceiling_violations['s2']}, "
        f"stage 3 {results.decoy_ceiling_violations['s3']}",
        f"claims above {ANALYTICS_CLAIM_CEILING.value}: {results.ceiling_violations}",
        f"untraceable candidates or moments: {results.untraceable} of {results.candidates}",
        "",
        "candidate profile (baseline / support / persistence):",
    ]
    for group in sorted(results.profile):
        counts = results.profile[group]
        total = sum(counts.values())
        parts = ", ".join(f"{k} {v / total:.0%}" for k, v in sorted(counts.items()))
        lines.append(f"  {group:<26} n={total:<5} {parts}")
    return "\n".join(lines)
