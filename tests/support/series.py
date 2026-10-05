"""Hand-built metric series and match contexts for Stage 3 unit tests.

Every series carries synthetic event IDs (one per numerator unit) so traceability can be checked.
"""

from collections.abc import Sequence

from matcheyes.analytics.context import MatchContext, build_context
from matcheyes.analytics.metrics import METRICS, MetricKind, Series
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.events import (
    BodyPart,
    Card,
    CardType,
    PeriodEnd,
    PeriodStart,
    Shot,
    ShotKind,
    ShotOutcome,
)
from tests.support.builders import EventStream, at, match_info, pid

BINS = 60
TRIALS = 20.0
SAMPLES = 4.0
MEAN_VARIANCE = 100.0


def _ids(team: str, metric: str, b: int, count: float) -> tuple[str, ...]:
    return tuple(f"{team}-{metric}-{b}-{k}" for k in range(max(1, round(count))))


def series_from(
    metric: str, team: str, values: Sequence[float], trials: Sequence[float] | None = None
) -> Series:
    """COUNT: values per bin. PROPORTION: success rate per bin over `trials` (default 20).
    MEAN: mean per bin over 4 samples with variance 100."""
    kind = next(m.kind for m in METRICS if m.name == metric)
    n = len(values)
    if kind is MetricKind.COUNT:
        den = [1.0] * n
        num = list(values)
        squares = [0.0] * n
    elif kind is MetricKind.PROPORTION:
        den = list(trials) if trials is not None else [TRIALS] * n
        num = [v * d for v, d in zip(values, den, strict=True)]
        squares = [0.0] * n
    else:
        den = [SAMPLES] * n
        num = [v * SAMPLES for v in values]
        squares = [SAMPLES * (v * v + MEAN_VARIANCE) for v in values]
    return Series(
        metric=metric,
        team_id=team,
        numerators=tuple(num),
        denominators=tuple(den),
        squares=tuple(squares),
        event_ids=tuple(_ids(team, metric, b, x) if x else () for b, x in enumerate(num)),
    )


def step(before: float, after: float, at_bin: int = 30, n: int = BINS) -> list[float]:
    return [before if b < at_bin else after for b in range(n)]


FLAT = {MetricKind.COUNT: 2.0, MetricKind.PROPORTION: 0.5, MetricKind.MEAN: 40.0}


def flat_series(teams: tuple[str, str], n: int = BINS) -> dict[tuple[str, str], Series]:
    return {(m.name, t): series_from(m.name, t, [FLAT[m.kind]] * n) for m in METRICS for t in teams}


def scripted_context(
    goals: Sequence[tuple[int, str]] = (),
    reds: Sequence[tuple[int, str]] = (),
    n: int = BINS,
) -> MatchContext:
    """A single-period match of n minute bins with goals and red cards at the given minutes."""
    s = EventStream()
    s.add(PeriodStart, 1, 0)
    timed: list[tuple[int, str, str]] = [(m, "goal", t) for m, t in goals]
    timed += [(m, "red", t) for m, t in reds]
    for minute, kind, team in sorted(timed):
        if kind == "goal":
            s.add(
                Shot,
                1,
                minute * 60 + 30,
                team_id=team,
                player_id=pid(team, 9),
                location=at(95, 34),
                end_location=at(105, 34),
                outcome=ShotOutcome.GOAL,
                kind=ShotKind.OPEN_PLAY,
                body_part=BodyPart.RIGHT_FOOT,
            )
        else:
            s.add(
                Card, 1, minute * 60 + 30, team_id=team, player_id=pid(team, 4), card=CardType.RED
            )
    s.add(PeriodEnd, 1, (n - 1) * 60 + 30)
    return build_context(s.events, Timeline(s.events), match_info())
