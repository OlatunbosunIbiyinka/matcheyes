"""Stage 5 verifier hardening: assertion entailment, provenance replay and hedged claim text."""

import pytest

from matcheyes.agents.contracts import (
    HYPOTHESIS_DESCRIPTIONS,
    HYPOTHESIS_HEDGED,
    ClaimType,
    Comparator,
    Fact,
    FactAssertion,
    HypothesisKind,
    Status,
    ToolName,
    Verdict,
)
from matcheyes.agents.hypotheses import Condition, entails
from matcheyes.agents.narrative import conclude
from matcheyes.agents.verification import Verifier
from matcheyes.orchestration.audit import CAUSAL_TERMS
from tests.agents.test_verification import _check, _hypothesis, _item, _replace, _verify, strong_run

H = HypothesisKind
C = Comparator


@pytest.mark.parametrize(
    ("comparator", "value", "test", "expected"),
    [
        (C.EQ, 3, Condition("x", C.GE, 2), True),
        (C.EQ, 1, Condition("x", C.GE, 2), False),
        (C.GE, 2, Condition("x", C.GE, 2), True),
        (C.GE, 0, Condition("x", C.GE, 2), False),
        (C.GT, 2, Condition("x", C.GE, 2), True),
        (C.GE, 2, Condition("x", C.GT, 2), False),
        (C.GE, 3, Condition("x", C.GT, 2), True),
        (C.LE, -1, Condition("x", C.LE, -1), True),
        (C.LE, 5, Condition("x", C.LE, -1), False),
        (C.LE, -1, Condition("x", C.LT, -1), False),
        (C.LT, -1, Condition("x", C.LE, -1), True),
        (C.GE, 2, Condition("x", C.LE, 5), False),
        (C.EQ, "goal", Condition("x", C.EQ, "goal"), True),
        (C.NE, "weak", Condition("x", C.NE, "weak"), True),
        (C.NE, "other", Condition("x", C.NE, "weak"), False),
        (C.NE, "goal", Condition("x", C.EQ, "goal"), False),
        (C.EQ, True, Condition("x", C.GE, 1), False),
        (C.GE, True, Condition("x", C.GE, 1), False),
    ],
)
def test_an_assertion_entails_a_rule_only_if_it_states_enough(
    comparator: Comparator, value: Fact, test: Condition, expected: bool
) -> None:
    assert entails(comparator, value, test) is expected


def test_a_true_non_entailing_assertion_is_not_material() -> None:
    """level_rank >= 0 is true of a Strong change but says nothing about strength."""
    run = strong_run()
    item = _item(run, ToolName.GET_CANDIDATE_ASSESSMENT)
    weak = FactAssertion(evidence_id=item.evidence_id, fact="level_rank", comparator=C.GE, value=0)
    result = _verify(run, _replace(run[3], H.TACTICAL_CHANGE, supporting=(weak,)))
    check = _check(result, H.TACTICAL_CHANGE, weak)
    assert check.accepted and check.reason == "true but not material support"
    assert result.leading is None


def test_the_same_fact_stated_exactly_is_material() -> None:
    run = strong_run()
    item = _item(run, ToolName.GET_CANDIDATE_ASSESSMENT)
    exact = FactAssertion(
        evidence_id=item.evidence_id,
        fact="level_rank",
        comparator=C.EQ,
        value=item.facts["level_rank"],
    )
    lead = _hypothesis(run[3], H.TACTICAL_CHANGE)
    others = tuple(a for a in lead.supporting if a.fact != "level_rank")
    result = _verify(run, _replace(run[3], H.TACTICAL_CHANGE, supporting=(exact, *others)))
    assert _check(result, H.TACTICAL_CHANGE, exact).reason == "true and material"
    assert result.leading is H.TACTICAL_CHANGE


# --- provenance replay -----------------------------------------------------------------------


def test_clean_evidence_replays_and_nothing_is_quarantined() -> None:
    result = _verify(strong_run())
    assert result.gates["provenance"]
    assert result.quarantined == ()


def test_an_altered_item_is_quarantined_and_citations_of_it_rejected() -> None:
    run = strong_run()
    item = _item(run, ToolName.GET_CANDIDATE_ASSESSMENT)
    altered = item.model_copy(update={"facts": {**item.facts, "level_rank": 7}})
    pool = tuple(altered if e is item else e for e in run[2])
    result = _verify(run, evidence=pool)
    assert result.quarantined == (item.evidence_id,)
    assert not result.gates["provenance"]
    assert result.leading is None
    cited = [
        c
        for v in result.hypotheses
        for c in v.checks
        if c.assertion.evidence_id == item.evidence_id
    ]
    assert cited and all(not c.accepted and "provenance" in c.reason for c in cited)
    final = conclude(run[0], run[1], pool, result)
    assert item.evidence_id not in {e.evidence_id for e in final.evidence}
    assert final.quarantined == (item.evidence_id,)
    assert "failed provenance checks and was excluded" in final.narrative


def test_a_clean_insight_carries_no_exclusion_notice() -> None:
    run = strong_run()
    final = conclude(run[0], run[1], run[2], _verify(run))
    assert final.quarantined == ()
    assert "provenance" not in final.narrative


@pytest.mark.parametrize(
    "update",
    [
        {"event_ids": ("m-fabricated-000001",)},
        {"team_id": "somewhere-else"},
        {"summary": "Edited summary."},
        {"span": (0, 1)},
    ],
    ids=["fabricated_event", "wrong_team", "edited_summary", "moved_span"],
)
def test_any_edit_to_a_tool_result_fails_replay(update: dict[str, object]) -> None:
    run = strong_run()
    item = run[2][0]
    edited = item.model_copy(update=update)
    kept, quarantined = Verifier(run[0]).provenance((edited, *run[2][1:]))
    assert item.evidence_id in quarantined
    assert edited not in kept


def test_duplicate_evidence_ids_are_quarantined() -> None:
    run = strong_run()
    kept, quarantined = Verifier(run[0]).provenance((*run[2], run[2][0]))
    assert quarantined == {run[2][0].evidence_id: "duplicate evidence id"}
    assert len(kept) == len(run[2])


def test_a_request_that_does_not_exist_in_this_match_is_quarantined() -> None:
    run = strong_run()
    item = _item(run, ToolName.GET_CANDIDATE_ASSESSMENT)
    foreign = item.model_copy(update={"arguments": {"candidate_id": "ctx-shift-nowhere-x-1"}})
    _, quarantined = Verifier(run[0]).provenance((foreign,))
    assert quarantined[item.evidence_id] == "request does not replay on this match"


# --- hedged claim text -----------------------------------------------------------------------


def test_hedged_text_carries_no_causal_language() -> None:
    for kind, text in HYPOTHESIS_HEDGED.items():
        lowered = text.lower()
        assert not [t for t in CAUSAL_TERMS if t in lowered], kind


def test_a_tentative_claim_is_hedged_and_not_typed_causal() -> None:
    run = strong_run()
    result = _verify(run)
    final = conclude(run[0], run[1], run[2], result)
    assert final.verdict is Verdict.TENTATIVE
    claim = final.claims[1]
    assert claim.claim_type is ClaimType.INTERPRETIVE
    assert claim.text == HYPOTHESIS_HEDGED[H.TACTICAL_CHANGE]
    assert claim.text != HYPOTHESIS_DESCRIPTIONS[H.TACTICAL_CHANGE]
    assert claim.status is Status.SUPPORTED
