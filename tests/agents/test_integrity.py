"""Evidence integrity: a quarantine that could bear on an insight flags it, caps it and warns."""

from functools import cache

import pytest

from matcheyes.agents.contracts import (
    INTEGRITY_CAP,
    EvidenceIntegrity,
    EvidenceItem,
    EvidenceRequest,
    HypothesisKind,
    ToolName,
    Verdict,
    VerificationResult,
)
from matcheyes.agents.hypotheses import PRIORITY, RELEVANT_TOOLS
from matcheyes.agents.narrative import INTEGRITY_WARNING, VERIFIED_ON_REMAINING, conclude
from matcheyes.agents.tools import MatchWorkspace, ToolBox
from matcheyes.agents.verification import Verifier
from matcheyes.domain.claims import ClaimStrength
from matcheyes.orchestration.audit import audit_insight
from matcheyes_synth.effects import counterfactual
from matcheyes_synth.generator import generate_match
from matcheyes_synth.scenarios import scenario
from tests.agents.support import reference_run
from tests.agents.test_verification import Run, _item, strong_run

H = EvidenceIntegrity
C = ClaimStrength

EXCLUSION_NOTICE = "some evidence failed provenance checks and was excluded"


def _edited(item: EvidenceItem) -> EvidenceItem:
    return item.model_copy(update={"summary": "Edited after the tool ran."})


def _verify(run: Run, evidence: tuple[EvidenceItem, ...]) -> VerificationResult:
    ws, case, _, assessment = run
    return Verifier(ws).verify(case, evidence, assessment)


def _cited(run: Run) -> set[str]:
    return {a.evidence_id for h in run[3].hypotheses for a in (*h.supporting, *h.contradicting)}


@cache
def supported_run() -> Run:
    """A reference investigation that reaches SUPPORTED: the development S03 twin, seed 10004,
    where a goal explains the change and every alternative is contradicted by evidence."""
    match = generate_match(counterfactual(scenario("S03_game_state_deep_block")), 10004)
    ws = MatchWorkspace.build(match.observable)
    c = ws.candidates["ctx-shift-northmoor-field_tilt-61"]
    case, evidence, assessment, record = reference_run(ws, c)
    assert record.final.strength is C.SUPPORTED
    return ws, case, evidence, assessment


# --- 1. clean ----------------------------------------------------------------------------------


def test_a_clean_investigation_is_intact_and_unchanged() -> None:
    run = strong_run()
    result = _verify(run, run[2])
    assert result.evidence_integrity is H.INTACT
    assert result.quarantined == ()
    final = conclude(run[0], run[1], run[2], result)
    assert final.evidence_integrity is H.INTACT
    assert INTEGRITY_WARNING not in final.narrative
    assert VERIFIED_ON_REMAINING not in final.narrative
    assert EXCLUSION_NOTICE not in final.narrative
    assert "[verified, hypothesised]" in final.narrative
    assert not any("integrity" in d for d in result.downgrades)


# --- 2. irrelevant quarantine -------------------------------------------------------------------


def test_an_uncited_duplicate_whose_original_survives_is_not_flagged() -> None:
    run = strong_run()
    spare = _irrelevant_item(run)
    pool = (*run[2], spare, _edited(spare))
    result = _verify(run, pool)
    assert result.quarantined == (spare.evidence_id,)
    assert result.evidence_integrity is H.UNAFFECTED
    clean = _verify(run, run[2])
    assert (result.leading, result.strength) == (clean.leading, clean.strength)
    final = conclude(run[0], run[1], pool, result)
    assert final.evidence_integrity is H.UNAFFECTED
    assert EXCLUSION_NOTICE in final.narrative
    assert INTEGRITY_WARNING not in final.narrative
    assert audit_insight(run[0], final, result).ok


def _irrelevant_item(run: Run) -> EvidenceItem:
    """A real result from a tool that bears on none of the explanations considered here."""
    ws, case, _, assessment = run
    kinds = [
        k for k in PRIORITY if k in case.plausible or k in {h.kind for h in assessment.hypotheses}
    ]
    bearing = {tool for kind in kinds for tool in RELEVANT_TOOLS[kind]}
    c = ws.candidates[case.candidate_id]
    requests = {
        ToolName.GET_WORKLOAD: {"team_id": c.team_id, "bin": c.bin_index},
        ToolName.GET_SUBSTITUTE_INVOLVEMENT: {
            "team_id": c.team_id,
            "metric": c.metric,
            "start_bin": c.bin_index,
            "end_bin": c.bin_index + 5,
        },
    }
    tool = next(t for t in requests if t not in bearing)
    request = EvidenceRequest(
        request_id="r-extra",
        tool=tool,
        arguments=requests[tool],
        hypothesis=HypothesisKind.NATURAL_VARIATION,
    )
    return ToolBox(ws).run(request, "ev-99")


def test_an_uncited_item_from_a_tool_bearing_on_nothing_considered_is_not_flagged() -> None:
    run = strong_run()
    extra = _irrelevant_item(run)
    result = _verify(run, (*run[2], _edited(extra)))
    assert result.quarantined == ("ev-99",)
    assert result.evidence_integrity is H.UNAFFECTED
    clean = _verify(run, run[2])
    assert (result.leading, result.strength) == (clean.leading, clean.strength)


# --- 3. relevant quarantine ---------------------------------------------------------------------


def test_a_cited_item_that_fails_replay_compromises_the_insight() -> None:
    run = strong_run()
    item = _item(run, ToolName.GET_CANDIDATE_ASSESSMENT)
    assert item.evidence_id in _cited(run)
    pool = tuple(_edited(e) if e is item else e for e in run[2])
    result = _verify(run, pool)
    assert result.quarantined == (item.evidence_id,)
    assert result.evidence_integrity is H.COMPROMISED
    final = conclude(run[0], run[1], pool, result)
    assert final.evidence_integrity is H.COMPROMISED
    assert INTEGRITY_WARNING in final.narrative.splitlines()
    assert audit_insight(run[0], final, result).ok


def test_an_uncited_item_from_a_relevant_tool_is_treated_as_compromised() -> None:
    """Its content is untrusted, so relevance is judged from the tool, never from its facts."""
    run = supported_run()
    spare = next(
        e
        for e in run[2]
        if e.evidence_id not in _cited(run) and e.tool is ToolName.INSPECT_PERSISTENCE
    )
    pool = tuple(_edited(e) if e is spare else e for e in run[2])
    assert _verify(run, pool).evidence_integrity is H.COMPROMISED


def test_a_request_that_no_longer_replays_is_compromised_even_if_uncited() -> None:
    run = strong_run()
    extra = _irrelevant_item(run).model_copy(update={"arguments": {"team_id": "nowhere", "bin": 1}})
    result = _verify(run, (*run[2], extra))
    assert result.evidence_integrity is H.COMPROMISED


# --- 5. no overclaim ---------------------------------------------------------------------------


def test_compromised_evidence_caps_a_supported_claim_even_if_the_rest_still_qualifies() -> None:
    run = supported_run()
    clean = _verify(run, run[2])
    assert clean.strength is C.SUPPORTED
    spare = next(
        e
        for e in run[2]
        if e.evidence_id not in _cited(run) and e.tool is ToolName.INSPECT_PERSISTENCE
    )
    pool = tuple(_edited(e) if e is spare else e for e in run[2])
    result = _verify(run, pool)
    assert result.leading is clean.leading
    assert result.strength is INTEGRITY_CAP
    assert result.eligible_strength is INTEGRITY_CAP
    assert any(d.startswith("evidence integrity compromised") for d in result.downgrades)
    final = conclude(run[0], run[1], pool, result)
    assert final.verdict is Verdict.TENTATIVE
    assert final.strength.rank <= INTEGRITY_CAP.rank
    assert audit_insight(run[0], final, result).ok


def test_losing_the_leading_explanations_support_withholds_it() -> None:
    run = supported_run()
    clean = _verify(run, run[2])
    lead = next(v for v in clean.hypotheses if v.kind is clean.leading)
    support = lead.supporting_evidence_ids[0]
    pool = tuple(_edited(e) if e.evidence_id == support else e for e in run[2])
    result = _verify(run, pool)
    assert result.evidence_integrity is H.COMPROMISED
    assert result.leading is None
    assert result.strength.rank < C.SUPPORTED.rank


@pytest.mark.parametrize("index", range(4))
def test_no_single_corrupted_item_produces_a_stronger_claim(index: int) -> None:
    run = supported_run()
    clean = _verify(run, run[2])
    target = run[2][index]
    pool = tuple(_edited(e) if e is target else e for e in run[2])
    result = _verify(run, pool)
    assert result.strength.rank <= clean.strength.rank
    if result.evidence_integrity is H.COMPROMISED:
        assert result.strength.rank <= INTEGRITY_CAP.rank


# --- 6. narrative -------------------------------------------------------------------------------


def test_the_warning_and_label_follow_the_template() -> None:
    run = supported_run()
    spare = next(
        e
        for e in run[2]
        if e.evidence_id not in _cited(run) and e.tool is ToolName.INSPECT_PERSISTENCE
    )
    pool = tuple(_edited(e) if e is spare else e for e in run[2])
    final = conclude(run[0], run[1], pool, _verify(run, pool))
    lines = final.narrative.splitlines()
    assert lines[0].startswith("FACT: ")
    assert lines[1].startswith("ANALYSIS: Stage 3 graded this change")
    assert lines[2] == INTEGRITY_WARNING
    assert lines[3].startswith(f"AI INTERPRETATION [{VERIFIED_ON_REMAINING}, hypothesised]: ")
    assert EXCLUSION_NOTICE not in final.narrative
    assert "[verified, " not in final.narrative


def test_the_auditor_rejects_a_compromised_insight_presented_as_fully_verified() -> None:
    run = supported_run()
    spare = next(
        e
        for e in run[2]
        if e.evidence_id not in _cited(run) and e.tool is ToolName.INSPECT_PERSISTENCE
    )
    pool = tuple(_edited(e) if e is spare else e for e in run[2])
    result = _verify(run, pool)
    final = conclude(run[0], run[1], pool, result)
    assert final.verdict is Verdict.TENTATIVE
    hidden = final.model_copy(
        update={
            "narrative": "\n".join(
                line.replace(VERIFIED_ON_REMAINING, "verified")
                for line in final.narrative.splitlines()
                if line != INTEGRITY_WARNING
            )
        }
    )
    report = audit_insight(run[0], hidden, result, template=False)
    reasons = {f.detail for f in report.findings}
    assert "compromised evidence integrity is not disclosed" in reasons
    assert "verification label does not match evidence integrity" in reasons


def test_the_auditor_rejects_a_compromised_explanation_above_the_cap() -> None:
    run = supported_run()
    final = conclude(run[0], run[1], run[2], _verify(run, run[2]))
    forged = final.model_copy(update={"evidence_integrity": H.COMPROMISED})
    report = audit_insight(run[0], forged, None)
    assert "explanation above the compromised-integrity cap" in {f.detail for f in report.findings}


# --- 7. determinism -----------------------------------------------------------------------------


def test_the_same_corrupted_input_gives_the_same_integrity_and_output() -> None:
    run = supported_run()
    spare = next(e for e in run[2] if e.evidence_id not in _cited(run))
    pool = tuple(_edited(e) if e is spare else e for e in run[2])
    first, second = _verify(run, pool), _verify(run, pool)
    assert first == second
    assert conclude(run[0], run[1], pool, first) == conclude(run[0], run[1], pool, second)
