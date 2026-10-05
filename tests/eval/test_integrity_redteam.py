"""The Stage 5 held-out provenance miss, reproduced: the ranking still changes, now flagged.

S06 held-out seed 900004: evidence from another match (seed 900003) replaces one tool result.
Provenance quarantines it; without the real result, a different hedged explanation ranks first.
"""

from functools import cache

import pytest

from matcheyes.agents.contracts import EvidenceIntegrity, HypothesisKind
from matcheyes.agents.narrative import INTEGRITY_WARNING, VERIFIED_ON_REMAINING
from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.orchestration.audit import audit_insight
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from matcheyes.orchestration.lineage import audit_lineage
from matcheyes_eval.redteam import EVIDENCE_FAULTS, Donors, TamperingToolBox, integrity_outcome
from matcheyes_synth.effects import counterfactual
from matcheyes_synth.generator import generate_match
from matcheyes_synth.scenarios import scenario

pytestmark = pytest.mark.integration

H = HypothesisKind
CANDIDATE = "ctx-shift-thornvale-defensive_action_height-44"


@cache
def workspaces() -> tuple[MatchWorkspace, MatchWorkspace, MatchWorkspace]:
    spec = scenario("S06_impact_substitution")
    ws = MatchWorkspace.build(generate_match(spec, 900004).observable)
    donor = MatchWorkspace.build(generate_match(spec, 900003).observable)
    twin = MatchWorkspace.build(generate_match(counterfactual(spec), 900004).observable)
    return ws, donor, twin


def tampered() -> tuple[InvestigationRecord, set[str]]:
    ws, donor, twin = workspaces()
    orchestrator = Orchestrator(ws, RuleBasedReasoner())
    toolbox = TamperingToolBox(
        Donors(ws, donor, twin), EVIDENCE_FAULTS["evidence_from_another_match"]
    )
    orchestrator.toolbox = toolbox
    return orchestrator.investigate(CANDIDATE), toolbox.tampered


@cache
def clean() -> InvestigationRecord:
    return Orchestrator(workspaces()[0], RuleBasedReasoner()).investigate(CANDIDATE)


def test_a_material_ranking_change_carries_the_integrity_warning() -> None:
    base = clean()
    record, corrupted = tampered()
    assert corrupted == {"ev-03"}
    assert base.final.leading is H.OPPONENT_DRIVEN
    assert base.final.evidence_integrity is EvidenceIntegrity.INTACT
    final = record.final
    assert record.verification is not None
    assert record.verification.quarantined == ("ev-03",)
    assert final.leading is H.TACTICAL_CHANGE
    assert final.evidence_integrity is EvidenceIntegrity.COMPROMISED
    assert INTEGRITY_WARNING in final.narrative.splitlines()
    assert f"[{VERIFIED_ON_REMAINING}, hypothesised]" in final.narrative
    assert final.strength.rank <= base.final.strength.rank
    assert integrity_outcome(base.final, final) == "different_conclusion_same_strength"
    assert audit_insight(workspaces()[0], final, record.verification).ok
    assert audit_lineage(workspaces()[0], record).ok


def test_the_same_corruption_gives_the_same_integrity_and_insight() -> None:
    first, _ = tampered()
    second, _ = tampered()
    assert first.verification == second.verification
    assert first.final == second.final
    assert first.final.model_dump_json() == second.final.model_dump_json()


def test_lineage_rejects_an_insight_that_drops_the_flag() -> None:
    record, _ = tampered()
    hidden = record.model_copy(
        update={
            "final": record.final.model_copy(
                update={"evidence_integrity": EvidenceIntegrity.UNAFFECTED}
            )
        }
    )
    report = audit_lineage(workspaces()[0], hidden)
    assert any("integrity" in b.reason for b in report.broken)
