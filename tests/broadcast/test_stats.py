"""Snapshot statistics (ADR-0016): contract, validity, samples, reproducibility and stability."""

import json
from collections.abc import Sequence
from functools import cache
from typing import Any

import pytest
from pydantic import ValidationError

from matcheyes.analytics.analysis import analyse_match
from matcheyes.analytics.timeline import Timeline
from matcheyes.api.live import clock_tick
from matcheyes.broadcast import stats as stats_module
from matcheyes.broadcast.stats import (
    ANALYTICS_ERROR,
    MEASURES,
    NOT_ENOUGH_DATA,
    SnapshotStats,
    StatsStatus,
    Unit,
    ValueStatus,
    display,
    snapshot_stats,
    stats_message,
    team_analytics,
)
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle.contracts import SnapshotOutcome
from matcheyes.lifecycle.engine import LifecycleEngine
from matcheyes.lifecycle.evaluate import SnapshotEvaluation
from matcheyes.lifecycle.snapshot import Snapshot, build_snapshot, prefix_digest, snapshot_schedule
from tests.lifecycle.support import SCENARIO, engine_for, in_progress
from tests.synth.generated import generated

KEYS = (
    "shots",
    "on_ball_share",
    "field_tilt",
    "pass_completion",
    "progressive_actions",
    "high_regains",
    "pressures",
    "ppda",
)
SUMMARY_FIELD = {key: key for key in KEYS}
POOLED = {"on_ball_share": "on_ball_share", "field_tilt": "field_tilt"}
SERIES_BACKED = ("shots", "on_ball_share", "field_tilt", "pass_completion")
SERIES_BACKED += ("progressive_actions", "high_regains", "pressures")


def snapshots(match: ObservableMatch) -> list[Snapshot]:
    events = match.events
    return [
        build_snapshot(match.info, events, entry, prefix_digest(events[: entry.watermark]))
        for entry in snapshot_schedule(events)
    ]


@cache
def full_match() -> ObservableMatch:
    return generated(SCENARIO).observable


@cache
def full_snapshots() -> tuple[Snapshot, ...]:
    return tuple(snapshots(full_match()))


@cache
def partial_stats() -> tuple[SnapshotStats, ...]:
    return tuple(snapshot_stats(s) for s in snapshots(in_progress()))


def corrupted(index: int = 4) -> ObservableMatch:
    match = in_progress()
    events = list(match.events)
    events[index] = events[index].model_copy(update={"player_id": "nobody-99"})
    return match.model_copy(update={"events": tuple(events)})


# --- the contract --------------------------------------------------------------------------


def test_the_catalogue_is_the_eight_approved_measures_with_existing_minimum_samples() -> None:
    assert tuple(m.key for m in MEASURES) == KEYS
    samples = {m.key: m.min_sample for m in MEASURES}
    assert samples == {k: 0 for k in KEYS} | {
        "on_ball_share": 60,
        "field_tilt": 20,
        "pass_completion": 40,
    }
    for excluded in ("goals", "xg", "shots_on_target", "possession", "momentum", "turnovers"):
        assert excluded not in samples


def test_a_valid_snapshot_reports_every_measure_for_both_teams_in_team_sheet_order() -> None:
    latest = partial_stats()[-1]
    assert latest.status is StatsStatus.VALID and latest.reason is None
    assert tuple(t.team_id for t in latest.teams) == in_progress().info.team_ids
    for team in latest.teams:
        assert tuple(team.values) == KEYS
        assert all(v.status is ValueStatus.OK for v in team.values.values())


@pytest.mark.parametrize(
    ("unit", "value", "text"),
    [
        (Unit.SHARE, 0.5853658536585366, "59%"),
        (Unit.SHARE, 0.4146341463414634, "41%"),
        (Unit.SHARE, 0.585, "59%"),
        (Unit.SHARE, 0.125, "13%"),
        (Unit.SHARE, 0.0, "0%"),
        (Unit.SHARE, 1.0, "100%"),
        (Unit.RATIO, 5.3125, "5.3"),
        (Unit.RATIO, 10.5, "10.5"),
        (Unit.RATIO, 15.0, "15.0"),
        (Unit.RATIO, 0.25, "0.3"),
        (Unit.COUNT, 5, "5"),
        (Unit.COUNT, 0, "0"),
        (Unit.SHARE, None, NOT_ENOUGH_DATA),
        (Unit.RATIO, None, NOT_ENOUGH_DATA),
    ],
)
def test_visible_values_are_formatted_on_the_server(
    unit: Unit, value: float | None, text: str
) -> None:
    assert display(unit, value) == text


def test_the_wire_message_has_the_clock_ticks_snapshot_fields_and_round_trips() -> None:
    snapshot = snapshots(in_progress())[-1]
    stats = snapshot_stats(snapshot)
    payload = json.loads(stats_message(3, stats))
    assert list(payload) == [
        "edition",
        "stats_version",
        "match_id",
        "snapshot_id",
        "watermark",
        "period",
        "minute",
        "as_of",
        "clock_ms",
        "status",
        "reason",
        "measures",
        "teams",
    ]
    tick = json.loads(clock_tick(3, snapshot.header))
    assert {k: payload[k] for k in tick} == tick
    assert payload["stats_version"] == "0.1.0"
    assert SnapshotStats.model_validate({k: v for k, v in payload.items() if k != "edition"}) == (
        stats
    )
    assert stats_message(3, stats) == stats_message(3, snapshot_stats(snapshot))


# --- small samples ---------------------------------------------------------------------------


def test_a_proportion_is_shown_only_from_its_minimum_sample() -> None:
    minimum = {m.key: m.min_sample for m in MEASURES}
    seen_insufficient = set()
    for stats in partial_stats():
        for team in stats.teams:
            for key in ("on_ball_share", "field_tilt", "pass_completion"):
                v = team.values[key]
                assert v.sample is not None
                shown = v.sample >= minimum[key] and v.value is not None
                assert (v.status is ValueStatus.OK) == shown, (stats.minute, key)
                if not shown:
                    seen_insufficient.add(key)
                    assert v.value is None and v.display == NOT_ENOUGH_DATA
    assert seen_insufficient == {"on_ball_share", "field_tilt", "pass_completion"}


def test_an_undefined_ppda_is_not_enough_data_and_counts_are_always_shown() -> None:
    undefined = 0
    for snapshot, stats in zip(snapshots(in_progress()), partial_stats(), strict=True):
        summaries, _ = team_analytics(snapshot.match)
        for summary, team in zip(summaries, stats.teams, strict=True):
            ppda = team.values["ppda"]
            assert (summary.ppda is None) == (ppda.status is ValueStatus.INSUFFICIENT_SAMPLE)
            undefined += summary.ppda is None
            for key in ("shots", "progressive_actions", "high_regains", "pressures"):
                assert team.values[key].status is ValueStatus.OK
                assert team.values[key].sample is None
    assert undefined > 0


# --- invalid and unavailable ----------------------------------------------------------------


def test_an_invalid_prefix_publishes_no_values() -> None:
    stats = snapshot_stats(snapshots(corrupted())[0])
    assert stats.status is StatsStatus.INVALID
    assert stats.reason == "player_not_on_pitch" and stats.teams == ()


def test_invalidity_persists_and_matches_the_lifecycle_outcome() -> None:
    match = corrupted(index=300)
    stats = [snapshot_stats(s) for s in snapshots(match)]
    engine = engine_for(match.events)
    outcomes = [r.outcome for r in engine.state.snapshots]
    assert [s.snapshot_id for s in stats] == [r.header.snapshot_id for r in engine.state.snapshots]
    invalid = [s.status is StatsStatus.INVALID for s in stats]
    assert invalid == [o is SnapshotOutcome.INVALID for o in outcomes]
    first = invalid.index(True)
    assert first > 0 and all(invalid[first:]) and not any(invalid[:first])


def test_an_analytics_failure_is_unavailable_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: Any) -> None:
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(stats_module, "team_summaries", broken)
    stats = snapshot_stats(snapshots(in_progress())[-1])
    assert stats.status is StatsStatus.UNAVAILABLE
    assert stats.reason == ANALYTICS_ERROR and stats.teams == ()
    assert "boom" not in stats.model_dump_json() and "ZeroDivision" not in stats.model_dump_json()


def test_statistics_do_not_depend_on_reasoning_success() -> None:
    match = in_progress()

    def reasoning_fails(snapshot: Snapshot) -> SnapshotEvaluation:
        return SnapshotEvaluation(
            header=snapshot.header, outcome=SnapshotOutcome.FAILED, failure="RuntimeError"
        )

    engine = LifecycleEngine(match.info, reasoning_fails)
    for event in match.events:
        engine.ingest(event)
    latest = engine.latest_snapshot()
    assert latest is not None
    assert {r.outcome for r in engine.state.snapshots} == {SnapshotOutcome.FAILED}
    assert snapshot_stats(latest) == partial_stats()[-1]


# --- contract invariants -----------------------------------------------------------------------


def _valid_payload() -> dict[str, Any]:
    return partial_stats()[-1].model_dump(mode="json")


def _edit(path: Sequence[str | int], value: object) -> dict[str, Any]:
    payload = _valid_payload()
    target: Any = payload
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return payload


def _values(team: int) -> Sequence[str | int]:
    return ("teams", team, "values")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_edit((*_values(0), "shots", "display"), "6"), "not the formatted value"),
        (_edit((*_values(0), "on_ball_share", "display"), "60%"), "not the formatted value"),
        (_edit((*_values(0), "ppda", "status"), "insufficient_sample"), "missing exactly"),
        (_edit((*_values(0), "pass_completion", "sample"), 39), "below the minimum sample"),
        (_edit((*_values(0), "shots", "sample"), 4), "exactly for measures that name one"),
        (_edit((*_values(0), "shots", "value"), 5.0), "counts are integers"),
        (_edit((*_values(0), "field_tilt", "value"), 1.2), "between 0 and 1"),
        (_edit(("teams",), []), "both teams"),
        (_edit(("reason",), "player_not_on_pitch"), "carry no reason"),
        (_edit(("status",), "invalid"), "carry no values"),
        (_edit(("measures",), []), "fixed catalogue"),
        (_edit(("stats_version",), "0.2.0"), "0.1.0"),
    ],
)
def test_contract_invariants(payload: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SnapshotStats.model_validate(payload)


def test_values_must_cover_every_measure_in_order_for_two_distinct_teams() -> None:
    payload = _valid_payload()
    del payload["teams"][0]["values"]["ppda"]
    with pytest.raises(ValidationError, match="catalogue order"):
        SnapshotStats.model_validate(payload)
    payload = _valid_payload()
    payload["teams"][1]["team_id"] = payload["teams"][0]["team_id"]
    with pytest.raises(ValidationError, match="both teams"):
        SnapshotStats.model_validate(payload)


@pytest.mark.parametrize(
    ("status", "reason", "message"),
    [
        ("invalid", None, "sorted violation codes"),
        ("invalid", "not_a_code", "sorted violation codes"),
        ("invalid", "unknown_team, player_not_on_pitch", "sorted violation codes"),
        ("unavailable", "RuntimeError", ANALYTICS_ERROR),
    ],
)
def test_statuses_without_values_give_a_closed_reason(
    status: str, reason: str | None, message: str
) -> None:
    payload = _valid_payload() | {"status": status, "reason": reason, "teams": []}
    with pytest.raises(ValidationError, match=message):
        SnapshotStats.model_validate(payload)


# --- reproducibility, parity and stability -----------------------------------------------------


def test_statistics_are_a_pure_function_of_the_snapshot() -> None:
    again = tuple(snapshot_stats(s) for s in snapshots(in_progress()))
    assert again == partial_stats()
    engine = engine_for(in_progress().events)
    assert [s.snapshot_id for s in partial_stats()] == [
        r.header.snapshot_id for r in engine.state.snapshots
    ]


def test_statistics_equal_stage2_on_every_snapshot() -> None:
    for snapshot, stats in zip(snapshots(in_progress()), partial_stats(), strict=True):
        analysis = analyse_match(snapshot.match)
        series = {(s.metric, s.team_id): s for s in analysis.series}
        for summary, team in zip(analysis.summaries, stats.teams, strict=True):
            assert team.team_id == summary.team_id
            for key in KEYS:
                v = team.values[key]
                if v.status is ValueStatus.OK:
                    assert v.value == getattr(summary, SUMMARY_FIELD[key]), (stats.minute, key)
            for key, name in POOLED.items():
                assert team.values[key].sample == sum(series[name, team.team_id].denominators)
            assert team.values["pass_completion"].sample == summary.open_play_passes


def test_the_final_snapshot_equals_the_full_match_summary() -> None:
    match = full_match()
    final = snapshot_stats(full_snapshots()[-1])
    assert final.watermark == len(match.events) and final.status is StatsStatus.VALID
    for summary, team in zip(analyse_match(match).summaries, final.teams, strict=True):
        for key in KEYS:
            assert team.values[key].value == getattr(summary, SUMMARY_FIELD[key]), key


def _bin_totals(match: ObservableMatch, bins: int | None = None) -> dict[tuple[str, str], tuple]:
    _, series = team_analytics(match)
    end = bins if bins is not None else len(Timeline(match.events))
    return {
        key: (round(sum(s.numerators[:end]), 9), round(sum(s.denominators[:end]), 9))
        for key, s in series.items()
    }


def test_exposed_measures_are_never_revised_by_later_events() -> None:
    match = full_match()
    revised: dict[str, int] = {}
    for snapshot in full_snapshots():
        bins = len(Timeline(snapshot.match.events))
        later = _bin_totals(match, bins)
        for key, totals in _bin_totals(snapshot.match).items():
            if later[key] != totals:
                revised[key[0]] = revised.get(key[0], 0) + 1
    assert not set(revised) & set(SERIES_BACKED), revised
    assert set(revised) == {"turnovers"}, revised


def test_turnovers_and_pressure_regain_rate_are_not_exposed() -> None:
    exposed = json.dumps([m.model_dump() for m in MEASURES])
    for name in ("turnovers", "pressure_regain_rate", "possessions", "mean_possession_s"):
        assert name not in exposed
