"""Properties every Stage 4 investigation must satisfy, on every generated scenario."""

import pytest

from matcheyes.__main__ import format_investigation
from matcheyes.agents.contracts import (
    AGENT_CLAIM_CEILING,
    HYPOTHESIS_HEDGED,
    ClaimType,
    HypothesisKind,
    Status,
    Verdict,
)
from matcheyes.agents.hypotheses import RESIDUAL, TRIGGERED
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.orchestration.investigation import MatchInvestigation, investigate_match
from tests.synth.generated import SCENARIO_IDS, generated

pytestmark = pytest.mark.integration

H = HypothesisKind
Case = tuple[set[str], MatchInvestigation]


@pytest.fixture(scope="module", params=SCENARIO_IDS)
def case(request: pytest.FixtureRequest) -> Case:
    match = generated(request.param).observable
    return {e.event_id for e in match.events}, investigate_match(match)


def test_every_insight_traces_to_observed_events_and_fetched_evidence(case: Case) -> None:
    known, inv = case
    for record in inv.records:
        final = record.final
        assert final.event_ids and set(final.event_ids) <= known
        evidence = {e.evidence_id for e in final.evidence}
        for claim in final.claims:
            assert set(claim.supporting_evidence_ids) <= evidence
            assert set(claim.contradicting_evidence_ids) <= evidence
        for item in final.evidence:
            assert set(item.event_ids) <= known


def test_the_claim_ladder_holds(case: Case) -> None:
    _, inv = case
    for record in inv.records:
        final, v = record.final, record.verification
        assert v is not None
        assert final.strength.rank <= AGENT_CLAIM_CEILING.rank
        assert v.strength.rank <= min(v.proposed_strength.rank, v.eligible_strength.rank)
        causal = [c for c in final.claims if c.claim_type is ClaimType.CAUSAL]
        explanations = [c for c in final.claims if c.hypothesis is not None]
        for claim in causal:
            assert claim.status is Status.SUPPORTED
            assert claim.strength is ClaimStrength.SUPPORTED
        if final.verdict is Verdict.EXPLAINED:
            assert final.strength is ClaimStrength.SUPPORTED
            assert final.leading not in RESIDUAL
            assert final.stage3_level == EvidenceLevel.STRONG.value
            assert all(s is Status.CONTRADICTED for _, s in final.alternatives)
            assert len(causal) == 1
        if final.verdict is Verdict.TENTATIVE:
            assert not causal
            assert explanations[0].strength is ClaimStrength.HYPOTHESISED
            assert explanations[0].text == HYPOTHESIS_HEDGED[explanations[0].hypothesis]
        if final.verdict in (Verdict.INSUFFICIENT_EVIDENCE, Verdict.NATURAL_VARIATION):
            assert not causal


def test_triggered_explanations_respect_time(case: Case) -> None:
    _, inv = case
    for record in inv.records:
        if record.final.leading in TRIGGERED:
            assert record.verification is not None
            assert record.verification.gates["temporal"]


def test_every_plausible_alternative_is_assessed_by_the_reference(case: Case) -> None:
    _, inv = case
    for record in inv.records:
        assert record.verification is not None
        assert record.verification.gates["alternatives"]
        assert record.verification.gates["factual"]
        assert record.verification.gates["no_unsupported_assertions"]


def test_narratives_are_labelled_and_match_the_verdict(case: Case) -> None:
    _, inv = case
    for record in inv.records:
        final = record.final
        lines = final.narrative.splitlines()
        assert lines[0].startswith("FACT: ")
        assert lines[1].startswith("ANALYSIS: ")
        if final.verdict in (Verdict.EXPLAINED, Verdict.TENTATIVE):
            assert f"[verified, {final.strength.value}]" in lines[2]
        if final.verdict is Verdict.INSUFFICIENT_EVIDENCE:
            assert "insufficient evidence" in lines[2]


def test_the_cli_report_renders(case: Case) -> None:
    _, inv = case
    text = format_investigation(inv)
    assert inv.match_id in text
