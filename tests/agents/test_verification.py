"""The verifier's gates, exercised by corrupting real reference assessments one way at a time."""

from functools import cache

from matcheyes.agents.casefile import CaseFile
from matcheyes.agents.contracts import (
    AssertionCheck,
    Assessment,
    Comparator,
    EvidenceItem,
    FactAssertion,
    HypothesisKind,
    ProposedHypothesis,
    Status,
    ToolName,
    VerificationResult,
)
from matcheyes.agents.hypotheses import RESIDUAL
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.agents.verification import GATES, Verifier
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.orchestration.investigation import investigate_match
from tests.agents.support import CONTROL, SUBSTITUTION, reference_run, strongest, workspace
from tests.synth.generated import generated

H = HypothesisKind
Run = tuple[MatchWorkspace, CaseFile, tuple[EvidenceItem, ...], Assessment]


@cache
def strong_run() -> Run:
    """The strongest change in the control match: a Strong, untriggered change."""
    ws = workspace(CONTROL)
    c = strongest(ws)
    assert c.level is EvidenceLevel.STRONG
    case, evidence, assessment, _ = reference_run(ws, c)
    return ws, case, evidence, assessment


@cache
def triggered_run() -> Run:
    """A change the reference investigation explains with a goal (score-state response)."""
    ws = workspace(SUBSTITUTION)
    inv = investigate_match(generated(SUBSTITUTION).observable)
    record = next(r for r in inv.records if r.final.leading is H.SCORE_STATE_RESPONSE)
    c = ws.candidates[record.final.candidate_id]
    case, evidence, assessment, _ = reference_run(ws, c)
    return ws, case, evidence, assessment


def _verify(
    run: Run, assessment: Assessment | None = None, evidence: tuple[EvidenceItem, ...] | None = None
) -> VerificationResult:
    ws, case, pool, reference = run
    return Verifier(ws).verify(
        case, pool if evidence is None else evidence, assessment or reference
    )


def _hypothesis(assessment: Assessment, kind: HypothesisKind) -> ProposedHypothesis:
    return next(h for h in assessment.hypotheses if h.kind is kind)


def _replace(assessment: Assessment, kind: HypothesisKind, **update: object) -> Assessment:
    hypotheses = tuple(
        h.model_copy(update=update) if h.kind is kind else h for h in assessment.hypotheses
    )
    return assessment.model_copy(update={"hypotheses": hypotheses})


def _verdict(result: VerificationResult, kind: HypothesisKind) -> Status:
    return next(v.verified for v in result.hypotheses if v.kind is kind)


def _check(
    result: VerificationResult, kind: HypothesisKind, assertion: FactAssertion
) -> AssertionCheck:
    verdict = next(v for v in result.hypotheses if v.kind is kind)
    return next(c for c in verdict.checks if c.assertion == assertion)


def _item(run: Run, tool: ToolName) -> EvidenceItem:
    return next(e for e in run[2] if e.tool is tool)


# --- the reference itself passes -----------------------------------------------------------


def test_the_reference_assessment_passes_every_gate() -> None:
    result = _verify(strong_run())
    assert set(result.gates) == set(GATES)
    assert all(result.gates.values()), result.gates
    assert result.leading is H.TACTICAL_CHANGE
    assert result.strength is ClaimStrength.HYPOTHESISED


def test_a_residual_explanation_never_reaches_supported() -> None:
    """Tactical change rests only on the change itself, so even with every alternative
    contradicted on a Strong change it stays HYPOTHESISED."""
    run = strong_run()
    inflated = run[3].model_copy(update={"proposed_strength": ClaimStrength.SUPPORTED})
    result = _verify(run, inflated)
    others = [v for v in result.hypotheses if v.kind is not H.TACTICAL_CHANGE]
    assert all(v.verified is Status.CONTRADICTED for v in others)
    assert H.TACTICAL_CHANGE in RESIDUAL
    assert result.strength is ClaimStrength.HYPOTHESISED
    assert not result.gates["strength_eligible"]
    assert any("strength supported -> hypothesised" in d for d in result.downgrades)


# --- factual gate --------------------------------------------------------------------------


def test_a_fabricated_evidence_id_is_rejected() -> None:
    run = strong_run()
    lead = _hypothesis(run[3], H.TACTICAL_CHANGE)
    forged = tuple(a.model_copy(update={"evidence_id": "ev-99"}) for a in lead.supporting)
    result = _verify(run, _replace(run[3], H.TACTICAL_CHANGE, supporting=forged))
    assert not result.gates["factual"]
    assert not result.gates["no_unsupported_assertions"]
    assert _verdict(result, H.TACTICAL_CHANGE) is not Status.SUPPORTED
    assert result.leading is None


def test_a_false_assertion_is_rejected() -> None:
    run = strong_run()
    item = _item(run, ToolName.GET_CANDIDATE_ASSESSMENT)
    false = FactAssertion(
        evidence_id=item.evidence_id, fact="level_rank", comparator=Comparator.GT, value=99
    )
    result = _verify(run, _replace(run[3], H.TACTICAL_CHANGE, supporting=(false,)))
    check = _check(result, H.TACTICAL_CHANGE, false)
    assert not check.accepted
    assert check.reason.startswith("false")
    assert result.leading is None


def test_an_invented_fact_name_is_rejected() -> None:
    run = strong_run()
    item = _item(run, ToolName.GET_CANDIDATE_ASSESSMENT)
    invented = FactAssertion(
        evidence_id=item.evidence_id,
        fact="manager_instruction",
        comparator=Comparator.EQ,
        value=True,
    )
    result = _verify(run, _replace(run[3], H.TACTICAL_CHANGE, supporting=(invented,)))
    assert not result.gates["factual"]
    assert result.leading is None


# --- materiality and relevance -------------------------------------------------------------


def test_true_but_immaterial_support_cannot_promote() -> None:
    run = strong_run()
    item = _item(run, ToolName.GET_CANDIDATE_ASSESSMENT)
    trivial = FactAssertion(
        evidence_id=item.evidence_id, fact="period", comparator=Comparator.GE, value=1
    )
    result = _verify(run, _replace(run[3], H.TACTICAL_CHANGE, supporting=(trivial,)))
    check = _check(result, H.TACTICAL_CHANGE, trivial)
    assert check.accepted and check.reason == "true but not material support"
    assert _verdict(result, H.TACTICAL_CHANGE) is Status.INSUFFICIENT_EVIDENCE
    assert result.leading is None


def test_evidence_from_an_irrelevant_tool_is_rejected() -> None:
    run = strong_run()
    item = _item(run, ToolName.FIND_TEAM_CHANGES)
    borrowed = FactAssertion(
        evidence_id=item.evidence_id,
        fact="count",
        comparator=Comparator.EQ,
        value=item.facts["count"],
    )
    result = _verify(run, _replace(run[3], H.TACTICAL_CHANGE, supporting=(borrowed,)))
    check = _check(result, H.TACTICAL_CHANGE, borrowed)
    assert not check.accepted and check.reason.startswith("irrelevant")
    assert not result.gates["relevant"]


# --- contradictions ------------------------------------------------------------------------


def test_uncited_contradictions_in_the_pool_still_block_support() -> None:
    """Natural variation is contradicted by the Strong sustained change in the pool even when
    the agent hides that contradiction and claims natural variation is supported."""
    run = strong_run()
    support = _hypothesis(run[3], H.TACTICAL_CHANGE).supporting
    hidden = _replace(
        run[3], H.NATURAL_VARIATION, status=Status.SUPPORTED, contradicting=(), supporting=support
    )
    hidden = hidden.model_copy(update={"leading": H.NATURAL_VARIATION})
    result = _verify(run, hidden)
    nv = next(v for v in result.hypotheses if v.kind is H.NATURAL_VARIATION)
    assert nv.verified is not Status.SUPPORTED
    assert nv.contradicting_evidence_ids
    assert result.leading is None


def test_contradicted_requires_a_cited_material_contradiction() -> None:
    run = strong_run()
    forged = _replace(run[3], H.OPPONENT_DRIVEN, status=Status.CONTRADICTED, contradicting=())
    result = _verify(run, forged)
    assert _verdict(result, H.OPPONENT_DRIVEN) is Status.INSUFFICIENT_EVIDENCE


# --- alternatives and strength -------------------------------------------------------------


def test_dropping_alternatives_fails_the_alternatives_gate() -> None:
    run = strong_run()
    lead = _hypothesis(run[3], H.TACTICAL_CHANGE)
    dropped = run[3].model_copy(
        update={"hypotheses": (lead,), "proposed_strength": ClaimStrength.SUPPORTED}
    )
    result = _verify(run, dropped)
    assert not result.gates["alternatives"]
    assert result.strength.rank <= ClaimStrength.HYPOTHESISED.rank
    unassessed = [v for v in result.hypotheses if v.proposed is None]
    assert unassessed
    assert all(v.verified is Status.INSUFFICIENT_EVIDENCE for v in unassessed)


def test_the_verifier_never_upgrades() -> None:
    run = strong_run()
    modest = run[3].model_copy(update={"proposed_strength": ClaimStrength.OBSERVED})
    assert _verify(run, modest).strength is ClaimStrength.OBSERVED
    insufficient = _replace(run[3], H.TACTICAL_CHANGE, status=Status.INSUFFICIENT_EVIDENCE)
    result = _verify(run, insufficient.model_copy(update={"leading": None}))
    assert _verdict(result, H.TACTICAL_CHANGE) is Status.INSUFFICIENT_EVIDENCE


# --- temporal and triggers -----------------------------------------------------------------


def test_a_verified_trigger_contradicts_the_untriggered_explanation() -> None:
    run = triggered_run()
    result = _verify(run)
    assert result.leading is H.SCORE_STATE_RESPONSE
    assert result.gates["temporal"]
    tactical = next(v for v in result.hypotheses if v.kind is H.TACTICAL_CHANGE)
    assert tactical.verified is Status.CONTRADICTED


def test_a_cause_after_the_change_fails_the_temporal_gate() -> None:
    ws, case, pool, assessment = run = triggered_run()
    later = next(k for k in ws.key_events if k.bin_index > case.trigger_window[1])
    moved = tuple(
        e.model_copy(update={"facts": {**e.facts, "nearest_event_id": later.event_id}})
        if e.tool is ToolName.CHECK_GAME_STATE_RESPONSE
        else e
        for e in pool
    )
    result = _verify(run, assessment, moved)
    assert _verdict(result, H.SCORE_STATE_RESPONSE) is not Status.SUPPORTED
    notes = next(v.notes for v in result.hypotheses if v.kind is H.SCORE_STATE_RESPONSE)
    assert any("precedes" in n for n in notes)
