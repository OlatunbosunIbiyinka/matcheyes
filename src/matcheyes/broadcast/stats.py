"""Snapshot-anchored team statistics for the match centre (ADR-0016).

One `SnapshotStats` per canonical snapshot: eight existing Stage 2 team measures, cumulative over
that snapshot's own event prefix, with the sample behind each proportion and a display string
formatted here, so a surface shows numbers without computing them.

Validity follows the data and the analytics only. A prefix that breaks a data invariant is
`invalid` and carries no values; an analytics error is `unavailable` and carries no values. The
outcome of reasoning, verification or audit on the same snapshot plays no part: statistics are
facts about the prefix, not about any insight.

Only measures whose value on a prefix is never revised by later events are exposed: each one
counts single events by their own attributes. Possession-based measures (turnovers, possession
lengths) and measures that look ahead (pressure regain rate) are not.
"""

import json
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from matcheyes.analytics.metrics import METRIC_BY_NAME, Series, compute_series
from matcheyes.analytics.possessions import build_possessions
from matcheyes.analytics.summary import TeamSummary, team_summaries
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.match import ObservableMatch
from matcheyes.domain.time import Period
from matcheyes.ingestion.invariants import ValidationReport, ViolationCode, validate_prefix
from matcheyes.lifecycle.snapshot import Snapshot

STATS_VERSION: Literal["0.1.0"] = "0.1.0"
NOT_ENOUGH_DATA = "Not enough data"
ANALYTICS_ERROR = "analytics_error"
VIOLATION_CODES = frozenset(c.value for c in ViolationCode)


class StatsStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    """The prefix broke a data invariant: no values."""
    UNAVAILABLE = "unavailable"
    """The analytics did not complete: no values."""


class ValueStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    """Below the measure's minimum sample, or undefined (no denominator): no value."""


class Unit(StrEnum):
    COUNT = "count"
    SHARE = "share"
    RATIO = "ratio"


class Measure(DomainModel):
    key: str = Field(pattern=r"^[a-z_]{1,32}$")
    label: str
    unit: Unit
    min_sample: int = Field(ge=0, description="Smallest sample for which a value is shown.")
    sample_of: str | None = Field(description="What the sample counts; None if none is shown.")
    note: str | None = None


MEASURES: tuple[Measure, ...] = (
    Measure(key="shots", label="Shots", unit=Unit.COUNT, min_sample=0, sample_of=None),
    Measure(
        key="on_ball_share",
        label="Share of on-ball actions",
        unit=Unit.SHARE,
        min_sample=METRIC_BY_NAME["on_ball_share"].min_samples,
        sample_of="on-ball actions by both teams",
        note="Not possession time.",
    ),
    Measure(
        key="field_tilt",
        label="Field tilt",
        unit=Unit.SHARE,
        min_sample=METRIC_BY_NAME["field_tilt"].min_samples,
        sample_of="attacking-third on-ball actions by both teams",
        note="Share of on-ball actions in the attacking third.",
    ),
    Measure(
        key="pass_completion",
        label="Open-play pass completion",
        unit=Unit.SHARE,
        min_sample=METRIC_BY_NAME["pass_completion"].min_samples,
        sample_of="open-play passes",
    ),
    Measure(
        key="progressive_actions",
        label="Progressive passes and carries",
        unit=Unit.COUNT,
        min_sample=0,
        sample_of=None,
    ),
    Measure(
        key="high_regains",
        label="High regains",
        unit=Unit.COUNT,
        min_sample=0,
        sample_of=None,
        note="Ball won within 40 m of the opponent's goal.",
    ),
    Measure(key="pressures", label="Pressures", unit=Unit.COUNT, min_sample=0, sample_of=None),
    Measure(
        key="ppda",
        label="PPDA",
        unit=Unit.RATIO,
        min_sample=0,
        sample_of=None,
        note="Opponent open-play passes per defensive action; undefined without one.",
    ),
)
MEASURE_BY_KEY: dict[str, Measure] = {m.key: m for m in MEASURES}
COMPLEMENTARY: tuple[str, ...] = tuple(
    m.key for m in MEASURES if m.key in METRIC_BY_NAME and METRIC_BY_NAME[m.key].complementary
)
"""Shares of one pooled whole (the two teams' values sum to 1): their displays total 100%."""
COMPLEMENTARY_TOLERANCE = 1e-9
POOLED_SERIES: tuple[str, ...] = ("on_ball_share", "field_tilt")
"""The only Stage 2 series the statistics read (for the pooled samples); summaries give the rest."""


def _percent(value: float) -> Decimal:
    exact = Decimal(repr(float(value)))
    return (exact * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def display(unit: Unit, value: float | None) -> str:
    """The visible text of a value: whole-number percentages, one decimal for ratios, integers
    for counts, rounded half up from the shortest decimal form of the value."""
    if value is None:
        return NOT_ENOUGH_DATA
    if unit is Unit.COUNT:
        return str(value)
    if unit is Unit.SHARE:
        return f"{_percent(value)}%"
    return str(Decimal(repr(float(value))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def complementary_displays(home: float, away: float) -> tuple[str, str]:
    """The displays of two complementary shares, totalling exactly 100%: largest remainder with a
    tie going to the larger share. The larger share (home when equal) is rounded half up as in
    `display`, and the smaller shows 100 minus that, so a floating-point sum just below 1 cannot
    total 99%. The values themselves are never altered."""
    home_larger = home >= away
    shown = _percent(home if home_larger else away)
    rest = 100 - shown
    first, second = (shown, rest) if home_larger else (rest, shown)
    return f"{first}%", f"{second}%"


class TeamValue(DomainModel):
    value: int | float | None
    display: str
    sample: int | None = Field(ge=0)
    status: ValueStatus


class TeamStats(DomainModel):
    team_id: Identifier
    values: dict[str, TeamValue]


class SnapshotStats(DomainModel):
    """Statistics of one snapshot. The snapshot fields are those of the stream's clock tick."""

    stats_version: Literal["0.1.0"] = STATS_VERSION
    match_id: Identifier
    snapshot_id: Identifier
    watermark: int = Field(ge=1)
    period: Period
    minute: str = Field(description='The closed minute, e.g. "40\'".')
    as_of: str = Field(description='End of the closed minute on the match clock, e.g. "41\'".')
    clock_ms: int = Field(ge=0)
    status: StatsStatus
    reason: str | None = Field(max_length=400)
    measures: tuple[Measure, ...] = MEASURES
    teams: tuple[TeamStats, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.measures != MEASURES:
            raise ValueError("the measures are the fixed catalogue")
        if self.status is StatsStatus.VALID:
            if self.reason is not None:
                raise ValueError("valid statistics carry no reason")
            if len(self.teams) != 2 or self.teams[0].team_id == self.teams[1].team_id:
                raise ValueError("valid statistics cover both teams")
            for team in self.teams:
                _check_values(team)
            _check_complementary(self.teams[0], self.teams[1])
            return self
        if self.teams:
            raise ValueError(f"{self.status.value} statistics carry no values")
        if self.status is StatsStatus.UNAVAILABLE and self.reason != ANALYTICS_ERROR:
            raise ValueError(f"unavailable statistics give the reason {ANALYTICS_ERROR}")
        if self.status is StatsStatus.INVALID:
            codes = (self.reason or "").split(", ")
            if not self.reason or codes != sorted(set(codes)) or not set(codes) <= VIOLATION_CODES:
                raise ValueError("invalid statistics give the sorted violation codes")
        return self


def _check_values(team: TeamStats) -> None:
    if list(team.values) != list(MEASURE_BY_KEY):
        raise ValueError("every measure, in catalogue order")
    for key, v in team.values.items():
        measure = MEASURE_BY_KEY[key]
        if (v.value is None) != (v.status is ValueStatus.INSUFFICIENT_SAMPLE):
            raise ValueError(f"{key}: a value is missing exactly when the sample is insufficient")
        if measure.unit is Unit.COUNT and v.value is not None and type(v.value) is not int:
            raise ValueError(f"{key}: counts are integers")
        if measure.unit is Unit.SHARE and v.value is not None and not 0 <= v.value <= 1:
            raise ValueError(f"{key}: shares lie between 0 and 1")
        if (v.sample is not None) != (measure.sample_of is not None):
            raise ValueError(f"{key}: a sample is given exactly for measures that name one")
        if v.status is ValueStatus.OK and v.sample is not None and v.sample < measure.min_sample:
            raise ValueError(f"{key}: a value below the minimum sample is shown")
        if key in COMPLEMENTARY and v.value is not None:
            continue  # checked as a pair
        if v.display != display(measure.unit, v.value):
            raise ValueError(f"{key}: the display is not the formatted value")


def _check_complementary(home: TeamStats, away: TeamStats) -> None:
    for key in COMPLEMENTARY:
        a, b = home.values[key], away.values[key]
        if (a.value is None) != (b.value is None):
            raise ValueError(f"{key}: complementary shares are shown for both teams or neither")
        if a.value is None or b.value is None:
            continue
        if abs(a.value + b.value - 1) > COMPLEMENTARY_TOLERANCE:
            raise ValueError(f"{key}: complementary shares sum to 1")
        if (a.display, b.display) != complementary_displays(a.value, b.value):
            raise ValueError(f"{key}: the display is not the formatted value of the pair")


def team_analytics(
    match: ObservableMatch,
) -> tuple[tuple[TeamSummary, ...], dict[tuple[str, Identifier], Series]]:
    """The Stage 2 team summaries and series of a prefix, as `analyse_match` computes them."""
    events = match.events
    possessions = build_possessions(events)
    series = compute_series(events, possessions, Timeline(events), match.info.team_ids)
    return team_summaries(events, possessions, series, match.info), series


def _value(measure: Measure, value: float | None, sample: int | None) -> TeamValue:
    if value is not None and sample is not None and sample < measure.min_sample:
        value = None
    status = ValueStatus.OK if value is not None else ValueStatus.INSUFFICIENT_SAMPLE
    return TeamValue(
        value=value, display=display(measure.unit, value), sample=sample, status=status
    )


def _team(summary: TeamSummary, series: Mapping[tuple[str, Identifier], Series]) -> TeamStats:
    team = summary.team_id

    def pooled(name: str) -> int:
        return int(sum(series[name, team].denominators))

    raw: dict[str, tuple[float | None, int | None]] = {
        "shots": (summary.shots, None),
        "on_ball_share": (summary.on_ball_share, pooled(POOLED_SERIES[0])),
        "field_tilt": (summary.field_tilt, pooled(POOLED_SERIES[1])),
        "pass_completion": (summary.pass_completion, summary.open_play_passes),
        "progressive_actions": (summary.progressive_actions, None),
        "high_regains": (summary.high_regains, None),
        "pressures": (summary.pressures, None),
        "ppda": (summary.ppda, None),
    }
    return TeamStats(team_id=team, values={m.key: _value(m, *raw[m.key]) for m in MEASURES})


def _allocate(home: TeamStats, away: TeamStats) -> tuple[TeamStats, TeamStats]:
    """Replace the independent displays of each complementary pair with the allocated pair."""
    values = dict(home.values), dict(away.values)
    for key in COMPLEMENTARY:
        a, b = values[0][key], values[1][key]
        if a.value is not None and b.value is not None:
            shown = complementary_displays(a.value, b.value)
            values[0][key] = a.model_copy(update={"display": shown[0]})
            values[1][key] = b.model_copy(update={"display": shown[1]})
    return (
        home.model_copy(update={"values": values[0]}),
        away.model_copy(update={"values": values[1]}),
    )


def _anchor(snapshot: Snapshot) -> dict[str, Any]:
    header = snapshot.header
    return {
        "match_id": header.match_id,
        "snapshot_id": header.snapshot_id,
        "watermark": header.watermark,
        "period": header.as_of.period,
        "minute": header.label,
        "as_of": header.as_of.display_minute,
        "clock_ms": header.as_of.clock_ms,
    }


def unavailable_stats(snapshot: Snapshot) -> SnapshotStats:
    """The fixed no-values message for a snapshot whose statistics could not be built. Only the
    header is read, so the prefix may be missing or untrusted."""
    return SnapshotStats(
        **_anchor(snapshot), status=StatsStatus.UNAVAILABLE, reason=ANALYTICS_ERROR, teams=()
    )


def snapshot_stats(snapshot: Snapshot) -> SnapshotStats:
    """The statistics of one snapshot: a pure function of its header and prefix."""
    report = validate_prefix(snapshot.match)
    if not report.ok:
        return snapshot_stats_from(snapshot, report, (), {})
    try:
        summaries, series = team_analytics(snapshot.match)
    except Exception:  # any analytics failure: nothing is shown for this snapshot
        return unavailable_stats(snapshot)
    return snapshot_stats_from(snapshot, report, summaries, series)


def snapshot_stats_from(
    snapshot: Snapshot,
    validity: ValidationReport,
    summaries: Sequence[TeamSummary],
    series: Mapping[tuple[str, Identifier], Series],
) -> SnapshotStats:
    """The statistics of one snapshot from its prefix's validity report and Stage 2 team summaries
    and series, already computed on that same prefix (`summaries` and `series` are not read when
    the prefix is invalid). Only the snapshot's header is read."""
    fields = _anchor(snapshot)
    if not validity.ok:
        codes = ", ".join(sorted({v.code.value for v in validity.violations}))
        return SnapshotStats(**fields, status=StatsStatus.INVALID, reason=codes, teams=())
    try:
        home, away = _allocate(*(_team(s, series) for s in summaries))
        return SnapshotStats(**fields, status=StatsStatus.VALID, reason=None, teams=(home, away))
    except Exception:  # any analytics failure: nothing is shown for this snapshot
        return unavailable_stats(snapshot)


def stats_message(edition: int, stats: SnapshotStats) -> str:
    """The `data` of one SSE `stats` message: the edition, then the statistics."""
    payload = {"edition": edition, **stats.model_dump(mode="json")}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
