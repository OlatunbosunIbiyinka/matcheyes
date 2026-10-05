"""Temporal persistence: does a shift hold across its after-window, or is it one burst?

A 15-minute window difference can be produced by a three-minute spell (a run of corners, one
long attack). The after-window is split into consecutive sub-windows of `sub_window_bins`
minutes; each is compared with the same before-window:

* hold - directional statistic >= `hold_z`;
* against - directional statistic <= -`hold_z` (the metric is back on, or beyond, the other
  side of the baseline);
* neutral or unavailable otherwise.

Classification, in order:

* INDETERMINATE - fewer than two sub-windows could be measured;
* REVERSED - any sub-window is against the shift;
* SUSTAINED - at least two sub-windows hold, including the last measured one (the change has
  not decayed by the end of the window);
* TRANSIENT - otherwise (the change is concentrated in part of the window).

A shorter sub-window has a third of the samples, so each metric's minimum sample requirement is
divided by three for sub-windows. Thresholds: a sub-window holds at one standard error, a
deliberately low bar because it is a consistency check on an already-detected shift, not a new
detection; the rationale is in docs/contextual-evidence.md#persistence.

`continues` reports, without affecting the classification, whether the next sub-window after the
after-window still holds, when it exists within the same game-state regime.
"""

import math
from enum import StrEnum
from typing import Literal

from pydantic import Field

from matcheyes.analytics.baselines import Direction, Span
from matcheyes.analytics.changepoints import compare_spans
from matcheyes.analytics.context import MatchContext
from matcheyes.analytics.metrics import MetricSpec, Series
from matcheyes.domain.base import DomainModel

SubStatus = Literal["hold", "against", "neutral", "unavailable"]


class Persistence(StrEnum):
    SUSTAINED = "sustained"
    TRANSIENT = "transient"
    REVERSED = "reversed"
    INDETERMINATE = "indeterminate"


class SubWindow(DomainModel):
    span: Span
    statistic: float | None = Field(description="Directional: positive means as expected.")
    status: SubStatus


class PersistenceAssessment(DomainModel):
    persistence: Persistence
    sub_windows: tuple[SubWindow, ...]
    continues: bool | None = Field(description="None when no same-regime follow-on window exists.")


def sub_window_spec(spec: MetricSpec) -> MetricSpec:
    return spec.model_copy(update={"min_samples": math.ceil(spec.min_samples / 3)})


def _measure(
    series: Series, spec: MetricSpec, sign: int, before: Span, span: Span, hold_z: float
) -> SubWindow:
    result = compare_spans(series, spec, before, span)
    if result is None:
        return SubWindow(span=span, statistic=None, status="unavailable")
    directional = result.statistic * sign
    status: SubStatus = "neutral"
    if directional >= hold_z:
        status = "hold"
    elif directional <= -hold_z:
        status = "against"
    return SubWindow(span=span, statistic=directional, status=status)


def classify(sub_windows: tuple[SubWindow, ...]) -> Persistence:
    measured = [w for w in sub_windows if w.status != "unavailable"]
    if len(measured) < 2:
        return Persistence.INDETERMINATE
    if any(w.status == "against" for w in measured):
        return Persistence.REVERSED
    holds = sum(1 for w in measured if w.status == "hold")
    if holds >= 2 and measured[-1].status == "hold":
        return Persistence.SUSTAINED
    return Persistence.TRANSIENT


def assess_persistence(
    series: Series,
    spec: MetricSpec,
    direction: Direction,
    before: Span,
    after: Span,
    context: MatchContext,
    sub_window_bins: int,
    hold_z: float,
) -> PersistenceAssessment:
    sign = 1 if direction == "up" else -1
    small = sub_window_spec(spec)
    lo, hi = after
    spans = [(s, s + sub_window_bins) for s in range(lo, hi - sub_window_bins + 1, sub_window_bins)]
    windows = tuple(_measure(series, small, sign, before, span, hold_z) for span in spans)

    continues: bool | None = None
    follow = (hi, hi + sub_window_bins)
    if follow[1] <= len(context.bins) and context.regime_after(lo, follow[1])[1] == follow[1]:
        measured = _measure(series, small, sign, before, follow, hold_z)
        if measured.status != "unavailable":
            continues = measured.status == "hold"
    return PersistenceAssessment(
        persistence=classify(windows), sub_windows=windows, continues=continues
    )
