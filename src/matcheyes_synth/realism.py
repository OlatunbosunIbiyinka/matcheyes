"""Realism statistics for generated matches, compared with the provisional bands in
docs/synthetic-data.md#realism-acceptance.

These are acceptance checks on the generator's football, computed from observable events only.
They never look at whether an engine detects anything (no circular acceptance).
"""

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from matcheyes.domain.events import Foul, OwnGoal, Pass, PassOutcome, Shot, ShotOutcome
from matcheyes.domain.match import ObservableMatch

ON_BALL = ("pass", "carry", "take_on", "shot")


@dataclass(frozen=True)
class Band:
    metric: str
    low: float
    high: float
    per: str
    applies_to_mean: bool = False


BANDS: tuple[Band, ...] = (
    Band("events_per_match", 1400, 2400, "match"),
    Band("passes_per_team", 300, 650, "team"),
    Band("pass_completion_per_team", 0.70, 0.90, "team"),
    Band("shots_per_team", 4, 25, "team"),
    Band("goals_per_match", 2.3, 3.3, "match", applies_to_mean=True),
    Band("fouls_per_match", 14, 32, "match"),
    Band("possession_share_per_team", 0.30, 0.70, "team"),
)


def match_metrics(match: ObservableMatch) -> dict[str, list[float]]:
    """Per-metric samples: one value per match, or one per team for team metrics."""
    teams = match.info.team_ids
    passes = {t: 0 for t in teams}
    completed = {t: 0 for t in teams}
    shots = {t: 0 for t in teams}
    on_ball = {t: 0 for t in teams}
    goals = fouls = 0
    for event in match.events:
        team = getattr(event, "team_id", None)
        if event.type in ON_BALL and team is not None:
            on_ball[team] += 1
        if isinstance(event, Pass):
            passes[event.team_id] += 1
            completed[event.team_id] += event.outcome is PassOutcome.COMPLETE
        elif isinstance(event, Shot):
            shots[event.team_id] += 1
            goals += event.outcome is ShotOutcome.GOAL
        elif isinstance(event, OwnGoal):
            goals += 1
        elif isinstance(event, Foul):
            fouls += 1
    total_on_ball = sum(on_ball.values()) or 1
    return {
        "events_per_match": [float(len(match.events))],
        "passes_per_team": [float(passes[t]) for t in teams],
        "pass_completion_per_team": [completed[t] / max(1, passes[t]) for t in teams],
        "shots_per_team": [float(shots[t]) for t in teams],
        "goals_per_match": [float(goals)],
        "fouls_per_match": [float(fouls)],
        "possession_share_per_team": [on_ball[t] / total_on_ball for t in teams],
    }


@dataclass(frozen=True)
class BandResult:
    band: Band
    samples: int
    mean: float
    p5: float
    p50: float
    p95: float
    within_band: float

    @property
    def passed(self) -> bool:
        if self.band.applies_to_mean:
            return self.band.low <= self.mean <= self.band.high
        return self.within_band >= 0.9


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def summarise(matches: Iterable[ObservableMatch]) -> list[BandResult]:
    pooled: dict[str, list[float]] = {band.metric: [] for band in BANDS}
    for match in matches:
        for metric, values in match_metrics(match).items():
            pooled[metric].extend(values)
    results = []
    for band in BANDS:
        values = pooled[band.metric]
        if not values:
            raise ValueError("summarise() needs at least one match")
        inside = sum(band.low <= v <= band.high for v in values) / len(values)
        results.append(
            BandResult(
                band=band,
                samples=len(values),
                mean=statistics.fmean(values),
                p5=_quantile(values, 0.05),
                p50=_quantile(values, 0.5),
                p95=_quantile(values, 0.95),
                within_band=inside,
            )
        )
    return results


def format_table(results: Sequence[BandResult]) -> str:
    rows = [
        "| Metric | Band | n | Mean | p5 | p50 | p95 | Within band | Pass |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        band = f"{r.band.low:g} - {r.band.high:g}" + (" (mean)" if r.band.applies_to_mean else "")
        within = "n/a (mean)" if r.band.applies_to_mean else f"{r.within_band:.0%}"
        rows.append(
            f"| {r.band.metric} | {band} | {r.samples} | {r.mean:.3g} | {r.p5:.3g} | "
            f"{r.p50:.3g} | {r.p95:.3g} | {within} | {'yes' if r.passed else 'NO'} |"
        )
    return "\n".join(rows)
