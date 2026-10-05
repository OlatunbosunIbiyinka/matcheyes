"""Real verified insights of every verdict and integrity state, for Stage 6 tests.

Each sample is produced by the Stage 4/5 pipeline itself (reference reasoner, verifier,
conclusion), never written by hand, so personalization is tested against the truth it will
actually receive.
"""

from dataclasses import dataclass
from functools import cache

from matcheyes.agents.contracts import (
    EvidenceIntegrity,
    EvidenceItem,
    FinalInsight,
    ToolName,
    Verdict,
    VerificationResult,
)
from matcheyes.agents.narrative import conclude, unavailable
from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.agents.verification import Verifier
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from matcheyes.personalization.contracts import (
    EXCLUSION_TEXT,
    INTEGRITY_TEXT,
    NO_EXPLANATION_TEXT,
    Audience,
    PersonalizationProfile,
    PersonalizedInsight,
    SectionKind,
    ViewSection,
    factual_claim,
    fingerprint,
    lead_claim,
    verification_label,
)
from matcheyes_eval.stage6 import preference_profiles
from tests.agents.support import CONTROL, SUBSTITUTION, case_for, workspace
from tests.agents.test_integrity import _cited, _irrelevant_item, supported_run
from tests.agents.test_verification import Run, strong_run


@dataclass(frozen=True)
class Sample:
    name: str
    ws: MatchWorkspace
    final: FinalInsight
    verification: VerificationResult | None

    @property
    def record(self) -> InvestigationRecord:
        return InvestigationRecord(final=self.final, verification=self.verification, trace=())


def _edited(item: EvidenceItem) -> EvidenceItem:
    return item.model_copy(update={"summary": "Edited after the tool ran."})


def _concluded(name: str, run: Run, pool: tuple[EvidenceItem, ...]) -> Sample:
    ws, case, _, assessment = run
    verification = Verifier(ws).verify(case, pool, assessment)
    return Sample(name, ws, conclude(ws, case, pool, verification), verification)


def _spare(run: Run) -> EvidenceItem:
    return next(
        e
        for e in run[2]
        if e.evidence_id not in _cited(run) and e.tool is ToolName.INSPECT_PERSISTENCE
    )


@cache
def explained() -> Sample:
    run = supported_run()
    return _concluded("explained", run, run[2])


@cache
def tentative() -> Sample:
    run = strong_run()
    return _concluded("tentative", run, run[2])


@cache
def compromised() -> Sample:
    """SUPPORTED on clean evidence; an edited relevant item caps it at hypothesised."""
    run = supported_run()
    spare = _spare(run)
    return _concluded("compromised", run, tuple(_edited(e) if e is spare else e for e in run[2]))


@cache
def compromised_withheld() -> Sample:
    """The leading explanation's support fails provenance: no explanation, integrity flagged."""
    run = supported_run()
    clean = Verifier(run[0]).verify(run[1], run[2], run[3])
    lead = next(v for v in clean.hypotheses if v.kind is clean.leading)
    support = lead.supporting_evidence_ids[0]
    pool = tuple(_edited(e) if e.evidence_id == support else e for e in run[2])
    return _concluded("compromised_withheld", run, pool)


@cache
def unaffected() -> Sample:
    run = strong_run()
    spare = _irrelevant_item(run)
    return _concluded("unaffected", run, (*run[2], spare, _edited(spare)))


@cache
def control_records() -> tuple[Sample, ...]:
    ws = workspace(CONTROL)
    orch = Orchestrator(ws, RuleBasedReasoner())
    return tuple(
        Sample(c.candidate_id, ws, r.final, r.verification)
        for c in ws.stage3.candidates
        if c.level.rank >= 1
        for r in (orch.investigate(c.candidate_id),)
    )


def _first(verdict: Verdict) -> Sample:
    sample = next(s for s in control_records() if s.final.verdict is verdict)
    return Sample(verdict.value, sample.ws, sample.final, sample.verification)


@cache
def natural_variation() -> Sample:
    return _first(Verdict.NATURAL_VARIATION)


@cache
def insufficient() -> Sample:
    return _first(Verdict.INSUFFICIENT_EVIDENCE)


@cache
def open_alternatives() -> Sample:
    """A tentative explanation whose alternatives are not ruled out."""
    ws = workspace(SUBSTITUTION)
    orch = Orchestrator(ws, RuleBasedReasoner())
    for c in ws.stage3.candidates:
        if c.level.rank < 1:
            continue
        record = orch.investigate(c.candidate_id)
        lead = lead_claim(record.final)
        if lead is not None and "Not ruled out: " in lead.uncertainty:
            return Sample("open_alternatives", ws, record.final, record.verification)
    raise AssertionError("no tentative insight with open alternatives")


@cache
def unavailable_sample() -> Sample:
    ws = workspace(CONTROL)
    candidate = ws.stage3.candidates[0]
    final = unavailable(ws, case_for(ws, candidate), "investigator plan: invalid_output")
    return Sample("unavailable", ws, final, None)


SAMPLES = (
    explained,
    tentative,
    compromised,
    compromised_withheld,
    unaffected,
    natural_variation,
    insufficient,
    open_alternatives,
    unavailable_sample,
)


def all_samples() -> tuple[Sample, ...]:
    return tuple(make() for make in SAMPLES)


def check_samples() -> None:
    """The fixtures cover what they claim to: every verdict and integrity state."""
    samples = {s.name: s.final for s in all_samples()}
    assert samples["explained"].verdict is Verdict.EXPLAINED
    assert samples["compromised"].evidence_integrity is EvidenceIntegrity.COMPROMISED
    assert samples["compromised"].verdict is Verdict.TENTATIVE
    assert samples["compromised_withheld"].leading is None
    assert samples["compromised_withheld"].evidence_integrity is EvidenceIntegrity.COMPROMISED
    assert samples["unaffected"].evidence_integrity is EvidenceIntegrity.UNAFFECTED
    assert samples["open_alternatives"].verdict is Verdict.TENTATIVE


def profiles(sample: Sample, audience: Audience) -> dict[str, PersonalizationProfile]:
    """Every preference shape Stage 6 must stay truthful under, for one insight."""
    return preference_profiles(sample.final, sample.ws, audience)


def hand_view(final: FinalInsight, profile: PersonalizationProfile) -> PersonalizedInsight:
    """The smallest valid view, built by hand from the truth (not by the renderer)."""
    sections = [ViewSection(kind=SectionKind.FACT, text=factual_claim(final).text, mandatory=True)]
    if final.evidence_integrity is EvidenceIntegrity.COMPROMISED:
        sections.append(
            ViewSection(kind=SectionKind.INTEGRITY, text=INTEGRITY_TEXT, mandatory=True)
        )
    elif final.quarantined:
        sections.append(
            ViewSection(kind=SectionKind.INTEGRITY, text=EXCLUSION_TEXT, mandatory=True)
        )
    label, lead = verification_label(final), lead_claim(final)
    if label is not None and lead is not None:
        sections += [
            ViewSection(
                kind=SectionKind.INTERPRETATION,
                text=f"{label} {lead.text}",
                evidence_ids=lead.supporting_evidence_ids,
                mandatory=True,
            ),
            ViewSection(kind=SectionKind.CAVEAT, text=lead.uncertainty, mandatory=True),
        ]
    else:
        text = NO_EXPLANATION_TEXT.get(final.verdict, NO_EXPLANATION_TEXT[Verdict.UNAVAILABLE])
        sections.append(ViewSection(kind=SectionKind.NO_INSIGHT, text=text, mandatory=True))
    return PersonalizedInsight(
        profile=profile,
        source=final,
        source_fingerprint=fingerprint(final),
        relevance=0,
        relevance_basis=(),
        sections=tuple(sections),
    )
