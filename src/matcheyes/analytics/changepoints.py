"""Shift detection: where does a team's metric change between two adjacent windows?

At every minute boundary t the baseline window [t - B, t) is compared with [t, t + W); a baseline
longer than the after-window lowers the comparison's variance. Near kick-off the baseline is
whatever play exists (at least W minutes). The statistic is
the difference in window values over its standard error, with each window's variance taken
as the larger of:

* COUNT - the sample variance of per-minute counts, or the Poisson variance (the mean);
* PROPORTION - the binomial variance, or a cluster-robust variance with minutes as clusters;
* MEAN - the per-event sample variance (floored), or the same cluster-robust variance.

A boundary is a candidate when |statistic| reaches the threshold *and* the change is large
enough to matter in football terms (each metric's `min_effect`). Candidates are thinned by
non-maximum suppression so one change yields one shift. A shift says only that the metric
moved; it says nothing about why.
"""

import math
from collections.abc import Sequence

from pydantic import Field

from matcheyes.analytics.metrics import METRIC_BY_NAME, MetricKind, MetricSpec, Series
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier

MIN_MEAN_VARIANCE = 1.0
"""Floor on per-sample variance for MEAN metrics (m^2), to stop tiny samples looking certain."""


class DetectionConfig(DomainModel):
    """Defaults chosen on development seeds only (docs/decisions/0008)."""

    window_bins: int = Field(default=15, ge=3, description="Length of the after-window.")
    baseline_bins: int = Field(
        default=30,
        ge=3,
        description="Longest before-window; early in a match it shrinks to the play available, "
        "but never below window_bins.",
    )
    z_threshold: float = Field(default=2.0, gt=0)
    suppression_bins: int = Field(default=15, ge=1)


class WindowValue(DomainModel):
    start_bin: int = Field(ge=0)
    end_bin: int = Field(ge=0, description="Exclusive.")
    value: float
    sample: float = Field(ge=0, description="Minutes (counts), trials or samples (otherwise).")


class Shift(DomainModel):
    metric: str
    team_id: Identifier
    bin_index: int = Field(ge=0, description="First bin of the after-window.")
    before: WindowValue
    after: WindowValue
    statistic: float

    @property
    def delta(self) -> float:
        return self.after.value - self.before.value

    @property
    def direction(self) -> str:
        return "up" if self.delta > 0 else "down"


def _window(series: Series, spec: MetricSpec, lo: int, hi: int) -> tuple[WindowValue, float] | None:
    """Window value plus the variance of that value, or None if the window is too thin."""
    num = series.numerators[lo:hi]
    den = series.denominators[lo:hi]
    trials = sum(den)
    if trials <= 0 or (spec.kind is not MetricKind.COUNT and trials < spec.min_samples):
        return None
    value = sum(num) / trials
    window = WindowValue(start_bin=lo, end_bin=hi, value=value, sample=trials)
    if spec.kind is MetricKind.COUNT:
        n = len(num)
        sample_var = sum((x - value) ** 2 for x in num) / (n - 1) if n > 1 else 0.0
        return window, max(sample_var, value) / n
    if spec.kind is MetricKind.PROPORTION:
        independent = value * (1 - value) / trials
    else:
        spread = sum(series.squares[lo:hi]) / trials - value * value
        independent = max(spread * trials / max(trials - 1, 1), MIN_MEAN_VARIANCE) / trials
    return window, max(independent, _clustered_variance(num, den, value))


def _clustered_variance(num: Sequence[float], den: Sequence[float], value: float) -> float:
    """Variance of a pooled ratio treating each minute as a cluster.

    Actions within a possession are not independent (one long spell in the final third
    contributes many correlated "attacking-third actions"), so the independent-sample variance
    understates uncertainty. Minute clusters absorb that dependence.
    """
    used = [(y, n) for y, n in zip(num, den, strict=True) if n > 0]
    m = len(used)
    if m < 2:
        return 0.0
    mean_n = sum(n for _, n in used) / m
    residuals = sum((y - value * n) ** 2 for y, n in used)
    return residuals / (m * (m - 1) * mean_n * mean_n)


def compare(
    series: Series, spec: MetricSpec, t: int, width: int, baseline: int | None = None
) -> Shift | None:
    before = _window(series, spec, max(0, t - (baseline or width)), t)
    after = _window(series, spec, t, t + width)
    if before is None or after is None:
        return None
    (b, var_b), (a, var_a) = before, after
    se = math.sqrt(var_a + var_b)
    if se <= 0:
        return None
    return Shift(
        metric=spec.name,
        team_id=series.team_id,
        bin_index=t,
        before=b,
        after=a,
        statistic=(a.value - b.value) / se,
    )


def is_material(spec: MetricSpec, shift: Shift) -> bool:
    floor = spec.min_effect
    if spec.relative_effect:
        floor *= max(abs(shift.before.value), abs(shift.after.value))
    return abs(shift.delta) >= floor


def scan(series: Series, config: DetectionConfig) -> list[Shift]:
    """All boundaries' comparisons for one series, in time order (no thresholding)."""
    spec = METRIC_BY_NAME[series.metric]
    w, base = config.window_bins, config.baseline_bins
    results = []
    for t in range(min(w, base), len(series.numerators) - w + 1):
        shift = compare(series, spec, t, w, base)
        if shift is not None:
            results.append(shift)
    return results


def detect_shifts(series: Series, config: DetectionConfig) -> list[Shift]:
    spec = METRIC_BY_NAME[series.metric]
    candidates = [
        s
        for s in scan(series, config)
        if abs(s.statistic) >= config.z_threshold
        and is_material(spec, s)
        and not (spec.complementary and s.delta < 0)
    ]
    accepted: list[Shift] = []
    for shift in sorted(candidates, key=lambda s: (-abs(s.statistic), s.bin_index)):
        if all(abs(shift.bin_index - a.bin_index) > config.suppression_bins for a in accepted):
            accepted.append(shift)
    return sorted(accepted, key=lambda s: s.bin_index)
