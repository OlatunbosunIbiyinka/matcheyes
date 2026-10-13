"""Complementary shares (ADR-0016): the two teams' displays of a pooled share total exactly 100%."""

from functools import cache
from typing import Any

import pytest
from pydantic import ValidationError

from matcheyes.api.catalog import load_catalog
from matcheyes.broadcast.stats import (
    COMPLEMENTARY,
    NOT_ENOUGH_DATA,
    SnapshotStats,
    StatsStatus,
    Unit,
    ValueStatus,
    complementary_displays,
    display,
    snapshot_stats,
    team_analytics,
)
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle.snapshot import Snapshot
from tests.broadcast.test_stats import full_match, snapshots
from tests.support.imports import SRC_ROOT

DEMO = "m-b71a80f94d25"
TABLE = [
    ((0.505, 0.495), ("51%", "49%")),
    ((0.125, 0.875), ("12%", "88%")),
    ((0.585, 0.415), ("59%", "41%")),
    ((0.584, 0.416), ("58%", "42%")),
    ((0.5, 0.5), ("50%", "50%")),
    ((0.995, 0.005), ("100%", "0%")),
    ((1.0, 0.0), ("100%", "0%")),
    ((0.0, 1.0), ("0%", "100%")),
    ((0.495, 0.505), ("49%", "51%")),
]


def _total(pair: tuple[str, str]) -> int:
    return sum(int(d.rstrip("%")) for d in pair)


@cache
def demo_match() -> ObservableMatch:
    return load_catalog(SRC_ROOT.parent / "deploy" / "matches")[DEMO]


@cache
def real_snapshots() -> tuple[Snapshot, ...]:
    return tuple(s for m in (full_match(), demo_match()) for s in snapshots(m))


@cache
def real_stats() -> tuple[SnapshotStats, ...]:
    return tuple(snapshot_stats(s) for s in real_snapshots())


def _edited(stats: SnapshotStats, key: str, home: dict[str, Any], away: dict[str, Any]) -> Any:
    data = stats.model_dump(mode="json")
    data["teams"][0]["values"][key].update(home)
    data["teams"][1]["values"][key].update(away)
    return data


def test_only_on_ball_share_and_field_tilt_are_complementary() -> None:
    assert COMPLEMENTARY == ("on_ball_share", "field_tilt")


@pytest.mark.parametrize(("values", "shown"), TABLE)
def test_the_boundary_table_is_allocated_by_largest_remainder(
    values: tuple[float, float], shown: tuple[str, str]
) -> None:
    assert complementary_displays(*values) == shown


def test_independent_rounding_would_total_101_on_the_boundary_cases() -> None:
    for home, away in ((0.505, 0.495), (0.125, 0.875), (0.995, 0.005)):
        assert _total((display(Unit.SHARE, home), display(Unit.SHARE, away))) == 101


def test_pass_completion_still_rounds_independently() -> None:
    assert (display(Unit.SHARE, 0.505), display(Unit.SHARE, 0.495)) == ("51%", "50%")


def test_every_pooled_split_totals_100_and_the_larger_share_rounds_as_usual() -> None:
    for whole in range(1, 241):
        for part in range(whole + 1):
            home = part / whole
            away = (whole - part) / whole
            pair = complementary_displays(home, away)
            assert _total(pair) == 100, (part, whole)
            larger = 0 if home >= away else 1
            assert pair[larger] == display(Unit.SHARE, max(home, away)), (part, whole)


def test_a_floating_point_sum_just_below_one_still_totals_100() -> None:
    home = 0.1 + 0.2 + 0.3  # 0.6000000000000001
    away = 1 - home
    assert _total(complementary_displays(home, away)) == 100
    assert _total(complementary_displays(0.4949999999, 0.5050000001)) == 100


def test_ties_go_to_the_larger_share_and_equal_shares_show_50_each() -> None:
    assert complementary_displays(0.5, 0.5) == ("50%", "50%")
    assert complementary_displays(0.755, 0.245) == ("76%", "24%")
    assert complementary_displays(0.245, 0.755) == ("24%", "76%")


def test_every_real_snapshot_totals_exactly_100_with_stage_2_values_unchanged() -> None:
    allocated = 0
    for snapshot, stats in zip(real_snapshots(), real_stats(), strict=True):
        assert stats.status is StatsStatus.VALID
        summaries, _ = team_analytics(snapshot.match)
        for key in COMPLEMENTARY:
            home, away = (t.values[key] for t in stats.teams)
            if home.value is None or away.value is None:
                assert home.display == away.display == NOT_ENOUGH_DATA
                continue
            assert [home.value, away.value] == [getattr(s, key) for s in summaries]
            assert _total((home.display, away.display)) == 100
            independent = (display(Unit.SHARE, home.value), display(Unit.SHARE, away.value))
            allocated += (home.display, away.display) != independent
        completion = [t.values["pass_completion"] for t in stats.teams]
        assert all(v.display == display(Unit.SHARE, v.value) for v in completion)
    assert allocated >= 2  # field tilt on one snapshot of each match totalled 101 independently


def test_both_teams_share_one_sample_so_both_show_or_neither() -> None:
    for stats in real_stats():
        for key in COMPLEMENTARY:
            home, away = (t.values[key] for t in stats.teams)
            assert home.sample == away.sample
            assert home.status == away.status


def _shown(stats: SnapshotStats, key: str) -> bool:
    return all(t.values[key].value is not None for t in stats.teams)


def test_the_validator_rejects_a_pair_that_totals_101() -> None:
    stats = next(s for s in real_stats() if _shown(s, "field_tilt"))
    data = _edited(stats, "field_tilt", {"value": 0.505, "display": "51%"}, {})
    data["teams"][1]["values"]["field_tilt"].update({"value": 0.495, "display": "50%"})
    with pytest.raises(ValidationError, match="not the formatted value of the pair"):
        SnapshotStats.model_validate(data)
    data["teams"][1]["values"]["field_tilt"]["display"] = "49%"
    assert SnapshotStats.model_validate(data).teams[1].values["field_tilt"].display == "49%"


def test_the_validator_rejects_one_share_shown_and_one_insufficient() -> None:
    stats = next(s for s in real_stats() if _shown(s, "on_ball_share"))
    insufficient = {
        "value": None,
        "display": NOT_ENOUGH_DATA,
        "status": ValueStatus.INSUFFICIENT_SAMPLE.value,
    }
    data = _edited(stats, "on_ball_share", {}, insufficient)
    with pytest.raises(ValidationError, match="both teams or neither"):
        SnapshotStats.model_validate(data)


def test_the_validator_rejects_shares_that_do_not_sum_to_one() -> None:
    stats = next(s for s in real_stats() if _shown(s, "on_ball_share"))
    data = _edited(
        stats, "on_ball_share", {"value": 0.6, "display": "60%"}, {"value": 0.6, "display": "40%"}
    )
    with pytest.raises(ValidationError, match="sum to 1"):
        SnapshotStats.model_validate(data)
