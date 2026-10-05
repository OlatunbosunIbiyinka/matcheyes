"""Evidence lineage: every final claim resolves to observed events, and broken links are found."""

import pytest

from matcheyes.agents.contracts import EvidenceItem, Verdict
from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from matcheyes.orchestration.lineage import audit_lineage
from tests.agents.support import SUBSTITUTION, strongest, workspace
from tests.synth.generated import SCENARIO_IDS


def _record(scenario: str = SUBSTITUTION) -> InvestigationRecord:
    ws = workspace(scenario)
    return Orchestrator(ws, RuleBasedReasoner()).investigate(strongest(ws).candidate_id)


def _with_item(record: InvestigationRecord, index: int, item: EvidenceItem) -> InvestigationRecord:
    pool = list(record.final.evidence)
    pool[index] = item
    final = record.final.model_copy(update={"evidence": tuple(pool)})
    return record.model_copy(update={"final": final})


@pytest.mark.integration
@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_every_reference_investigation_has_unbroken_lineage(scenario: str) -> None:
    ws = workspace(scenario)
    orchestrator = Orchestrator(ws, RuleBasedReasoner())
    for c in ws.stage3.candidates[:6]:
        record = orchestrator.investigate(c.candidate_id)
        report = audit_lineage(ws, record)
        assert report.ok, report.broken
        assert report.reaches_events(f"candidate:{c.candidate_id}")
        if record.final.verdict in (Verdict.EXPLAINED, Verdict.TENTATIVE):
            assert report.reaches_events("claim:1")


def test_lineage_runs_through_every_layer() -> None:
    report = audit_lineage(workspace(SUBSTITUTION), _record())
    layers = set(report.nodes.values())
    assert {"claim", "evidence", "request", "candidate", "stage2", "series", "event"} <= layers


def test_an_altered_fact_breaks_the_replay_link() -> None:
    record = _record()
    item = record.final.evidence[0]
    fact = next(k for k, v in item.facts.items() if isinstance(v, int) and not isinstance(v, bool))
    altered = item.model_copy(update={"facts": {**item.facts, fact: item.facts[fact] + 7}})
    report = audit_lineage(workspace(SUBSTITUTION), _with_item(record, 0, altered))
    assert any("replay differs" in b.reason for b in report.broken)


def test_an_unknown_event_breaks_the_event_link() -> None:
    record = _record()
    item = record.final.evidence[0]
    forged = item.model_copy(update={"event_ids": (*item.event_ids, "m-forged-99999")})
    report = audit_lineage(workspace(SUBSTITUTION), _with_item(record, 0, forged))
    assert any("m-forged-99999" in b.reason for b in report.broken)


def test_a_claim_citing_missing_evidence_is_broken() -> None:
    record = _record()
    claim = record.final.claims[0].model_copy(update={"supporting_evidence_ids": ("ev-99",)})
    final = record.final.model_copy(update={"claims": (claim, *record.final.claims[1:])})
    report = audit_lineage(workspace(SUBSTITUTION), record.model_copy(update={"final": final}))
    assert any("ev-99" in b.reason for b in report.broken)


def explained_record() -> tuple[str, InvestigationRecord]:
    for scenario in SCENARIO_IDS:
        ws = workspace(scenario)
        orchestrator = Orchestrator(ws, RuleBasedReasoner())
        for c in ws.stage3.candidates[:6]:
            record = orchestrator.investigate(c.candidate_id)
            if record.final.verdict in (Verdict.EXPLAINED, Verdict.TENTATIVE):
                return scenario, record
    raise AssertionError("no reference investigation produced an explanation")


def test_an_explanation_without_verification_is_broken() -> None:
    scenario, record = explained_record()
    report = audit_lineage(workspace(scenario), record.model_copy(update={"verification": None}))
    assert any("without a verification" in b.reason for b in report.broken)


def test_explanation_support_that_differs_from_the_verdict_is_broken() -> None:
    scenario, record = explained_record()
    claim = record.final.claims[1]
    other = next(
        e.evidence_id
        for e in record.final.evidence
        if e.evidence_id not in claim.supporting_evidence_ids
    )
    tampered = claim.model_copy(update={"supporting_evidence_ids": (other,)})
    final = record.final.model_copy(update={"claims": (record.final.claims[0], tampered)})
    report = audit_lineage(workspace(scenario), record.model_copy(update={"final": final}))
    assert any("differs from the verdict" in b.reason for b in report.broken)
