"""Typed contracts for Stage 6 personalization (ADR-0012).

A `PersonalizedInsight` holds the verified `FinalInsight` it presents (`source`) and copies none
of its truth fields. Construction enforces the truth invariants (`truth_violations`,
docs/personalization.md#truth-invariants); `personalization.audit.audit_view` re-checks the
rendered text independently.
"""

import hashlib
import re
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from matcheyes.agents.contracts import EvidenceIntegrity, FinalInsight, Verdict, VerifiedClaim
from matcheyes.agents.narrative import VERIFIED_ON_REMAINING
from matcheyes.analytics.metrics import METRIC_BY_NAME
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier

PERSONALIZATION_VERSION = "0.1.0"

ProfileId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")]
"""Club and player preferences are identifiers, never free text."""

INTEGRITY_TEXT = (
    "Evidence integrity compromised: evidence relevant to this insight failed provenance "
    "checks and was excluded; the explanation may be incomplete."
)
EXCLUSION_TEXT = (
    "Some evidence failed provenance checks and was excluded; what follows rests on the "
    "remaining evidence only."
)
NO_VERIFIED_INSIGHT = "No verified insight available."
NO_EXPLANATION_TEXT = {
    Verdict.INSUFFICIENT_EVIDENCE: "No verified explanation: there is not enough evidence to "
    "explain this change.",
    Verdict.UNAVAILABLE: "No verified explanation: the investigation did not complete.",
}

LABELLED = frozenset({Verdict.EXPLAINED, Verdict.TENTATIVE, Verdict.NATURAL_VARIATION})
"""Verdicts that carry a verified explanation (or the verified absence of one)."""

LABEL = r"\[(verified|" + VERIFIED_ON_REMAINING + r"), ([a-z]+)\]"
EVIDENCE_ID = r"\bev-\d+\b"


class Audience(StrEnum):
    FAN = "fan"
    BROADCASTER = "broadcaster"
    ANALYST = "analyst"


class Language(StrEnum):
    EN = "en"


class PersonalizationProfile(DomainModel):
    """Who a view is for. Enums and identifiers only: no preference text reaches a template."""

    audience: Audience
    favourite_club_id: ProfileId | None = None
    favourite_player_id: ProfileId | None = None
    favourite_metric: str | None = Field(default=None, description="Relevance only.")
    language: Language = Language.EN

    @field_validator("favourite_metric")
    @classmethod
    def _known_metric(cls, value: str | None) -> str | None:
        if value is not None and value not in METRIC_BY_NAME:
            raise ValueError(f"unknown metric {value!r}")
        return value


class SectionKind(StrEnum):
    FACT = "fact"
    """The Stage 3 statement, verbatim."""
    CONTEXT = "context"
    """Observable context: minute, team, game state."""
    INTEGRITY = "integrity"
    """The compromised-integrity warning, or the exclusion notice."""
    INTERPRETATION = "interpretation"
    """The verified explanation: its label and claim text, with its supporting evidence."""
    CAVEAT = "caveat"
    """The verified claim's uncertainty, verbatim."""
    NO_INSIGHT = "no_insight"
    """No verified explanation: insufficient evidence, or the investigation did not complete."""
    TRIGGER = "trigger"
    """The event a verified triggered explanation cites, by type and minute."""
    GLOSSARY = "glossary"
    """What the metric measures."""
    INVOLVEMENT = "involvement"
    """How many cited events the favourite player acts in."""
    ALTERNATIVES = "alternatives"
    EVIDENCE = "evidence"
    """One verified evidence item: its templated summary and facts."""
    QUARANTINE = "quarantine"
    """Evidence excluded because it failed provenance checks."""
    DETAIL = "detail"
    """Verification detail: claim types, downgrades, Stage 3 basis. Its text may name a
    quarantined ID only as the verifier's record of the exclusion."""
    NARRATIVE = "narrative"
    """The canonical narrative, verbatim."""


class ViewSection(DomainModel):
    kind: SectionKind
    text: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[Identifier, ...] = ()
    mandatory: bool = Field(default=False, description="Never compressed away.")


def fingerprint(final: FinalInsight) -> str:
    """Deterministic identity of a verified insight: SHA-256 of its canonical JSON."""
    return hashlib.sha256(final.model_dump_json().encode("utf-8")).hexdigest()


def lead_claim(final: FinalInsight) -> VerifiedClaim | None:
    return next((c for c in final.claims if c.hypothesis is not None), None)


def factual_claim(final: FinalInsight) -> VerifiedClaim:
    return next(c for c in final.claims if c.hypothesis is None)


def verification_label(final: FinalInsight) -> str | None:
    """The label every view must show for this insight, or None when it has no explanation."""
    lead = lead_claim(final)
    if lead is None or final.verdict not in LABELLED:
        return None
    compromised = final.evidence_integrity is EvidenceIntegrity.COMPROMISED
    return f"[{VERIFIED_ON_REMAINING if compromised else 'verified'}, {lead.strength.value}]"


def _of(sections: tuple[ViewSection, ...], kind: SectionKind) -> list[ViewSection]:
    return [s for s in sections if s.kind is kind]


def _check_integrity(source: FinalInsight, sections: tuple[ViewSection, ...]) -> list[str]:
    found: list[str] = []
    compromised = source.evidence_integrity is EvidenceIntegrity.COMPROMISED
    expected = INTEGRITY_TEXT if compromised else EXCLUSION_TEXT if source.quarantined else None
    integrity = _of(sections, SectionKind.INTEGRITY)
    if expected is None and integrity:
        found.append("integrity section on an insight with nothing excluded")
    if expected is not None and [(s.text, s.mandatory) for s in integrity] != [(expected, True)]:
        found.append("integrity disclosure missing or altered")
    if not compromised and any(
        INTEGRITY_TEXT in s.text for s in sections if s.kind is not SectionKind.NARRATIVE
    ):
        found.append("integrity warning on an insight whose integrity is not compromised")
    return found


def _check_explanation(source: FinalInsight, sections: tuple[ViewSection, ...]) -> list[str]:
    found: list[str] = []
    label, lead = verification_label(source), lead_claim(source)
    interpretation = _of(sections, SectionKind.INTERPRETATION)
    labels = [
        m.group(0)
        for s in sections
        if s.kind is not SectionKind.NARRATIVE
        for m in re.finditer(LABEL, s.text)
    ]
    if label is None or lead is None:
        if (
            interpretation
            or _of(sections, SectionKind.CAVEAT)
            or _of(sections, SectionKind.TRIGGER)
        ):
            found.append(f"explanation shown for a {source.verdict.value} insight")
        if labels:
            found.append("verification label on an insight without an explanation")
        if not any(s.mandatory for s in _of(sections, SectionKind.NO_INSIGHT)):
            found.append("no-verified-explanation section missing")
        return found
    if len(interpretation) != 1 or not interpretation[0].mandatory:
        return [*found, "exactly one mandatory interpretation section is required"]
    shown = interpretation[0]
    if label not in shown.text or any(found_label != label for found_label in labels):
        found.append(f"verification label differs from {label}")
    if lead.text not in shown.text:
        found.append("interpretation does not carry the verified claim text")
    if shown.evidence_ids != lead.supporting_evidence_ids:
        found.append("interpretation evidence differs from the claim's supporting evidence")
    caveats = _of(sections, SectionKind.CAVEAT)
    if [(s.text, s.mandatory) for s in caveats] != [(lead.uncertainty, True)]:
        found.append("the claim's uncertainty is missing or altered")
    if _of(sections, SectionKind.NO_INSIGHT):
        found.append("no-verified-explanation section on an explained insight")
    return found


def _check_evidence(source: FinalInsight, sections: tuple[ViewSection, ...]) -> list[str]:
    found: list[str] = []
    pool = {e.evidence_id for e in source.evidence}
    quarantined = set(source.quarantined)
    for s in sections:
        if s.kind is SectionKind.NARRATIVE:
            if s.text != source.narrative:
                found.append("canonical narrative altered")
            continue
        allowed = quarantined if s.kind is SectionKind.QUARANTINE else pool
        named = set(re.findall(EVIDENCE_ID, s.text))
        if s.kind is SectionKind.DETAIL:
            named -= quarantined
        cited = set(s.evidence_ids) | named
        if not cited <= allowed:
            found.append(f"{s.kind.value} section cites {sorted(cited - allowed)}")
    return found


def truth_violations(view: "PersonalizedInsight") -> list[str]:
    """Every way the view departs from the verified truth it holds. Empty for a valid view."""
    source, sections = view.source, view.sections
    found: list[str] = []
    if view.source_fingerprint != fingerprint(source):
        found.append("source fingerprint does not match the source insight")
    facts = _of(sections, SectionKind.FACT)
    if [(s.text, s.mandatory) for s in facts] != [(factual_claim(source).text, True)]:
        found.append("the verified fact is missing or altered")
    found += _check_integrity(source, sections)
    found += _check_explanation(source, sections)
    found += _check_evidence(source, sections)
    return found


class PersonalizedInsight(DomainModel):
    """One audience's view of one verified insight. `source` is the truth; the rest is
    presentation."""

    policy_version: str = PERSONALIZATION_VERSION
    profile: PersonalizationProfile
    source: FinalInsight
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    relevance: int
    relevance_basis: tuple[str, ...]
    sections: tuple[ViewSection, ...] = Field(min_length=1)
    omitted: tuple[str, ...] = Field(default=(), description="Optional content left out.")

    @model_validator(mode="after")
    def _truth_invariants(self) -> Self:
        problems = truth_violations(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self


class AudienceFeed(DomainModel):
    """Every audited insight of a match for one profile. Nothing is dropped silently: an insight
    is in `primary`, in `secondary` (no verified explanation) or in `withheld` (failed audit)."""

    profile: PersonalizationProfile
    primary: tuple[PersonalizedInsight, ...]
    secondary: tuple[PersonalizedInsight, ...]
    withheld: tuple[Identifier, ...] = Field(description="Failed audit_insight; not presented.")
    notice: str | None

    @model_validator(mode="after")
    def _placement(self) -> Self:
        views = (*self.primary, *self.secondary)
        if any(v.profile != self.profile for v in views):
            raise ValueError("a view was built for another profile")
        ids = [v.source.investigation_id for v in views] + list(self.withheld)
        if len(ids) != len(set(ids)):
            raise ValueError("an insight appears more than once")
        for v in self.secondary:
            if v.source.evidence_integrity is EvidenceIntegrity.COMPROMISED:
                raise ValueError("a compromised insight was moved out of the primary feed")
            if v.source.verdict in LABELLED:
                raise ValueError("an explained insight was moved out of the primary feed")
        if self.profile.audience is Audience.ANALYST and self.secondary:
            raise ValueError("the analyst feed shows every insight")
        if (self.notice == NO_VERIFIED_INSIGHT) != (not self.primary):
            raise ValueError("the no-verified-insight notice must mark an empty primary feed")
        return self
