"""Stage 2 evaluation: deterministic analytics against planted truth, twins, controls and decoys.

Tuning uses development seeds only. Held-out seeds are evaluated with a frozen configuration.
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from matcheyes.analytics.analysis import analyse_match
from matcheyes.analytics.changepoints import DetectionConfig
from matcheyes.analytics.moments import AnalysisConfig
from matcheyes.domain.match import ObservableMatch
from matcheyes_eval.scoring import (
    score_decoy,
    score_insight,
    shift_moment_count,
)
from matcheyes_synth.effects import counterfactual
from matcheyes_synth.generator import generate_match
from matcheyes_synth.scenarios import CATALOGUE
from matcheyes_synth.seeds import development_seeds, held_out_seeds, is_held_out
from matcheyes_synth.truth import ScenarioSpec

Split = Literal["development", "held-out"]
Variant = Literal["planted", "twin"]
CONTROL_SCENARIO = "S01_control_balanced"


@dataclass(frozen=True)
class Case:
    spec: ScenarioSpec
    seed: int
    variant: Variant
    match: ObservableMatch


def seeds_for(split: Split, count: int) -> tuple[int, ...]:
    return development_seeds(count) if split == "development" else held_out_seeds(count)


def build_cases(seeds: Iterable[int], scenarios: Iterable[ScenarioSpec] = CATALOGUE) -> list[Case]:
    cases = []
    for spec in scenarios:
        for seed in seeds:
            cases.append(Case(spec, seed, "planted", generate_match(spec, seed).observable))
            if spec.interventions:
                twin = generate_match(counterfactual(spec), seed).observable
                cases.append(Case(spec, seed, "twin", twin))
    return cases


@dataclass
class Rate:
    hits: int = 0
    total: int = 0

    def add(self, hit: bool) -> None:
        self.hits += int(hit)
        self.total += 1

    @property
    def value(self) -> float:
        return self.hits / self.total if self.total else 0.0

    def __str__(self) -> str:
        return f"{self.value:5.0%} ({self.hits}/{self.total})"


@dataclass
class InsightRow:
    planted: Rate = field(default_factory=Rate)
    twin: Rate = field(default_factory=Rate)
    planted_moment: Rate = field(default_factory=Rate)
    twin_moment: Rate = field(default_factory=Rate)
    coverage: list[float] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    mechanisms_planted: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))
    mechanisms_twin: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))


@dataclass
class Stage2Results:
    split: str
    seeds: int
    config: AnalysisConfig
    insights: dict[str, InsightRow] = field(default_factory=lambda: defaultdict(InsightRow))
    decoys: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))
    decoy_ceiling_violations: int = 0
    shift_moments: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))

    def recall(self, variant: Variant, *, moments: bool = False) -> Rate:
        total = Rate()
        for key, row in self.insights.items():
            if key.startswith("S07"):
                continue
            if moments:
                rate = row.planted_moment if variant == "planted" else row.twin_moment
            else:
                rate = row.planted if variant == "planted" else row.twin
            total.hits += rate.hits
            total.total += rate.total
        return total

    def mean_coverage(self) -> float:
        values = [
            c for k, r in self.insights.items() if not k.startswith("S07") for c in r.coverage
        ]
        return statistics.fmean(values) if values else 0.0

    def control_moments(self) -> float:
        values = self.shift_moments.get(CONTROL_SCENARIO, [])
        return statistics.fmean(values) if values else 0.0


def evaluate(
    cases: Iterable[Case], split: str, seeds: int, config: AnalysisConfig
) -> Stage2Results:
    results = Stage2Results(split=split, seeds=seeds, config=config)
    for case in cases:
        analysis = analyse_match(case.match, config)
        sid = case.spec.scenario_id
        if case.variant == "planted":
            results.shift_moments[sid].append(shift_moment_count(analysis))
            for decoy in case.spec.decoys:
                decoy_score = score_decoy(decoy, analysis, case.match)
                results.decoys[f"{sid}/{decoy.decoy_id}"].add(decoy_score.shift_moments > 0)
                results.decoy_ceiling_violations += int(decoy_score.over_ceiling)
        for insight in case.spec.expected_insights:
            row = results.insights[f"{sid}/{insight.insight_id}"]
            score = score_insight(insight, analysis, case.match)
            mechanisms = [m.value for m in insight.mechanisms]
            if case.variant == "planted":
                row.planted.add(score.detected)
                row.planted_moment.add(score.in_moment)
                if score.mechanisms_scored:
                    row.coverage.append(len(score.mechanisms_found) / score.mechanisms_scored)
                if score.latency_s is not None:
                    row.latencies.append(score.latency_s)
                for mech in mechanisms:
                    row.mechanisms_planted[mech].add(mech in score.mechanisms_found)
            else:
                row.twin.add(score.detected)
                row.twin_moment.add(score.in_moment)
                for mech in mechanisms:
                    row.mechanisms_twin[mech].add(mech in score.mechanisms_found)
    return results


def config_with(z: float, window: int, baseline: int) -> AnalysisConfig:
    return AnalysisConfig(
        detection=DetectionConfig(
            window_bins=window, baseline_bins=baseline, z_threshold=z, suppression_bins=window
        )
    )


def tune(
    cases: list[Case], grid: Iterable[tuple[float, int, int]], seeds: int
) -> list[Stage2Results]:
    if any(is_held_out(c.seed) for c in cases):
        raise ValueError("tuning must never see held-out seeds")
    return [evaluate(cases, "development", seeds, config_with(*point)) for point in grid]


def format_results(results: Stage2Results) -> str:
    d = results.config.detection
    lines = [
        f"Stage 2 evaluation - {results.split} split, {results.seeds} seeds per scenario",
        f"config: window {d.window_bins} min, baseline {d.baseline_bins} min, "
        f"z >= {d.z_threshold}, merge {results.config.merge_bins} min, "
        f"moment z >= {results.config.moment_min_statistic} or 2+ families",
        "",
        "evidence = matching evidence exists; moment = it reached a candidate moment",
        f"{'insight':<36} {'planted':>14} {'twin':>14} {'planted-mom':>14} {'twin-mom':>14} "
        f"{'coverage':>9} {'latency':>8}",
    ]
    for key in sorted(results.insights):
        row = results.insights[key]
        coverage = f"{statistics.fmean(row.coverage):.0%}" if row.coverage else "n/a"
        latency = f"{statistics.median(row.latencies):.0f}s" if row.latencies else "n/a"
        twin = str(row.twin) if row.twin.total else "n/a"
        twin_m = str(row.twin_moment) if row.twin_moment.total else "n/a"
        lines.append(
            f"{key:<36} {row.planted!s:>14} {twin:>14} {row.planted_moment!s:>14} "
            f"{twin_m:>14} {coverage:>9} {latency:>8}"
        )
        for mech in sorted(row.mechanisms_planted):
            planted = row.mechanisms_planted[mech]
            twin_rate = row.mechanisms_twin.get(mech)
            twin_text = str(twin_rate) if twin_rate and twin_rate.total else "n/a"
            lines.append(f"  - {mech:<32} {planted!s:>14} {twin_text:>14}")
    lines += [
        "",
        f"overall evidence recall (excl. S07): planted {results.recall('planted')}, "
        f"twin {results.recall('twin')}",
        f"overall moment recall   (excl. S07): planted "
        f"{results.recall('planted', moments=True)}, "
        f"twin {results.recall('twin', moments=True)}",
        f"mean mechanism coverage (planted, excl. S07): {results.mean_coverage():.0%}",
        "",
        "metric-shift moments per match:",
    ]
    for sid in sorted(results.shift_moments):
        values = results.shift_moments[sid]
        lines.append(f"  {sid:<34} mean {statistics.fmean(values):4.1f}  max {max(values)}")
    lines += ["", "decoys (share of matches with a metric-shift moment for the decoy team):"]
    for key in sorted(results.decoys):
        lines.append(f"  {key:<34} {results.decoys[key]}")
    lines.append(f"claims above the decoy ceiling: {results.decoy_ceiling_violations}")
    return "\n".join(lines)


def format_tuning(rows: list[Stage2Results]) -> str:
    lines = [
        f"{'window':>6} {'base':>5} {'z':>5} {'recall':>14} {'twin':>14} {'coverage':>9} "
        f"{'control/match':>14}"
    ]
    for r in rows:
        d = r.config.detection
        lines.append(
            f"{d.window_bins:>6} {d.baseline_bins:>5} {d.z_threshold:>5.1f} "
            f"{r.recall('planted')!s:>14} "
            f"{r.recall('twin')!s:>14} {r.mean_coverage():>9.0%} {r.control_moments():>14.1f}"
        )
    return "\n".join(lines)
