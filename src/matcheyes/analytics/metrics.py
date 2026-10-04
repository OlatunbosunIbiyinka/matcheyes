"""Per-team, per-minute metric series.

Every metric is defined from the perspective of one team and stored per minute bin as a
numerator and denominator (plus a sum of squares for means), so any window can be aggregated
exactly. Definitions are documented in docs/metrics.md.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import Field

from matcheyes.analytics.geometry import (
    HALFWAY_X,
    HIGH_REGAIN_MIN_X,
    enters_attacking_third,
    in_attacking_third,
    is_progressive,
    is_wide,
)
from matcheyes.analytics.possessions import (
    ON_BALL,
    EndReason,
    Possession,
    controlling_team,
)
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.events import (
    BallRecovery,
    Block,
    Carry,
    Clearance,
    DuelOutcome,
    Foul,
    Interception,
    MatchEvent,
    Pass,
    PassKind,
    PassOutcome,
    Pressure,
    Shot,
    Tackle,
)

PRESSURE_REGAIN_WINDOW_MS = 5_000


class MetricKind(StrEnum):
    COUNT = "count"
    """Events per minute. Numerator = events in the bin; denominator = 1 per bin."""
    PROPORTION = "proportion"
    """Successes over trials, pooled across a window."""
    MEAN = "mean"
    """Mean of a per-event measurement, pooled across a window."""


class MetricFamily(StrEnum):
    CONTROL = "control"
    TERRITORY = "territory"
    PRESSING = "pressing"
    DEFENDING = "defending"
    PROGRESSION = "progression"
    WIDTH = "width"
    PASSING = "passing"
    ATTACKING = "attacking"
    BALL_SECURITY = "ball_security"


class MetricSpec(DomainModel):
    name: str
    title: str
    kind: MetricKind
    family: MetricFamily
    unit: str
    complementary: bool = Field(
        description="The two teams' values sum to 1, so a rise for one is a fall for the other."
    )
    min_effect: float = Field(gt=0, description="Smallest window change worth reporting.")
    relative_effect: bool = Field(
        default=False, description="min_effect is a fraction of the larger window value."
    )
    min_samples: int = Field(
        default=0, ge=0, description="Minimum denominator per window (proportions, means)."
    )


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name="on_ball_share",
        title="share of on-ball actions",
        kind=MetricKind.PROPORTION,
        family=MetricFamily.CONTROL,
        unit="share",
        complementary=True,
        min_effect=0.08,
        min_samples=60,
    ),
    MetricSpec(
        name="field_tilt",
        title="field tilt (share of attacking-third actions)",
        kind=MetricKind.PROPORTION,
        family=MetricFamily.TERRITORY,
        unit="share",
        complementary=True,
        min_effect=0.12,
        min_samples=20,
    ),
    MetricSpec(
        name="high_regains",
        title="high regains",
        kind=MetricKind.COUNT,
        family=MetricFamily.PRESSING,
        unit="per minute",
        complementary=False,
        min_effect=0.4,
        relative_effect=True,
    ),
    MetricSpec(
        name="pressures",
        title="pressures",
        kind=MetricKind.COUNT,
        family=MetricFamily.PRESSING,
        unit="per minute",
        complementary=False,
        min_effect=0.3,
        relative_effect=True,
    ),
    MetricSpec(
        name="pressure_regain_rate",
        title="pressure regain rate",
        kind=MetricKind.PROPORTION,
        family=MetricFamily.PRESSING,
        unit="share",
        complementary=False,
        min_effect=0.10,
        min_samples=15,
    ),
    MetricSpec(
        name="defensive_action_height",
        title="defensive action height",
        kind=MetricKind.MEAN,
        family=MetricFamily.DEFENDING,
        unit="metres from own goal",
        complementary=False,
        min_effect=4.0,
        min_samples=10,
    ),
    MetricSpec(
        name="progressive_actions",
        title="progressive passes and carries",
        kind=MetricKind.COUNT,
        family=MetricFamily.PROGRESSION,
        unit="per minute",
        complementary=False,
        min_effect=0.3,
        relative_effect=True,
    ),
    MetricSpec(
        name="attacking_third_entries",
        title="attacking-third entries",
        kind=MetricKind.COUNT,
        family=MetricFamily.PROGRESSION,
        unit="per minute",
        complementary=False,
        min_effect=0.3,
        relative_effect=True,
    ),
    MetricSpec(
        name="wide_share",
        title="share of completed passes into wide areas of the opponent half",
        kind=MetricKind.PROPORTION,
        family=MetricFamily.WIDTH,
        unit="share",
        complementary=False,
        min_effect=0.10,
        min_samples=12,
    ),
    MetricSpec(
        name="pass_completion",
        title="open-play pass completion",
        kind=MetricKind.PROPORTION,
        family=MetricFamily.PASSING,
        unit="share",
        complementary=False,
        min_effect=0.06,
        min_samples=40,
    ),
    MetricSpec(
        name="shots",
        title="shots",
        kind=MetricKind.COUNT,
        family=MetricFamily.ATTACKING,
        unit="per minute",
        complementary=False,
        min_effect=0.5,
        relative_effect=True,
    ),
    MetricSpec(
        name="turnovers",
        title="possessions lost in open play",
        kind=MetricKind.COUNT,
        family=MetricFamily.BALL_SECURITY,
        unit="per minute",
        complementary=False,
        min_effect=0.3,
        relative_effect=True,
    ),
)
METRIC_BY_NAME: dict[str, MetricSpec] = {m.name: m for m in METRICS}


class Series(DomainModel):
    """One team's metric, bin by bin."""

    metric: str
    team_id: Identifier
    numerators: tuple[float, ...]
    denominators: tuple[float, ...]
    squares: tuple[float, ...] = Field(description="Sum of squared samples (MEAN metrics only).")
    event_ids: tuple[tuple[Identifier, ...], ...] = Field(
        description="Per bin, the events counted in the numerator (every sample for means)."
    )

    def value(self, index: int) -> float | None:
        den = self.denominators[index]
        return self.numerators[index] / den if den else None

    def events_between(self, start_bin: int, end_bin: int) -> tuple[Identifier, ...]:
        return tuple(e for ids in self.event_ids[start_bin:end_bin] for e in ids)


@dataclass
class _Accumulator:
    bins: int
    num: list[float] = field(init=False)
    den: list[float] = field(init=False)
    sq: list[float] = field(init=False)
    ids: list[list[Identifier]] = field(init=False)

    def __post_init__(self) -> None:
        self.num = [0.0] * self.bins
        self.den = [0.0] * self.bins
        self.sq = [0.0] * self.bins
        self.ids = [[] for _ in range(self.bins)]

    def add(self, index: int, event_id: Identifier, hit: bool = True) -> None:
        self.num[index] += 1.0 if hit else 0.0
        self.den[index] += 1.0
        if hit:
            self.ids[index].append(event_id)

    def sample(self, index: int, event_id: Identifier, value: float) -> None:
        self.num[index] += value
        self.den[index] += 1.0
        self.sq[index] += value * value
        self.ids[index].append(event_id)


def is_regain(event: MatchEvent) -> bool:
    if isinstance(event, Interception | BallRecovery):
        return True
    return isinstance(event, Tackle) and event.outcome is DuelOutcome.WON


DEFENSIVE_ACTIONS = (Tackle, Interception, BallRecovery, Block, Clearance, Foul)
ON_PITCH = (*ON_BALL, Pressure, *DEFENSIVE_ACTIONS)


def _next_control(events: Sequence[MatchEvent]) -> list[tuple[Identifier, int, int] | None]:
    """For each event, the (team, period, clock_ms) of the next ball-controlling event after it."""
    result: list[tuple[Identifier, int, int] | None] = [None] * len(events)
    upcoming: tuple[Identifier, int, int] | None = None
    for i in range(len(events) - 1, -1, -1):
        result[i] = upcoming
        team = controlling_team(events[i])
        if team is not None:
            upcoming = (team, events[i].period, events[i].clock_ms)
    return result


def pressure_won_ball(pressure: Pressure, next_control: tuple[Identifier, int, int] | None) -> bool:
    """The pressing team is the next to control the ball, within 5 seconds."""
    if next_control is None:
        return False
    team, period, clock_ms = next_control
    return (
        team == pressure.team_id
        and period == pressure.period
        and clock_ms - pressure.clock_ms <= PRESSURE_REGAIN_WINDOW_MS
    )


def compute_series(
    events: Sequence[MatchEvent],
    possessions: Sequence[Possession],
    timeline: Timeline,
    team_ids: tuple[Identifier, Identifier],
) -> dict[tuple[str, Identifier], Series]:
    bins = len(timeline)
    acc = {(m.name, t): _Accumulator(bins) for m in METRICS for t in team_ids}
    next_control = _next_control(events)

    def both(metric: str, index: int, event_id: Identifier, team: Identifier) -> None:
        for t in team_ids:
            acc[metric, t].add(index, event_id, hit=t == team)

    for i, event in enumerate(events):
        if not isinstance(event, ON_PITCH):
            continue
        b = timeline.index_of_event(event)
        team, eid = event.team_id, event.event_id
        if isinstance(event, ON_BALL):
            both("on_ball_share", b, eid, team)
            if in_attacking_third(event.location):
                both("field_tilt", b, eid, team)
        if is_regain(event) and event.location.x >= HIGH_REGAIN_MIN_X:
            acc["high_regains", team].add(b, eid)
        if isinstance(event, Pressure):
            acc["pressures", team].add(b, eid)
            won = pressure_won_ball(event, next_control[i])
            acc["pressure_regain_rate", team].add(b, eid, hit=won)
        if isinstance(event, DEFENSIVE_ACTIONS):
            acc["defensive_action_height", team].sample(b, eid, event.location.x)
        if isinstance(event, Shot):
            acc["shots", team].add(b, eid)
        if isinstance(event, Pass) and event.kind is PassKind.OPEN_PLAY:
            complete = event.outcome is PassOutcome.COMPLETE
            acc["pass_completion", team].add(b, eid, hit=complete)
            if complete and event.end_location.x >= HALFWAY_X:
                acc["wide_share", team].add(b, eid, hit=is_wide(event.end_location))
        moved = isinstance(event, Carry) or (
            isinstance(event, Pass) and event.outcome is PassOutcome.COMPLETE
        )
        if moved and isinstance(event, Pass | Carry):
            if is_progressive(event.location, event.end_location):
                acc["progressive_actions", team].add(b, eid)
            if enters_attacking_third(event.location, event.end_location):
                acc["attacking_third_entries", team].add(b, eid)

    for possession in possessions:
        if possession.end_reason is EndReason.LOST_IN_PLAY:
            b = timeline.index_of(possession.period, possession.end_clock_ms)
            acc["turnovers", possession.team_id].add(b, possession.event_ids[-1])

    for spec in METRICS:
        if spec.kind is MetricKind.COUNT:
            for t in team_ids:
                acc[spec.name, t].den = [1.0] * bins

    return {
        key: Series(
            metric=key[0],
            team_id=key[1],
            numerators=tuple(a.num),
            denominators=tuple(a.den),
            squares=tuple(a.sq),
            event_ids=tuple(tuple(ids) for ids in a.ids),
        )
        for key, a in acc.items()
    }
