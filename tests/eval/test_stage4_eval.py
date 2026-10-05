import pytest

from matcheyes.agents.contracts import HypothesisKind
from matcheyes.agents.reasoning import AgentTask, RuleBasedReasoner, Step
from matcheyes_eval.__main__ import main
from matcheyes_eval.stage2 import CONTROL_SCENARIO, build_cases
from matcheyes_eval.stage4 import (
    EXPECTED_EXPLANATIONS,
    FAULTS,
    MEASURES,
    FaultyReasoner,
    evaluate_stage4,
    format_stage4,
)
from matcheyes_synth.scenarios import CATALOGUE, scenario
from matcheyes_synth.seeds import development_seeds
from matcheyes_synth.truth import InterventionKind
from tests.agents.support import CONTROL, case_for, strongest, workspace


def test_every_planted_intervention_kind_has_an_expected_explanation() -> None:
    kinds = {i.kind for spec in CATALOGUE for i in spec.interventions}
    assert kinds <= set(EXPECTED_EXPLANATIONS)
    assert set(EXPECTED_EXPLANATIONS) <= set(InterventionKind)
    for expected in EXPECTED_EXPLANATIONS.values():
        assert expected <= set(HypothesisKind)


def test_faulty_reasoner_corrupts_only_assessments() -> None:
    ws = workspace(CONTROL)
    case = case_for(ws, strongest(ws))
    model = FaultyReasoner(FAULTS["inflated_strength"])
    plan = AgentTask(step=Step.PLAN, case=case)
    assert model.respond(plan) == RuleBasedReasoner().respond(plan)
    assert model.applied == set()


@pytest.mark.integration
def test_stage4_evaluation_scores_planted_twins_controls_and_decoys() -> None:
    specs = [scenario(s) for s in (CONTROL_SCENARIO, "S02_press_surge", "S08_coincidence_decoy")]
    results = evaluate_stage4(
        build_cases(development_seeds(1), specs),
        "development",
        1,
        determinism_cases=2,
        fault_seeds=1,
    )
    row = results.insights["S02_press_surge/E1"]
    assert row.twin_group == "untriggered"
    for measure in MEASURES:
        assert row.planted[measure].total == 1
        assert row.twin[measure].total == 1
    assert results.matches[CONTROL_SCENARIO] == 1
    assert "S08_coincidence_decoy/D1" in results.decoys
    assert results.final_unsupported == 0
    assert results.untraceable == 0
    assert results.temporal_violations == 0
    assert results.over_ceiling == 0
    assert results.nondeterministic == 0
    assert results.determinism_checked == 2
    assert results.final_claims >= results.investigations
    for name, (caught, total) in results.faults.items():
        assert caught == total, name
    text = format_stage4(results)
    assert "unsupported-claim rate: 0 of" in text
    assert "insufficient-evidence rate:" in text
    assert "verifier fault injection" in text


@pytest.mark.slow
def test_stage4_command_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stage4", "--seeds", "1", "--fault-seeds", "0"]) == 0
    out = capsys.readouterr().out
    assert "Stage 4 evaluation - development split, 1 seeds" in out
    assert "final unsupported claims: 0" in out
