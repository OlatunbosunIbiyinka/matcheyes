"""Independent claim and narrative auditor: a second, separately written check of a final insight.

The verifier decides what may be claimed; this auditor checks, after the fact, that what *was*
claimed is defensible. It does not call or trust the verifier: it replays every evidence item,
re-derives materiality, timing, contradictions and the claim ladder from the shared rule
definitions in `agents.hypotheses`, and reads the rendered narrative as plain text.

Twelve checks (docs/evidence-audit.md#claim-auditor):

 1. factual_correctness   - the factual claim is Stage 3's statement; cited assertions are true.
 2. evidence_existence    - every cited evidence ID exists and replays exactly.
 3. relevance             - support bears on the explanation (tool, candidate).
 4. materiality           - support satisfies a rule on its actual facts; SUPPORTED covers every
                            support group.
 5. temporal_validity     - a triggered explanation's cause precedes the change; evidence lies in
                            the investigated period.
 6. team_correctness      - the insight is about the candidate's team; support concerns the right
                            team.
 7. metric_correctness    - the insight is about the candidate's metric and time.
 8. comparator_correctness - each accepted material assertion *states* enough to entail its rule.
 9. strength_eligibility  - no claim is stronger than the claim ladder allows.
10. contradiction_handling - no explanation is kept against a material contradiction in the pool.
11. alternatives_handling - every plausible alternative is accounted for; one is only marked
                            contradicted if evidence contradicts it.
12. unsupported_content   - the narrative and claim texts say nothing the case file does not:
                            labels, numbers, IDs, names, causal and certainty language.

Claims are also classified (fact / observation / association / hypothesis / supported
explanation) and the categories are never collapsed: wording allowed for one is checked against
the category the evidence earned.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import ValidationError

from matcheyes.agents.casefile import build_case_file
from matcheyes.agents.contracts import (
    AGENT_CLAIM_CEILING,
    HYPOTHESIS_DESCRIPTIONS,
    HYPOTHESIS_HEDGED,
    INTEGRITY_CAP,
    ClaimType,
    EvidenceIntegrity,
    EvidenceItem,
    EvidenceRequest,
    FinalInsight,
    HypothesisKind,
    Status,
    ToolName,
    Verdict,
    VerificationResult,
    VerifiedClaim,
)
from matcheyes.agents.hypotheses import (
    CONTRADICTIONS,
    RESIDUAL,
    SUPPORT_GROUPS,
    TRIGGER_FACTS,
    TRIGGERED,
    Rule,
    compare,
    covers_absence,
    entails,
    irrelevance,
    trigger_window,
)
from matcheyes.agents.narrative import INTEGRITY_WARNING, VERIFIED_ON_REMAINING, render
from matcheyes.agents.tools import MatchWorkspace, ToolBox, ToolError
from matcheyes.analytics.contextual import ContextualCandidate
from matcheyes.analytics.persistence import Persistence
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.claims import ClaimStrength

H = HypothesisKind

CHECKS = (
    "factual_correctness",
    "evidence_existence",
    "relevance",
    "materiality",
    "temporal_validity",
    "team_correctness",
    "metric_correctness",
    "comparator_correctness",
    "strength_eligibility",
    "contradiction_handling",
    "alternatives_handling",
    "unsupported_content",
)


class ClaimCategory(StrEnum):
    FACT = "fact"
    OBSERVATION = "observation"
    ASSOCIATION = "association"
    HYPOTHESIS = "hypothesis"
    SUPPORTED_EXPLANATION = "supported_explanation"


CAUSAL_TERMS = (
    "caused",
    "causes",
    "because",
    "led to",
    "leads to",
    "resulted in",
    "due to",
    "drove",
    "forced",
    "triggered",
    "changed the",
    "reshaped",
    "this is the reaction",
    "made them",
    "responsible for",
)
"""Language that asserts a cause. Only a SUPPORTED explanation may use it."""

CERTAINTY_TERMS = (
    "clearly",
    "definitely",
    "certainly",
    "undoubtedly",
    "proves",
    "proven",
    "proof",
    "conclusively",
    "obviously",
    "without doubt",
    "guaranteed",
    "always",
)
"""Never allowed: even SUPPORTED means "the evidence supports", not proven (domain/claims.py)."""

INTENT_TERMS = (
    "decided",
    "instructed",
    "ordered to",
    "wanted to",
    "intended",
    "game plan",
    "manager",
    "coach",
    "told to",
    "tired",
    "fatigued",
    "exhausted",
)
"""Unobservable: intent, instructions and physical state are not in event data."""

_EVIDENCE_ID = r"\bev-\d+\b"
_NUMBER = r"\d+(?:\.\d+)?"


def _has(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None


def calibration_findings(text: str, strength: ClaimStrength) -> list[str]:
    """Wording stronger than the verified strength. Applies to any text, including model prose."""
    lowered = text.lower()
    found = [f"certainty language '{t}'" for t in CERTAINTY_TERMS if _has(lowered, t)]
    found += [f"unobservable intent or state '{t}'" for t in INTENT_TERMS if _has(lowered, t)]
    if strength is not ClaimStrength.SUPPORTED:
        found += [
            f"causal language '{t}' at {strength.value}" for t in CAUSAL_TERMS if _has(lowered, t)
        ]
    return found


def categorise(claim: VerifiedClaim) -> ClaimCategory:
    if claim.hypothesis is None:
        if claim.strength is ClaimStrength.OBSERVED:
            return ClaimCategory.OBSERVATION
        return ClaimCategory.ASSOCIATION
    if claim.hypothesis is H.NATURAL_VARIATION:
        return ClaimCategory.OBSERVATION
    if claim.strength is ClaimStrength.SUPPORTED and claim.claim_type is ClaimType.CAUSAL:
        return ClaimCategory.SUPPORTED_EXPLANATION
    return ClaimCategory.HYPOTHESIS


@dataclass(frozen=True)
class Finding:
    check: str
    detail: str
    claim: int | None = None


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    categories: list[ClaimCategory] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def failed(self) -> set[str]:
        return {f.check for f in self.findings}

    def add(self, check: str, detail: str, claim: int | None = None) -> None:
        self.findings.append(Finding(check, detail, claim))


def _holds(rule: Rule, item: EvidenceItem, ws: MatchWorkspace, c: ContextualCandidate) -> bool:
    return (
        rule.tool is item.tool
        and rule.holds(item.facts)
        and (not rule.absence or covers_absence(ws, c, item))
    )


def _replayed(
    ws: MatchWorkspace, final: FinalInsight, report: AuditReport
) -> dict[str, EvidenceItem]:
    toolbox, pool = ToolBox(ws), {}
    for item in final.evidence:
        if item.evidence_id in pool:
            report.add("evidence_existence", f"{item.evidence_id} appears twice")
            continue
        try:
            replay = toolbox.run(
                EvidenceRequest(
                    request_id=item.request_id,
                    tool=item.tool,
                    arguments=item.arguments,
                    hypothesis=H.NATURAL_VARIATION,
                ),
                item.evidence_id,
            )
        except (ToolError, ValidationError):
            report.add("evidence_existence", f"{item.evidence_id} does not replay on this match")
            continue
        if replay != item:
            report.add("evidence_existence", f"{item.evidence_id} differs from its replay")
            continue
        pool[item.evidence_id] = item
    return pool


def _irrelevance_check(reason: str) -> str:
    if "team" in reason:
        return "team_correctness"
    if "period" in reason:
        return "temporal_validity"
    return "relevance"


def _contradictions(
    ws: MatchWorkspace, c: ContextualCandidate, kind: HypothesisKind, pool: dict[str, EvidenceItem]
) -> set[str]:
    return {
        eid
        for eid, item in pool.items()
        if irrelevance(ws, c, kind, item) is None
        and any(_holds(r, item, ws, c) for r in CONTRADICTIONS[kind])
    }


def _precedes(
    ws: MatchWorkspace, c: ContextualCandidate, kind: HypothesisKind, items: list[EvidenceItem]
) -> bool:
    if kind not in TRIGGERED:
        return True
    lo, hi = trigger_window(c)
    if kind is H.OPPONENT_DRIVEN:
        hi = c.bin_index - 1
    causes = [item.facts.get(TRIGGER_FACTS[kind]) for item in items]
    bins = [ws.bin_of(x) for x in causes if isinstance(x, str)]
    return any(b is not None and lo <= b <= hi for b in bins)


def valid_explanation(
    ws: MatchWorkspace,
    c: ContextualCandidate,
    kind: HypothesisKind,
    pool: dict[str, EvidenceItem],
) -> list[EvidenceItem] | None:
    """The items that make `kind` a valid supported explanation under the documented rules, or
    None. Valid means: every support group holds on relevant evidence, its cause precedes the
    change, and no relevant evidence materially contradicts it."""
    relevant = [i for i in pool.values() if irrelevance(ws, c, kind, i) is None]
    used: list[EvidenceItem] = []
    for group in SUPPORT_GROUPS[kind]:
        hits = [i for i in relevant if any(_holds(r, i, ws, c) for r in group)]
        if not hits:
            return None
        used.extend(i for i in hits if i not in used)
    if not _precedes(ws, c, kind, used) or _contradictions(ws, c, kind, pool):
        return None
    if not any(i.event_ids for i in used):
        return None
    stage3_contradicted = c.persistence.persistence in (
        Persistence.TRANSIENT,
        Persistence.REVERSED,
    ) or (c.pattern is not None and c.pattern.contradicted)
    if kind is not H.NATURAL_VARIATION and stage3_contradicted:
        return None
    return used


def eligible_strength(
    c: ContextualCandidate,
    case_strength: ClaimStrength,
    plausible: tuple[HypothesisKind, ...],
    leading: HypothesisKind | None,
    alternatives: dict[HypothesisKind, Status],
) -> ClaimStrength:
    """The claim ladder, written independently of the verifier's `_eligible`."""
    if leading is None:
        return case_strength
    if leading is H.NATURAL_VARIATION:
        return ClaimStrength.OBSERVED
    if c.level.rank < EvidenceLevel.WEAK.rank:
        return case_strength
    if leading in RESIDUAL:
        return ClaimStrength.HYPOTHESISED
    others = [k for k in plausible if k is not leading]
    if c.level is EvidenceLevel.STRONG and all(
        alternatives.get(k) is Status.CONTRADICTED for k in others
    ):
        return ClaimStrength.SUPPORTED
    return ClaimStrength.HYPOTHESISED


def _factual_claim(
    report: AuditReport,
    index: int,
    claim: VerifiedClaim,
    c: ContextualCandidate,
    pool: dict[str, EvidenceItem],
) -> None:
    if claim.text != c.statement:
        report.add("factual_correctness", "factual claim differs from Stage 3's statement", index)
    if claim.claim_type is not ClaimType.FACTUAL:
        report.add("unsupported_content", "the observation is typed as an explanation", index)
    if claim.strength is not c.strength:
        report.add("strength_eligibility", f"factual claim at {claim.strength.value}", index)
    for eid in claim.supporting_evidence_ids:
        item = pool.get(eid)
        if item is None:
            continue
        if item.tool is not ToolName.GET_CANDIDATE_ASSESSMENT:
            report.add("relevance", f"{eid} is not the candidate assessment", index)
            continue
        if item.arguments.get("candidate_id") != c.candidate_id:
            report.add("metric_correctness", f"{eid} assesses another candidate", index)
        if item.team_id not in (None, c.team_id):
            report.add("team_correctness", f"{eid} concerns another team", index)


def _explanation(
    report: AuditReport,
    index: int,
    claim: VerifiedClaim,
    kind: HypothesisKind,
    ws: MatchWorkspace,
    c: ContextualCandidate,
    pool: dict[str, EvidenceItem],
    verification: VerificationResult | None,
) -> None:
    support = [pool[e] for e in claim.supporting_evidence_ids if e in pool]
    for item in support:
        why = irrelevance(ws, c, kind, item)
        if why is not None:
            report.add(_irrelevance_check(why), f"{item.evidence_id}: {why}", index)
            continue
        if not any(_holds(r, item, ws, c) for g in SUPPORT_GROUPS[kind] for r in g):
            report.add("materiality", f"{item.evidence_id} satisfies no support rule", index)
    if claim.status is Status.SUPPORTED and kind is not H.NATURAL_VARIATION:
        for g, group in enumerate(SUPPORT_GROUPS[kind]):
            if not any(_holds(r, item, ws, c) for item in support for r in group):
                report.add("materiality", f"support group {g} of {kind.value} is not met", index)
    if not _precedes(ws, c, kind, support):
        report.add("temporal_validity", "no cited cause precedes the change", index)
    contradicted = _contradictions(ws, c, kind, pool)
    if contradicted and claim.status is Status.SUPPORTED:
        report.add(
            "contradiction_handling",
            f"{kind.value} kept despite material contradiction in {sorted(contradicted)}",
            index,
        )
    if verification is not None:
        verdict = next((v for v in verification.hypotheses if v.kind is kind), None)
        for chk in verdict.checks if verdict else ():
            if not (chk.accepted and chk.reason == "true and material"):
                continue
            a = chk.assertion
            cited = pool.get(a.evidence_id)
            if cited is None:
                continue
            actual = cited.facts.get(a.fact)
            if actual is None or not compare(actual, a.comparator, a.value):
                report.add("factual_correctness", f"assertion on {a.evidence_id} is false", index)
                continue
            rules = SUPPORT_GROUPS[kind] if chk.role == "supporting" else (CONTRADICTIONS[kind],)
            if not any(
                r.tool is cited.tool
                and r.test.fact == a.fact
                and entails(a.comparator, a.value, r.test)
                for g in rules
                for r in g
            ):
                report.add(
                    "comparator_correctness",
                    f"{a.fact} {a.comparator.value} {a.value!r} does not entail a rule",
                    index,
                )
    explained = claim.claim_type is ClaimType.CAUSAL
    if explained and claim.strength is not ClaimStrength.SUPPORTED:
        report.add("strength_eligibility", "causal claim below supported", index)
    expected = (HYPOTHESIS_DESCRIPTIONS if explained else HYPOTHESIS_HEDGED)[kind]
    if claim.text != expected:
        report.add("unsupported_content", "explanation text is not the templated wording", index)
    for finding in calibration_findings(claim.text, claim.strength):
        report.add("unsupported_content", finding, index)


def _label(kind: HypothesisKind) -> str:
    return kind.value.replace("_", " ")


def _listed(line: str, marker: str) -> set[str] | None:
    if marker not in line:
        return None
    tail = line.split(marker, 1)[1].split(".", 1)[0]
    return {p.strip() for p in tail.split(",") if p.strip() and p.strip() != "none"}


def audit_narrative(
    ws: MatchWorkspace,
    final: FinalInsight,
    report: AuditReport | None = None,
    template: bool = True,
) -> AuditReport:
    """Read the rendered narrative as text and check it against the structured insight.

    `template=False` skips exact template conformance, leaving only the text checks that would
    also apply to free-form prose (used to measure those checks on their own)."""
    report = report or AuditReport()
    c = ws.candidates.get(final.candidate_id)
    lines = final.narrative.splitlines()
    if c is None or not lines:
        report.add("unsupported_content", "narrative or candidate missing")
        return report
    if lines[0] != f"FACT: {c.statement}":
        report.add("factual_correctness", "FACT line is not Stage 3's statement")
    lead_claim = next((cl for cl in final.claims if cl.hypothesis is not None), None)
    case = build_case_file(ws, c.candidate_id, final.investigation_id)
    support_ids = lead_claim.supporting_evidence_ids if lead_claim and final.leading else ()
    if template and final.narrative != render(ws, case, final, support_ids):
        report.add("unsupported_content", "narrative departs from the template for this insight")
    for line in lines:
        if not line.startswith(("FACT: ", "ANALYSIS: ", "AI INTERPRETATION")):
            report.add("unsupported_content", f"unlabelled line: {line[:40]}")
    body = "\n".join(lines[1:])
    cited = set(re.findall(_EVIDENCE_ID, body))
    lead = next((cl for cl in final.claims if cl.hypothesis is not None), None)
    allowed_ids = set(lead.supporting_evidence_ids) if lead else set()
    if not cited <= allowed_ids:
        report.add("unsupported_content", f"narrative cites {sorted(cited - allowed_ids)}")
    considered = len(final.alternatives) + (1 if final.leading else 0)
    numbers = set(re.findall(_NUMBER, re.sub(_EVIDENCE_ID, "", body).replace("Stage 3", "")))
    allowed_numbers = set(re.findall(_NUMBER, c.statement)) | {str(considered)}
    if not numbers <= allowed_numbers:
        extra = sorted(numbers - allowed_numbers)
        report.add("unsupported_content", f"numbers not in the case file: {extra}")
    label = re.search(rf"\[(verified|{VERIFIED_ON_REMAINING}), ([a-z]+)\]", body)
    if label and lead and label.group(2) != lead.strength.value:
        report.add("strength_eligibility", f"label {label.group(2)} != {lead.strength.value}")
    compromised = final.evidence_integrity is EvidenceIntegrity.COMPROMISED
    if label and (label.group(1) == VERIFIED_ON_REMAINING) != compromised:
        report.add("unsupported_content", "verification label does not match evidence integrity")
    if compromised and INTEGRITY_WARNING not in lines:
        report.add("unsupported_content", "compromised evidence integrity is not disclosed")
    if final.verdict in (Verdict.EXPLAINED, Verdict.TENTATIVE) and not label:
        report.add("unsupported_content", "explanation without a verified strength label")
    open_ = {_label(k) for k, s in final.alternatives if s is not Status.CONTRADICTED}
    weakened = {_label(k) for k, s in final.alternatives if s is Status.CONTRADICTED}
    for marker, expected in (
        ("Not ruled out: ", open_),
        ("Open: ", open_),
        ("Weakened by evidence: ", weakened),
    ):
        shown = _listed(body, marker)
        if shown is not None and shown != expected:
            report.add("alternatives_handling", f"'{marker.strip()}' lists {sorted(shown)}")
    if final.verdict is Verdict.TENTATIVE and open_ and _listed(body, "Not ruled out: ") is None:
        report.add("alternatives_handling", "open alternatives are not shown")
    for finding in calibration_findings(body, final.strength):
        report.add("unsupported_content", finding)
    lowered = body.lower()
    for sheet in (ws.info.home, ws.info.away):
        for p in (*sheet.starting_xi, *sheet.bench):
            if p.player_id in body or p.name.lower() in lowered:
                report.add("unsupported_content", f"names player {p.player_id}")
        if sheet.club.club_id != c.team_id and sheet.club.name.lower() in lowered:
            report.add("team_correctness", f"mentions {sheet.club.name}, not the candidate's team")
    return report


def audit_insight(
    ws: MatchWorkspace,
    final: FinalInsight,
    verification: VerificationResult | None,
    template: bool = True,
) -> AuditReport:
    report = AuditReport()
    c = ws.candidates.get(final.candidate_id)
    if c is None:
        report.add("metric_correctness", f"{final.candidate_id} is not a Stage 3 candidate")
        return report
    if final.team_id != c.team_id:
        report.add("team_correctness", f"insight is about {final.team_id}, change is {c.team_id}")
    if final.at != c.at:
        report.add("metric_correctness", "insight time differs from the change")
    pool = _replayed(ws, final, report)
    report.categories = [categorise(cl) for cl in final.claims]
    for index, claim in enumerate(final.claims):
        for eid in claim.supporting_evidence_ids + claim.contradicting_evidence_ids:
            if eid not in pool:
                report.add("evidence_existence", f"cites {eid}, not in the replayed pool", index)
        if claim.hypothesis is None:
            _factual_claim(report, index, claim, c, pool)
        else:
            _explanation(report, index, claim, claim.hypothesis, ws, c, pool, verification)
    if final.strength.rank > AGENT_CLAIM_CEILING.rank:
        report.add("strength_eligibility", "above the agent claim ceiling")
    if final.evidence_integrity is EvidenceIntegrity.COMPROMISED and any(
        cl.hypothesis is not None and cl.strength.rank > INTEGRITY_CAP.rank for cl in final.claims
    ):
        report.add("strength_eligibility", "explanation above the compromised-integrity cap")
    if final.quarantined and final.evidence_integrity is EvidenceIntegrity.INTACT:
        report.add("evidence_existence", "evidence was quarantined but integrity is intact")
    if final.verdict is not Verdict.UNAVAILABLE:
        _alternatives(report, ws, c, final, pool)
    audit_narrative(ws, final, report, template)
    return report


def _alternatives(
    report: AuditReport,
    ws: MatchWorkspace,
    c: ContextualCandidate,
    final: FinalInsight,
    pool: dict[str, EvidenceItem],
) -> None:
    case = build_case_file(ws, c.candidate_id, final.investigation_id)
    statuses = dict(final.alternatives)
    covered = set(statuses) | ({final.leading} if final.leading else set())
    missing = [k.value for k in case.plausible if k not in covered]
    if missing:
        report.add("alternatives_handling", f"plausible alternatives not accounted for: {missing}")
    explained_by_trigger = any(
        k in TRIGGERED and k is not H.OPPONENT_DRIVEN
        for k in [*(k for k, s in statuses.items() if s is Status.SUPPORTED), final.leading]
    )
    for kind, status in statuses.items():
        if kind is H.TACTICAL_CHANGE and explained_by_trigger:
            continue
        if status is Status.CONTRADICTED and not _contradictions(ws, c, kind, pool):
            report.add(
                "alternatives_handling", f"{kind.value} marked contradicted without evidence"
            )
    if final.verdict is Verdict.EXPLAINED and any(
        s is not Status.CONTRADICTED for s in statuses.values()
    ):
        report.add("alternatives_handling", "explained while an alternative remains open")
    lead = next((cl for cl in final.claims if cl.hypothesis is not None), None)
    if lead is None:
        return
    eligible = eligible_strength(c, c.strength, case.plausible, final.leading, statuses)
    if lead.strength.rank > eligible.rank:
        report.add(
            "strength_eligibility",
            f"{lead.strength.value} exceeds the ladder ({eligible.value})",
            final.claims.index(lead),
        )
