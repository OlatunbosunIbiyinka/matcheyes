"""Agent contracts reject anything outside their shape: the first line of defence."""

import pytest
from pydantic import ValidationError

from matcheyes.agents.contracts import (
    AGENT_CLAIM_CEILING,
    Assessment,
    Challenge,
    Comparator,
    EvidenceRequest,
    FactAssertion,
    HypothesisKind,
    InvestigationPlan,
    ProposedHypothesis,
    Status,
    ToolName,
)
from matcheyes.domain.claims import ClaimStrength

H = HypothesisKind


def _request(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "request_id": "candidate",
        "tool": "get_candidate_assessment",
        "arguments": {"candidate_id": "c-1"},
        "hypothesis": "tactical_change",
    }
    return {**base, **overrides}


def test_a_valid_request_parses() -> None:
    request = EvidenceRequest.model_validate(_request())
    assert request.tool is ToolName.GET_CANDIDATE_ASSESSMENT


@pytest.mark.parametrize(
    "overrides",
    [
        {"tool": "run_shell"},
        {"tool": "get_answer_key"},
        {"hypothesis": "planted_cause"},
        {"request_id": "Has Spaces"},
        {"request_id": "x" * 41},
        {"arguments": {"candidate_id": "c" * 81}},
        {"arguments": {f"a{'b' * i}": 1 for i in range(11)}},
        {"arguments": {"Bad-Name": 1}},
        {"arguments": {"nested": {"x": 1}}},
        {"arguments": {"items": [1, 2]}},
        {"purpose": "p" * 401},
        {"unexpected": True},
    ],
)
def test_requests_outside_the_contract_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvidenceRequest.model_validate(_request(**overrides))


def test_plans_need_a_hypothesis_and_cap_requests() -> None:
    with pytest.raises(ValidationError):
        InvestigationPlan(hypotheses=(), requests=())
    many = tuple(EvidenceRequest.model_validate(_request(request_id=f"r{i}")) for i in range(21))
    with pytest.raises(ValidationError):
        InvestigationPlan(hypotheses=(H.TACTICAL_CHANGE,), requests=many)


def test_assessments_need_a_hypothesis_and_a_known_strength() -> None:
    with pytest.raises(ValidationError):
        Assessment(hypotheses=(), leading=None, proposed_strength=ClaimStrength.OBSERVED)
    with pytest.raises(ValidationError):
        Assessment.model_validate_json(
            '{"hypotheses": [{"kind": "tactical_change", "status": "supported", '
            '"statement": "x"}], "leading": null, "proposed_strength": "proven"}'
        )


def test_fact_assertions_are_bounded() -> None:
    with pytest.raises(ValidationError):
        FactAssertion(evidence_id="ev-01", fact="f" * 61, comparator=Comparator.EQ, value=1)
    with pytest.raises(ValidationError):
        FactAssertion.model_validate(
            {"evidence_id": "ev-01", "fact": "x", "comparator": "approx", "value": 1}
        )


def test_challenges_may_be_empty_but_are_bounded() -> None:
    assert Challenge().alternatives == ()
    with pytest.raises(ValidationError):
        Challenge(objections=tuple("o" for _ in range(11)))


def test_statuses_and_ceiling() -> None:
    hypothesis = ProposedHypothesis(kind=H.FORMATION_CHANGE, status=Status.SUPPORTED, statement="")
    assert hypothesis.supporting == ()
    assert AGENT_CLAIM_CEILING is ClaimStrength.SUPPORTED
