"""Stage 5 red team: every injected fault is detected, and clean or valid cases are not rejected."""

from functools import cache

import pytest

from matcheyes.agents.casefile import build_case_file
from matcheyes.agents.contracts import EvidenceRequest, HypothesisKind, ToolName
from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.orchestration.investigation import Orchestrator
from matcheyes_eval.__main__ import main
from matcheyes_eval.redteam import (
    ASSESSMENT_FAULTS,
    EVIDENCE_FAULTS,
    INSIGHT_FAULTS,
    Donors,
    RedTeamResults,
    TamperingToolBox,
    evaluate_redteam,
    format_redteam,
    hand_built,
)
from matcheyes_eval.stage2 import build_cases
from matcheyes_synth.scenarios import scenario
from matcheyes_synth.seeds import development_seeds
from tests.agents.support import CONTROL, SUBSTITUTION, strongest, workspace

pytestmark = pytest.mark.integration

SCENARIOS = ("S06_impact_substitution", "S07_against_the_run_of_play", "S02_press_surge")


@cache
def results() -> RedTeamResults:
    cases = build_cases(development_seeds(1), [scenario(s) for s in SCENARIOS])
    return evaluate_redteam(cases, "development", 1, fault_matches=2)


def test_the_brief_fault_classes_are_all_injected() -> None:
    brief = {
        "altered_metric_value",
        "wrong_team",
        "wrong_event_id",
        "event_outside_window",
        "reversed_temporal_order",
        "fabricated_event",
        "evidence_from_another_candidate",
        "evidence_from_another_match",
        "evidence_from_the_twin",
    }
    assert brief <= set(EVIDENCE_FAULTS)
    assert {
        "contradiction_ignored",
        "non_entailing_comparator",
        "hidden_contradiction",
        "score_state_misinterpretation",
        "substitution_misattribution",
        "unsupported_player_attribution",
        "inflated_strength",
    } <= set(ASSESSMENT_FAULTS)
    assert {
        "unsupported_causal_language",
        "omitted_alternatives",
        "upgraded_strength",
        "invented_fact",
    } <= set(INSIGHT_FAULTS)


def test_no_fault_reaches_a_reader_undetected() -> None:
    r = results()
    for layer in ("A", "B", "C"):
        tallies = r.faults[layer]
        assert sum(t.injected for t in tallies.values()) > 0, layer
        for name, t in tallies.items():
            assert t.missed == 0, f"{layer}/{name}"
    for name, t in r.faults["B"].items():
        assert t.verifier_flagged == t.injected, name


def test_tampered_investigations_are_never_stronger_and_report_their_integrity() -> None:
    integrity = results().integrity
    tampered = sum(t.injected for t in results().faults["B"].values())
    states = sum(v for k, v in integrity.items() if k.startswith("state:"))
    assert states == tampered
    assert integrity["state:intact"] == 0
    assert integrity["outcome:stronger"] == 0
    assert "evidence integrity" in format_redteam(results())


def test_clean_investigations_are_not_rejected() -> None:
    r = results()
    assert r.clean_investigations > 0
    assert r.clean_quarantined == 0
    assert r.clean_lineage_broken == 0
    assert r.clean_audit_flagged == 0
    assert r.reference_rejected == 0


def test_valid_cases_are_accepted() -> None:
    r = results()
    assert r.valid, "no valid cases found"
    for name, t in r.valid.items():
        assert t.accepted == t.total, name
        assert t.hand_built_accepted == t.total, name
        assert t.audit_clean == t.total, name


def test_decoys_are_scored_raw_and_audited() -> None:
    r = results()
    counts = r.decoys["S07_against_the_run_of_play/D1"]
    assert counts["matches"] == 1
    assert counts["audited_violation"] <= counts["raw_above_ceiling"]
    assert "decoys" in format_redteam(r)


def test_tampering_toolbox_records_what_it_corrupted() -> None:
    ws = workspace(CONTROL)
    toolbox = TamperingToolBox(Donors(ws, None, None), EVIDENCE_FAULTS["altered_metric_value"])
    c = strongest(ws)
    request = EvidenceRequest(
        request_id="r1",
        tool=ToolName.GET_CANDIDATE_ASSESSMENT,
        arguments={"candidate_id": c.candidate_id},
        hypothesis=HypothesisKind.NATURAL_VARIATION,
    )
    item = toolbox.run(request, "ev-01")
    assert toolbox.tampered == {"ev-01"}
    assert Orchestrator(ws, RuleBasedReasoner()).verifier.provenance((item,))[1]


def test_a_hand_built_correct_assessment_is_accepted() -> None:
    ws = workspace(SUBSTITUTION)
    orchestrator = Orchestrator(ws, RuleBasedReasoner())
    for c in ws.stage3.candidates[:8]:
        record = orchestrator.investigate(c.candidate_id)
        if record.final.leading is None or record.final.leading is HypothesisKind.NATURAL_VARIATION:
            continue
        pool = {e.evidence_id: e for e in record.final.evidence}
        built = hand_built(ws, c.candidate_id, pool, record.final.leading)
        result = orchestrator.verifier.verify(
            build_case_file(ws, c.candidate_id, "inv-x"), record.final.evidence, built
        )
        assert result.leading is record.final.leading
        assert result.strength is record.final.strength
        return
    pytest.fail("no explained candidate found")


def test_redteam_cli_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["redteam", "--seeds", "1", "--fault-matches", "0"]) == 0
    assert "clean reference investigations" in capsys.readouterr().out
