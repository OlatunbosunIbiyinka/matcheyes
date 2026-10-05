"""The independent claim and narrative auditor."""

from functools import cache

import pytest

from matcheyes.agents.contracts import (
    HYPOTHESIS_DESCRIPTIONS,
    ClaimType,
    HypothesisKind,
    Status,
    Verdict,
    VerifiedClaim,
)
from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.domain.claims import ClaimStrength
from matcheyes.orchestration.audit import (
    CHECKS,
    ClaimCategory,
    audit_insight,
    audit_narrative,
    calibration_findings,
    categorise,
    eligible_strength,
    valid_explanation,
)
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from tests.agents.support import CONTROL, strongest, workspace
from tests.synth.generated import SCENARIO_IDS

H = HypothesisKind
S = ClaimStrength


@cache
def tentative() -> InvestigationRecord:
    ws = workspace(CONTROL)
    record = Orchestrator(ws, RuleBasedReasoner()).investigate(strongest(ws).candidate_id)
    assert record.final.verdict is Verdict.TENTATIVE
    return record


def _failed(record: InvestigationRecord, **update: object) -> set[str]:
    final = record.final.model_copy(update=update)
    return audit_insight(workspace(CONTROL), final, record.verification).failed()


def _narrative(record: InvestigationRecord, edit: str) -> str:
    return record.final.narrative + edit


# --- calibration lexicon ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "strength", "flagged"),
    [
        ("The substitution caused the change.", S.HYPOTHESISED, True),
        ("The substitution caused the change.", S.SUPPORTED, False),
        ("Pressing increased after the substitution.", S.HYPOTHESISED, False),
        ("This was due to the red card.", S.ASSOCIATED, True),
        ("This clearly proves the plan worked.", S.SUPPORTED, True),
        ("The manager decided to press.", S.SUPPORTED, True),
        ("The players were tired.", S.SUPPORTED, True),
        ("Observable workload proxies only; not a fatigue measurement.", S.HYPOTHESISED, False),
        ("The change followed a goal and is consistent with it.", S.HYPOTHESISED, False),
    ],
)
def test_calibration_flags_wording_stronger_than_the_evidence(
    text: str, strength: ClaimStrength, flagged: bool
) -> None:
    assert bool(calibration_findings(text, strength)) is flagged


def test_claim_categories_are_not_collapsed() -> None:
    def claim(
        strength: ClaimStrength, kind: HypothesisKind | None, ctype: ClaimType
    ) -> VerifiedClaim:
        return VerifiedClaim(
            text="x",
            claim_type=ctype,
            hypothesis=kind,
            status=Status.SUPPORTED,
            strength=strength,
            supporting_evidence_ids=(),
            contradicting_evidence_ids=(),
            uncertainty="",
        )

    assert categorise(claim(S.OBSERVED, None, ClaimType.FACTUAL)) is ClaimCategory.OBSERVATION
    assert categorise(claim(S.ASSOCIATED, None, ClaimType.FACTUAL)) is ClaimCategory.ASSOCIATION
    assert (
        categorise(claim(S.HYPOTHESISED, H.PERSONNEL_CHANGE, ClaimType.INTERPRETIVE))
        is ClaimCategory.HYPOTHESIS
    )
    assert (
        categorise(claim(S.SUPPORTED, H.PERSONNEL_CHANGE, ClaimType.CAUSAL))
        is ClaimCategory.SUPPORTED_EXPLANATION
    )
    assert (
        categorise(claim(S.SUPPORTED, H.PERSONNEL_CHANGE, ClaimType.INTERPRETIVE))
        is ClaimCategory.HYPOTHESIS
    )


# --- clean investigations pass ---------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_reference_investigations_pass_every_check(scenario: str) -> None:
    ws = workspace(scenario)
    orchestrator = Orchestrator(ws, RuleBasedReasoner())
    for c in ws.stage3.candidates[:8]:
        record = orchestrator.investigate(c.candidate_id)
        report = audit_insight(ws, record.final, record.verification)
        assert report.ok, report.findings
        assert len(report.categories) == len(record.final.claims)


def test_the_auditor_has_twelve_checks() -> None:
    assert len(CHECKS) == 12 and len(set(CHECKS)) == 12


# --- tampering is caught by the right check --------------------------------------------------


def test_causal_wording_in_a_tentative_narrative_is_flagged() -> None:
    record = tentative()
    narrative = _narrative(record, " The substitution caused the change.")
    assert "unsupported_content" in _failed(record, narrative=narrative)
    report = audit_narrative(
        workspace(CONTROL),
        record.final.model_copy(update={"narrative": narrative}),
        template=False,
    )
    assert any("causal language" in f.detail for f in report.findings)


def test_an_upgraded_strength_label_is_flagged() -> None:
    record = tentative()
    narrative = record.final.narrative.replace("[verified, hypothesised]", "[verified, supported]")
    assert "strength_eligibility" in _failed(record, narrative=narrative)


def test_an_upgraded_explanation_claim_is_flagged() -> None:
    record = tentative()
    lead = record.final.claims[1]
    upgraded = lead.model_copy(
        update={
            "strength": S.SUPPORTED,
            "claim_type": ClaimType.CAUSAL,
            "text": HYPOTHESIS_DESCRIPTIONS[H.TACTICAL_CHANGE],
        }
    )
    failed = _failed(record, claims=(record.final.claims[0], upgraded))
    assert "strength_eligibility" in failed


@cache
def with_open_alternatives() -> tuple[str, InvestigationRecord]:
    for scenario in SCENARIO_IDS:
        ws = workspace(scenario)
        orchestrator = Orchestrator(ws, RuleBasedReasoner())
        for c in ws.stage3.candidates[:8]:
            record = orchestrator.investigate(c.candidate_id)
            final = record.final
            if final.verdict is Verdict.TENTATIVE and "Not ruled out:" in final.narrative:
                return scenario, record
    raise AssertionError("no tentative investigation with open alternatives")


def test_hiding_open_alternatives_is_flagged() -> None:
    scenario, record = with_open_alternatives()
    head = record.final.narrative.split("Not ruled out:")[0]
    final = record.final.model_copy(update={"narrative": head + "Not ruled out: none."})
    report = audit_insight(workspace(scenario), final, record.verification)
    assert "alternatives_handling" in report.failed()


def test_a_forged_elimination_is_flagged() -> None:
    scenario, record = with_open_alternatives()
    open_ = [k for k, s in record.final.alternatives if s is not Status.CONTRADICTED]
    alternatives = tuple(
        (k, Status.CONTRADICTED if k is open_[0] else s) for k, s in record.final.alternatives
    )
    final = record.final.model_copy(update={"alternatives": alternatives})
    report = audit_insight(workspace(scenario), final, record.verification)
    assert "alternatives_handling" in report.failed()


def test_a_named_player_is_flagged() -> None:
    record = tentative()
    ws = workspace(CONTROL)
    name = ws.info.home.bench[0].name
    report = audit_narrative(
        ws,
        record.final.model_copy(update={"narrative": _narrative(record, f" Led by {name}.")}),
        template=False,
    )
    assert any("names player" in f.detail for f in report.findings)


def test_an_invented_number_is_flagged() -> None:
    record = tentative()
    report = audit_narrative(
        workspace(CONTROL),
        record.final.model_copy(update={"narrative": _narrative(record, " Up 987 per cent.")}),
        template=False,
    )
    assert any("987" in f.detail for f in report.findings)


def test_a_wrong_team_is_flagged() -> None:
    record = tentative()
    other = "someone-else"
    assert "team_correctness" in _failed(record, team_id=other)


def test_an_edited_fact_line_is_flagged() -> None:
    record = tentative()
    lines = record.final.narrative.splitlines()
    lines[0] = lines[0].replace("from", "sharply from", 1)
    assert "factual_correctness" in _failed(record, narrative="\n".join(lines))


def test_tampered_evidence_in_the_final_insight_is_flagged() -> None:
    record = tentative()
    item = record.final.evidence[0]
    altered = item.model_copy(update={"summary": "Something else."})
    evidence = (altered, *record.final.evidence[1:])
    assert "evidence_existence" in _failed(record, evidence=evidence)


# --- the independent ladder and valid explanations -------------------------------------------


def test_the_independent_ladder_matches_the_verifier_on_clean_runs() -> None:
    ws = workspace(CONTROL)
    orchestrator = Orchestrator(ws, RuleBasedReasoner())
    for c in ws.stage3.candidates[:8]:
        record = orchestrator.investigate(c.candidate_id)
        v = record.verification
        if v is None or v.leading is None:
            continue
        statuses = {h.kind: h.verified for h in v.hypotheses if h.kind is not v.leading}
        assert eligible_strength(c, c.strength, v.plausible, v.leading, statuses) is (
            v.eligible_strength
        )


def test_a_reference_supported_explanation_is_valid_under_the_rules() -> None:
    record = tentative()
    ws = workspace(CONTROL)
    c = ws.candidates[record.final.candidate_id]
    pool = {e.evidence_id: e for e in record.final.evidence}
    assert valid_explanation(ws, c, H.TACTICAL_CHANGE, pool)
    assert valid_explanation(ws, c, H.TACTICAL_CHANGE, {}) is None
