"""Conclusion and narrative: a templated rendering of the *verified* result.

Narrative synthesis is code, not an agent: it may only restate what verification kept, with the
same strength, the same open alternatives and the same evidence IDs. Agent free text (statements,
objections, summaries) is never copied into it, so it cannot carry an injected instruction or an
unverified claim to a reader. Labels follow the project vocabulary: FACT (observable),
ANALYSIS (deterministic), AI INTERPRETATION (an agent's explanation, after verification).
"""

from matcheyes.agents.casefile import CaseFile
from matcheyes.agents.contracts import (
    HYPOTHESIS_DESCRIPTIONS,
    HYPOTHESIS_HEDGED,
    ClaimType,
    EvidenceIntegrity,
    EvidenceItem,
    FinalInsight,
    HypothesisKind,
    Status,
    ToolName,
    Verdict,
    VerificationResult,
    VerifiedClaim,
)
from matcheyes.agents.hypotheses import RESIDUAL
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.moments import Names
from matcheyes.domain.claims import ClaimStrength

H = HypothesisKind

INTEGRITY_WARNING = (
    "ANALYSIS: evidence integrity compromised: evidence relevant to this insight failed "
    "provenance checks and was excluded; the explanation may be incomplete."
)
VERIFIED_ON_REMAINING = "verified on remaining evidence"


def _label(kind: HypothesisKind) -> str:
    return kind.value.replace("_", " ")


def _open_text(leading: HypothesisKind, open_alternatives: list[HypothesisKind]) -> str:
    if open_alternatives:
        return "Not ruled out: " + ", ".join(_label(k) for k in open_alternatives) + "."
    if leading in RESIDUAL:
        return "Alternatives were weakened, but there is no evidence beyond the change itself."
    return "Held at hypothesised: the change is not strong enough to rule out ordinary variation."


def _verdict(verification: VerificationResult) -> Verdict:
    leading = verification.leading
    if leading is None:
        return Verdict.INSUFFICIENT_EVIDENCE
    if leading is H.NATURAL_VARIATION:
        return Verdict.NATURAL_VARIATION
    if verification.strength is ClaimStrength.SUPPORTED:
        return Verdict.EXPLAINED
    if verification.strength is ClaimStrength.HYPOTHESISED:
        return Verdict.TENTATIVE
    return Verdict.INSUFFICIENT_EVIDENCE


def conclude(
    ws: MatchWorkspace,
    case: CaseFile,
    evidence: tuple[EvidenceItem, ...],
    verification: VerificationResult,
) -> FinalInsight:
    candidate = ws.candidates[case.candidate_id]
    verdict = _verdict(verification)
    by_kind = {v.kind: v for v in verification.hypotheses}
    leading = verification.leading if verdict in (Verdict.EXPLAINED, Verdict.TENTATIVE) else None
    if verdict is Verdict.NATURAL_VARIATION:
        leading = H.NATURAL_VARIATION
    alternatives = tuple((v.kind, v.verified) for v in verification.hypotheses if v.kind != leading)
    open_alternatives = [k for k, s in alternatives if s is not Status.CONTRADICTED]
    factual_ids = tuple(
        e.evidence_id
        for e in evidence
        if e.tool is ToolName.GET_CANDIDATE_ASSESSMENT
        and e.evidence_id not in verification.quarantined
    )
    claims = [
        VerifiedClaim(
            text=case.statement,
            claim_type=ClaimType.FACTUAL,
            hypothesis=None,
            status=Status.SUPPORTED,
            strength=case.strength,
            supporting_evidence_ids=factual_ids,
            contradicting_evidence_ids=(),
            uncertainty=f"Stage 3 evidence level {case.level}; {case.persistence}.",
        )
    ]
    support_ids: tuple[str, ...] = ()
    if leading is not None:
        lead = by_kind[leading]
        support_ids = lead.supporting_evidence_ids
        if verdict is Verdict.EXPLAINED:
            uncertainty = (
                "Every plausible alternative was weakened by evidence. Based on observable "
                "events only; intent is not observable."
            )
        elif verdict is Verdict.TENTATIVE:
            uncertainty = _open_text(leading, open_alternatives)
        else:
            uncertainty = "No explanation beyond ordinary variation is needed."
        explained = verdict is Verdict.EXPLAINED
        claims.append(
            VerifiedClaim(
                text=(HYPOTHESIS_DESCRIPTIONS if explained else HYPOTHESIS_HEDGED)[leading],
                claim_type=ClaimType.CAUSAL if explained else ClaimType.INTERPRETIVE,
                hypothesis=leading,
                status=lead.verified,
                strength=verification.strength
                if leading is not H.NATURAL_VARIATION
                else ClaimStrength.OBSERVED,
                supporting_evidence_ids=lead.supporting_evidence_ids,
                contradicting_evidence_ids=lead.contradicting_evidence_ids,
                uncertainty=uncertainty,
            )
        )
    used = {i for v in verification.hypotheses for i in v.supporting_evidence_ids}
    event_ids = list(candidate.event_ids)
    for item in evidence:
        if item.evidence_id in verification.quarantined:
            continue
        if item.evidence_id in used:
            event_ids.extend(item.event_ids)
    final = FinalInsight(
        investigation_id=case.investigation_id,
        candidate_id=case.candidate_id,
        team_id=case.team_id,
        at=candidate.at,
        verdict=verdict,
        leading=leading,
        strength=max((c.strength for c in claims), key=lambda s: s.rank),
        stage3_level=case.level,
        claims=tuple(claims),
        alternatives=alternatives,
        evidence=tuple(e for e in evidence if e.evidence_id not in verification.quarantined),
        event_ids=tuple(dict.fromkeys(event_ids)),
        downgrades=verification.downgrades,
        quarantined=verification.quarantined,
        evidence_integrity=verification.evidence_integrity,
        narrative="",
    )
    return final.model_copy(update={"narrative": render(ws, case, final, support_ids)})


def unavailable(ws: MatchWorkspace, case: CaseFile, reason: str) -> FinalInsight:
    """Safe degradation: keep the Stage 3 candidate, say plainly that no explanation exists."""
    candidate = ws.candidates[case.candidate_id]
    final = FinalInsight(
        investigation_id=case.investigation_id,
        candidate_id=case.candidate_id,
        team_id=case.team_id,
        at=candidate.at,
        verdict=Verdict.UNAVAILABLE,
        leading=None,
        strength=case.strength,
        stage3_level=case.level,
        claims=(
            VerifiedClaim(
                text=case.statement,
                claim_type=ClaimType.FACTUAL,
                hypothesis=None,
                status=Status.SUPPORTED,
                strength=case.strength,
                supporting_evidence_ids=(),
                contradicting_evidence_ids=(),
                uncertainty=f"Stage 3 evidence level {case.level}.",
            ),
        ),
        alternatives=(),
        evidence=(),
        event_ids=candidate.event_ids,
        downgrades=(),
        failure=reason,
        narrative="",
    )
    return final.model_copy(update={"narrative": render(ws, case, final, ())})


def render(
    ws: MatchWorkspace, case: CaseFile, final: FinalInsight, support_ids: tuple[str, ...]
) -> str:
    team = Names(ws.info).team(case.team_id)
    lines = [f"FACT: {case.statement}"]
    if final.verdict is Verdict.UNAVAILABLE:
        lines.append(
            f"ANALYSIS: Stage 3 graded this change {case.level}. "
            "Explanation unavailable: the investigation did not complete."
        )
        return "\n".join(lines)
    considered = len(final.alternatives) + (1 if final.leading else 0)
    weakened = [_label(k) for k, s in final.alternatives if s is Status.CONTRADICTED]
    open_ = [_label(k) for k, s in final.alternatives if s is not Status.CONTRADICTED]
    lines.append(
        f"ANALYSIS: Stage 3 graded this change {case.level}; {considered} explanations were "
        f"investigated."
    )
    compromised = final.evidence_integrity is EvidenceIntegrity.COMPROMISED
    if compromised:
        lines.append(INTEGRITY_WARNING)
    elif final.quarantined:
        lines.append(
            "ANALYSIS: some evidence failed provenance checks and was excluded; what follows "
            "rests on the remaining evidence only."
        )
    verified = VERIFIED_ON_REMAINING if compromised else "verified"
    cite = f" (evidence {', '.join(support_ids)})" if support_ids else ""
    if final.verdict is Verdict.EXPLAINED and final.leading:
        lines.append(
            f"AI INTERPRETATION [{verified}, {final.strength.value}]: for {team}, "
            f"{HYPOTHESIS_DESCRIPTIONS[final.leading].lower()}{cite} "
            f"Weakened by evidence: {', '.join(weakened) or 'none'}."
        )
    elif final.verdict is Verdict.TENTATIVE and final.leading:
        open_kinds = [k for k, s in final.alternatives if s is not Status.CONTRADICTED]
        lines.append(
            f"AI INTERPRETATION [{verified}, {final.strength.value}]: for {team}, "
            f"the best-supported explanation is {_label(final.leading)}{cite}. "
            + _open_text(final.leading, open_kinds)
        )
    elif final.verdict is Verdict.NATURAL_VARIATION:
        lines.append(
            f"AI INTERPRETATION [{verified}, observed]: consistent with ordinary match variation"
            f"{cite}; no causal explanation is claimed."
        )
    else:
        lines.append(
            "AI INTERPRETATION: insufficient evidence to explain this change. "
            f"Open: {', '.join(open_) or 'none'}."
        )
    return "\n".join(lines)
