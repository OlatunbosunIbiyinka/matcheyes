"""Stage 7 evaluation on a match in progress: every property holds, every fault is caught."""

from functools import cache

import pytest

from matcheyes_eval.__main__ import main
from matcheyes_eval.stage2 import Case
from matcheyes_eval.stage7 import LIFECYCLE_FAULTS, Stage7Results, evaluate_stage7, format_stage7
from matcheyes_synth.scenarios import scenario
from tests.lifecycle.support import SCENARIO, in_progress
from tests.synth.generated import generated

pytestmark = pytest.mark.integration


@cache
def results() -> Stage7Results:
    spec = scenario(SCENARIO)
    case = Case(spec, spec.default_seed, "planted", in_progress())
    assert generated(SCENARIO).observable.events[: len(case.match.events)] == case.match.events
    return evaluate_stage7([case], "development", 1, fresh_replays=1)


def test_every_lifecycle_property_holds() -> None:
    r = results()
    assert r.matches == 1 and r.fresh_replays == 1 and r.storylines > 0
    numbered = {name.split(" ", 1)[0] for name in r.properties}
    expected = {str(n) for n in range(1, 20)} - {"8", "14"}
    assert expected <= numbered
    for name, rate in r.properties.items():
        assert rate.total > 0 and rate.hits == rate.total, name
    assert r.revisions_audit_flagged == 0 and not r.audit_findings


def test_views_and_integrity_propagate() -> None:
    r = results()
    assert r.stale_views.total > 0 and r.stale_views.hits == r.stale_views.total
    assert r.current_views.total > 0 and r.current_views.hits == r.current_views.total
    assert r.stage6_withheld == 0
    assert r.compromised_current > 0
    assert r.integrity_noticed.hits == r.integrity_noticed.total == r.compromised_current
    assert r.stage6_compromised_warned.hits == r.stage6_compromised_warned.total > 0


def test_faults_injected_are_caught_by_the_expected_check() -> None:
    faults = results().faults
    assert set(faults) <= set(LIFECYCLE_FAULTS) and len(faults) >= 10
    for row in faults.values():
        assert row.injected > 0 and row.caught == row.caught_by_expected == row.injected


def test_the_report_states_actual_results() -> None:
    text = format_stage7(results())
    assert "properties (passed / checked):" in text
    assert "lifecycle red team:" in text
    assert "storylines against planted truth" in text


@pytest.mark.slow
def test_stage7_command_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stage7", "--seeds", "1", "--matches", "1", "--fresh-replays", "0"]) == 0
    assert "Stage 7 lifecycle - development split, 1 seeds" in capsys.readouterr().out
