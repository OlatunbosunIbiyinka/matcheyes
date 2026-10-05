"""Multi-signal football patterns: do other metrics move the way the game would if the shift
reflects a real change in how a team plays?

A pattern is a set of signals that football reasoning expects to move together (rationale per
pattern in docs/contextual-evidence.md#patterns). A Stage 2 shift is the *core* signal; every
other signal of each pattern containing it is tested at the same boundary and on the same
(contextual) windows. No search over boundaries happens here, so a support test is a single
comparison, not a scan.

* support - directional statistic >= `support_z`;
* contradiction - directional statistic <= -`contradiction_z` (a lower bar: it should be easier
  to be contradicted than supported);
* neutral / unavailable (window too thin) otherwise.

Signals are not counted as independent when they cannot be: a signal from the core's own metric
family, or from a metric that shares events with the core metric by construction
(`DEPENDENT_METRICS`), is *corroborating* only. Strength comes from the number of distinct
independent families, never from the number of metrics.
"""

from typing import Literal

from pydantic import Field

from matcheyes.analytics.baselines import Direction, Span
from matcheyes.analytics.changepoints import compare_spans
from matcheyes.analytics.metrics import METRIC_BY_NAME, Series
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier, MatchInfo

Side = Literal["team", "opponent"]
Status = Literal["support", "contradiction", "neutral", "unavailable"]


class Signal(DomainModel):
    side: Side
    metric: str
    direction: Direction


class Pattern(DomainModel):
    name: str
    concept: str
    signals: tuple[Signal, ...] = Field(min_length=2)


def _s(side: Side, metric: str, direction: Direction) -> Signal:
    return Signal(side=side, metric=metric, direction=direction)


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        name="pressing_intensification",
        concept="The team presses more and wins the ball back higher up the pitch.",
        signals=(
            _s("team", "pressures", "up"),
            _s("team", "high_regains", "up"),
            _s("team", "pressure_regain_rate", "up"),
            _s("team", "defensive_action_height", "up"),
            _s("opponent", "pass_completion", "down"),
            _s("opponent", "turnovers", "up"),
        ),
    ),
    Pattern(
        name="pressing_decline",
        concept="The team's press becomes less frequent or less effective and the opponent "
        "moves the ball forward more easily.",
        signals=(
            _s("team", "pressures", "down"),
            _s("team", "pressure_regain_rate", "down"),
            _s("team", "high_regains", "down"),
            _s("opponent", "progressive_actions", "up"),
            _s("opponent", "pass_completion", "up"),
        ),
    ),
    Pattern(
        name="deeper_defending",
        concept="The team defends closer to its own goal and concedes territory.",
        signals=(
            _s("team", "defensive_action_height", "down"),
            _s("team", "high_regains", "down"),
            _s("opponent", "field_tilt", "up"),
            _s("opponent", "attacking_third_entries", "up"),
        ),
    ),
    Pattern(
        name="territorial_dominance",
        concept="The team plays more of the game in the opponent's third.",
        signals=(
            _s("team", "field_tilt", "up"),
            _s("team", "attacking_third_entries", "up"),
            _s("team", "shots", "up"),
            _s("opponent", "defensive_action_height", "down"),
        ),
    ),
    Pattern(
        name="attacking_decline",
        concept="The team reaches the final third and shoots less often.",
        signals=(
            _s("team", "attacking_third_entries", "down"),
            _s("team", "progressive_actions", "down"),
            _s("team", "shots", "down"),
            _s("opponent", "field_tilt", "up"),
        ),
    ),
    Pattern(
        name="control_gain",
        concept="The team keeps the ball more and moves it forward securely.",
        signals=(
            _s("team", "on_ball_share", "up"),
            _s("team", "pass_completion", "up"),
            _s("team", "progressive_actions", "up"),
            _s("team", "turnovers", "down"),
        ),
    ),
    Pattern(
        name="ball_security_loss",
        concept="The team loses the ball more often in open play.",
        signals=(
            _s("team", "turnovers", "up"),
            _s("team", "pass_completion", "down"),
            _s("opponent", "high_regains", "up"),
        ),
    ),
    Pattern(
        name="wide_attacking_shift",
        concept="The team attacks more through wide areas of the opponent's half.",
        signals=(
            _s("team", "wide_share", "up"),
            _s("team", "attacking_third_entries", "up"),
            _s("team", "progressive_actions", "up"),
        ),
    ),
)

DEPENDENT_METRICS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"high_regains", "defensive_action_height"}),
        frozenset({"field_tilt", "attacking_third_entries"}),
        frozenset({"field_tilt", "shots"}),
        frozenset({"pass_completion", "turnovers"}),
        frozenset({"turnovers", "high_regains"}),
        frozenset({"progressive_actions", "attacking_third_entries"}),
    }
)
"""Metric pairs that share events by construction, so they move together mechanically:
a high regain is also a defensive action (raising mean height); an attacking-third entry ends
in the attacking third (adding to field tilt), as do most shots; an incomplete open-play pass
usually ends a possession (a turnover), and an opponent's high regain ends one too; many entries
are progressive actions."""


class SignalReading(DomainModel):
    signal: Signal
    team_id: Identifier
    family: str
    statistic: float | None = Field(description="Directional: positive means as expected.")
    status: Status
    independent: bool


class PatternAssessment(DomainModel):
    pattern: str
    concept: str
    subject_team_id: Identifier = Field(description="The team the pattern describes.")
    readings: tuple[SignalReading, ...]
    independent_families: tuple[str, ...]

    @property
    def supports(self) -> tuple[SignalReading, ...]:
        return tuple(r for r in self.readings if r.status == "support")

    @property
    def contradictions(self) -> tuple[SignalReading, ...]:
        return tuple(r for r in self.readings if r.status == "contradiction")

    @property
    def contradicted(self) -> bool:
        """At least one contradiction, and no fewer contradictions than independent supports."""
        independent = sum(1 for r in self.supports if r.independent)
        return bool(self.contradictions) and len(self.contradictions) >= independent


def is_independent(core_metric: str, metric: str) -> bool:
    if METRIC_BY_NAME[core_metric].family is METRIC_BY_NAME[metric].family:
        return False
    return frozenset({core_metric, metric}) not in DEPENDENT_METRICS


def _read(
    signal: Signal,
    team_id: Identifier,
    core_metric: str,
    series: Series,
    spans: tuple[Span, Span],
    support_z: float,
    contradiction_z: float,
) -> SignalReading:
    spec = METRIC_BY_NAME[signal.metric]
    result = compare_spans(series, spec, *spans)
    directional = None
    status: Status = "unavailable"
    if result is not None:
        directional = result.statistic * (1 if signal.direction == "up" else -1)
        if directional >= support_z:
            status = "support"
        elif directional <= -contradiction_z:
            status = "contradiction"
        else:
            status = "neutral"
    return SignalReading(
        signal=signal,
        team_id=team_id,
        family=spec.family.value,
        statistic=directional,
        status=status,
        independent=is_independent(core_metric, signal.metric),
    )


def assess_patterns(
    team_id: Identifier,
    metric: str,
    direction: Direction,
    spans: tuple[Span, Span],
    series: dict[tuple[str, Identifier], Series],
    info: MatchInfo,
    support_z: float,
    contradiction_z: float,
) -> PatternAssessment | None:
    """The best-supported pattern containing the core signal, or None if no pattern does."""
    assessments: list[PatternAssessment] = []
    for pattern in PATTERNS:
        for core in pattern.signals:
            if (core.metric, core.direction) != (metric, direction):
                continue
            subject = team_id if core.side == "team" else info.opponent_of(team_id)
            readings = []
            for signal in pattern.signals:
                if signal == core:
                    continue
                team = subject if signal.side == "team" else info.opponent_of(subject)
                readings.append(
                    _read(
                        signal,
                        team,
                        metric,
                        series[signal.metric, team],
                        spans,
                        support_z,
                        contradiction_z,
                    )
                )
            families = sorted(
                {r.family for r in readings if r.status == "support" and r.independent}
            )
            assessments.append(
                PatternAssessment(
                    pattern=pattern.name,
                    concept=pattern.concept,
                    subject_team_id=subject,
                    readings=tuple(readings),
                    independent_families=tuple(families),
                )
            )
    if not assessments:
        return None
    return max(
        assessments,
        key=lambda a: (len(a.independent_families), len(a.supports), -len(a.contradictions)),
    )
