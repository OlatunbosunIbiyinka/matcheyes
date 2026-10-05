"""Score one MatchEyes analysis against the hidden answer key.

This is the only place where engine output and ground truth meet. The engine never sees any of
it; the mapping below states, from the evaluator's side, which observable evidence would count
as finding each planted mechanism.
"""

from dataclasses import dataclass
from typing import Literal

from matcheyes.analytics.analysis import MatchAnalysis
from matcheyes.analytics.evidence import (
    ANALYTICS_CLAIM_CEILING,
    MetricShiftEvidence,
    MomentKind,
    PlayerInvolvementEvidence,
    RunOfPlayEvidence,
)
from matcheyes.domain.events import PeriodEnd
from matcheyes.domain.match import ObservableMatch
from matcheyes.domain.time import MatchInstant
from matcheyes_synth.truth import Decoy, ExpectedInsight, InsightKind, MechanismSignal

EARLY_TOLERANCE_S = 120
"""A shift may be flagged up to two minutes before the planted window: window comparisons place
a ramped change slightly early."""

INVOLVEMENT_RATIO = 1.25
"""Involvement counts as "up" when the substitute's share of team actions is at least a quarter
higher than the replaced player's; a bare "higher" is close to a coin flip."""

Side = Literal["team", "opponent", "either"]


@dataclass(frozen=True)
class MetricExpectation:
    metric: str
    side: Side
    direction: Literal["up", "down"] | None


MECHANISM_METRICS: dict[MechanismSignal, MetricExpectation] = {
    MechanismSignal.HIGH_RECOVERIES_UP: MetricExpectation("high_regains", "team", "up"),
    MechanismSignal.TERRITORY_SHIFT: MetricExpectation("field_tilt", "either", None),
    MechanismSignal.POSSESSION_SHARE_SHIFT: MetricExpectation("on_ball_share", "either", None),
    MechanismSignal.DEFENSIVE_LINE_DEEPER: MetricExpectation(
        "defensive_action_height", "team", "down"
    ),
    MechanismSignal.PRESS_SUCCESS_DOWN: MetricExpectation("pressure_regain_rate", "team", "down"),
    MechanismSignal.OPPONENT_PROGRESSIONS_UP: MetricExpectation(
        "progressive_actions", "opponent", "up"
    ),
    MechanismSignal.WIDE_PROGRESSIONS_UP: MetricExpectation("wide_share", "team", "up"),
    MechanismSignal.SHOT_VOLUME_UP: MetricExpectation("shots", "team", "up"),
    MechanismSignal.OPPONENT_PASS_COMPLETION_DOWN: MetricExpectation(
        "pass_completion", "opponent", "down"
    ),
}


class Clock:
    """Elapsed match seconds, using the observed first-half length so stoppage never overlaps."""

    def __init__(self, match: ObservableMatch) -> None:
        ends = [e.clock_ms for e in match.events if isinstance(e, PeriodEnd) and e.period == 1]
        self.first_half_s = (ends[0] if ends else 45 * 60_000) / 1000

    def seconds(self, instant: MatchInstant) -> float:
        offset = 0.0 if instant.period == 1 else self.first_half_s
        return offset + instant.clock_ms / 1000


@dataclass(frozen=True)
class InsightScore:
    insight_id: str
    kind: InsightKind
    detected: bool
    latency_s: float | None
    mechanisms_scored: int
    mechanisms_found: tuple[str, ...]
    in_moment: bool
    """Some matching evidence was promoted to a candidate moment."""


@dataclass(frozen=True)
class DecoyScore:
    decoy_id: str
    shift_moments: int
    max_strength_rank: int
    over_ceiling: bool


def _side_ok(side: Side, evidence_team: str, team: str) -> bool:
    if side == "either":
        return True
    return (evidence_team == team) == (side == "team")


def detection_window(insight: ExpectedInsight, clock: Clock) -> tuple[float, float]:
    start = clock.seconds(insight.window_start)
    return start - EARLY_TOLERANCE_S, start + insight.max_detection_latency_s


def metric_mechanisms(
    insight: ExpectedInsight, metric: str, team_id: str, direction: str
) -> tuple[MechanismSignal, ...]:
    """Scored mechanisms of the insight that a change in this metric would count as finding."""
    found = []
    for mech in insight.mechanisms:
        expected = MECHANISM_METRICS.get(mech)
        if (
            expected is not None
            and metric == expected.metric
            and _side_ok(expected.side, team_id, insight.team_id)
            and expected.direction in (None, direction)
        ):
            found.append(mech)
    return tuple(found)


def score_insight(
    insight: ExpectedInsight, analysis: MatchAnalysis, match: ObservableMatch
) -> InsightScore:
    clock = Clock(match)
    start = clock.seconds(insight.window_start)
    lo, hi = detection_window(insight, clock)
    found: dict[str, float] = {}
    matched: set[str] = set()

    def within(at: MatchInstant) -> bool:
        return lo <= clock.seconds(at) <= hi

    for e in analysis.evidence:
        if not within(e.at):
            continue
        if isinstance(e, MetricShiftEvidence):
            for mech in metric_mechanisms(insight, e.metric, e.team_id, e.direction):
                found.setdefault(mech.value, clock.seconds(e.at))
                matched.add(e.evidence_id)
        elif (
            isinstance(e, PlayerInvolvementEvidence)
            and MechanismSignal.PLAYER_INVOLVEMENT_UP in insight.mechanisms
            and e.team_id == insight.team_id
            and e.share is not None
            and (e.replaced_share is None or e.share >= INVOLVEMENT_RATIO * e.replaced_share)
        ):
            found.setdefault(MechanismSignal.PLAYER_INVOLVEMENT_UP.value, clock.seconds(e.at))
            matched.add(e.evidence_id)
        elif (
            isinstance(e, RunOfPlayEvidence)
            and insight.kind is InsightKind.AGAINST_THE_RUN_OF_PLAY
            and e.team_id == insight.team_id
            and e.against_run_of_play
        ):
            found.setdefault("against_run_of_play", clock.seconds(e.at))
            matched.add(e.evidence_id)

    promoted = {i for m in analysis.moments for i in m.evidence_ids}
    return InsightScore(
        insight_id=insight.insight_id,
        kind=insight.kind,
        detected=bool(found),
        latency_s=min(found.values()) - start if found else None,
        mechanisms_scored=len(insight.mechanisms),
        mechanisms_found=tuple(sorted(found)),
        in_moment=bool(found) and bool(matched & promoted),
    )


def score_decoy(decoy: Decoy, analysis: MatchAnalysis, match: ObservableMatch) -> DecoyScore:
    clock = Clock(match)
    lo, hi = clock.seconds(decoy.window_start), clock.seconds(decoy.window_end)
    involved = [
        m
        for m in analysis.moments
        if m.team_id == decoy.team_id and lo <= clock.seconds(m.at) <= hi
    ]
    ceiling = min(decoy.max_claim_strength.rank, ANALYTICS_CLAIM_CEILING.rank)
    max_rank = max((m.strength.rank for m in involved), default=0)
    return DecoyScore(
        decoy_id=decoy.decoy_id,
        shift_moments=sum(1 for m in involved if m.kind is MomentKind.METRIC_SHIFTS),
        max_strength_rank=max_rank,
        over_ceiling=max_rank > ceiling,
    )


def shift_moment_count(analysis: MatchAnalysis) -> int:
    return sum(1 for m in analysis.moments if m.kind is MomentKind.METRIC_SHIFTS)
