"""Stage 7 attribution: insights the pipeline produced are never lost by the lifecycle, and the
measure itself does detect a reconciliation that loses one."""

from functools import cache

import pytest

from matcheyes.lifecycle.contracts import LifecycleState
from matcheyes.lifecycle.evaluate import SnapshotEvaluation
from matcheyes.lifecycle.reconcile import reconcile
from matcheyes_eval.stage2 import Case
from matcheyes_eval.stage7_attribution import (
    CATEGORIES,
    AttributionResults,
    _churn,
    attribute_case,
    fold,
    format_attribution,
)
from matcheyes_synth.scenarios import scenario
from tests.lifecycle.support import SCENARIO, evaluated, in_progress, insights

pytestmark = pytest.mark.integration


def _case() -> Case:
    spec = scenario(SCENARIO)
    return Case(spec, spec.default_seed, "planted", in_progress())


@cache
def results() -> AttributionResults:
    r = AttributionResults(split="development", seeds=1)
    attribute_case(_case(), r)
    return r


def _dropping_last(state: LifecycleState, evaluation: SnapshotEvaluation) -> LifecycleState:
    """A broken reconciliation: the last insight of every snapshot never reaches a storyline."""
    return reconcile(state, evaluation.model_copy(update={"insights": evaluation.insights[:-1]}))


def test_every_produced_insight_is_held_by_its_storyline() -> None:
    r = results()
    assert r.produced > 0
    assert r.lost == 0 and r.preserved == r.produced and r.wrong_key == 0
    assert r.one_storyline_per_insight.hits == r.one_storyline_per_insight.total > 0
    assert r.fold_equals_engine.hits == r.fold_equals_engine.total == 1


def test_expected_insights_are_classified_on_every_snapshot_after_the_window_opens() -> None:
    for row in results().insights:
        assert set(row.snapshots) <= set(CATEGORIES)
        assert row.snapshots["B lost"] == 0
        if row.first_available is None:
            assert row.final.startswith("A upstream: ") and not row.storylines
        else:
            assert row.first_held == row.first_available and row.storylines


def test_a_reconciliation_that_loses_an_insight_is_reported_as_a_lifecycle_bug() -> None:
    r = AttributionResults(split="development", seeds=1)
    attribute_case(_case(), r, step=_dropping_last)
    assert r.lost > 0
    assert r.one_storyline_per_insight.hits < r.one_storyline_per_insight.total
    assert r.fold_equals_engine.hits == 0


def test_wording_only_revisions_are_measured_as_churn() -> None:
    a = insights()[0]
    reworded = a.model_copy(
        update={"final": a.final.model_copy(update={"narrative": a.final.narrative + " Again."})}
    )
    folded = fold(in_progress().info.match_id, [evaluated(10, a), evaluated(20, reworded)])
    assert folded.holder[0, 0] is not None and folded.holder[1, 0] == folded.holder[0, 0]
    r = AttributionResults(split="development", seeds=1)
    _churn(folded.states[-1], r)
    assert r.churn_revisions == 1 and r.churn_fields == {"narrative": 1}


def test_the_report_states_actual_results() -> None:
    text = format_attribution(results())
    assert "preservation (every insight the pipeline produced" in text
    assert "lost 0" in text
    assert "revision churn:" in text
