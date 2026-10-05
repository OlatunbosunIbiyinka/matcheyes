"""Typed contracts for the Stage 4 investigation.

Agents exchange only these models. Free text (statements, rationales) is carried for readers but
is never authoritative: every claim that matters is a `FactAssertion` that the deterministic
verifier checks against a tool result, and every tool result resolves to event IDs.

Claim ladder in Stage 4 (docs/agentic-investigation.md#claim-ladder):

* OBSERVED / ASSOCIATED - inherited from Stage 3; the change itself, no explanation established.
* HYPOTHESISED - a verified-supported explanation with temporal precedence, but at least one
  plausible alternative has not been weakened by evidence.
* SUPPORTED - as HYPOTHESISED, on a Strong candidate, with every plausible alternative
  investigated and contradicted by verified evidence. A residual explanation (tactical change:
  no observable trigger, so no evidence beyond the change itself) never reaches SUPPORTED.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from matcheyes.domain.base import DomainModel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.entities import Identifier
from matcheyes.domain.time import MatchInstant

Fact = int | float | str | bool | None
ShortText = Annotated[str, StringConstraints(max_length=400)]
RequestId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
ArgumentName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{0,29}$")]
Arguments = Annotated[
    dict[ArgumentName, int | float | Annotated[str, StringConstraints(max_length=80)]],
    Field(max_length=10),
]

AGENT_CLAIM_CEILING = ClaimStrength.SUPPORTED


class HypothesisKind(StrEnum):
    TACTICAL_CHANGE = "tactical_change"
    SCORE_STATE_RESPONSE = "score_state_response"
    NUMERICAL_CHANGE = "numerical_change"
    PERSONNEL_CHANGE = "personnel_change"
    FORMATION_CHANGE = "formation_change"
    OPPONENT_DRIVEN = "opponent_driven"
    LATE_MATCH_DECLINE = "late_match_decline"
    NATURAL_VARIATION = "natural_variation"


HYPOTHESIS_DESCRIPTIONS: dict[HypothesisKind, str] = {
    HypothesisKind.TACTICAL_CHANGE: "The team changed how it plays without an observable trigger.",
    HypothesisKind.SCORE_STATE_RESPONSE: "The ordinary response to a goal.",
    HypothesisKind.NUMERICAL_CHANGE: "The ordinary response to a dismissal.",
    HypothesisKind.PERSONNEL_CHANGE: "A substitute changed the team's play.",
    HypothesisKind.FORMATION_CHANGE: "An announced formation change reshaped the team.",
    HypothesisKind.OPPONENT_DRIVEN: "The opponent changed first; this is the reaction.",
    HypothesisKind.LATE_MATCH_DECLINE: "Activity declined late in the match (observable "
    "workload proxies only; not a fatigue measurement).",
    HypothesisKind.NATURAL_VARIATION: "Ordinary match variation; no explanation needed.",
}

HYPOTHESIS_HEDGED: dict[HypothesisKind, str] = {
    HypothesisKind.TACTICAL_CHANGE: "The change may reflect a change in how the team plays; "
    "no observable trigger was found.",
    HypothesisKind.SCORE_STATE_RESPONSE: "The change followed a goal and is consistent with the "
    "ordinary response to it.",
    HypothesisKind.NUMERICAL_CHANGE: "The change followed a dismissal and is consistent with the "
    "ordinary response to it.",
    HypothesisKind.PERSONNEL_CHANGE: "The change followed a substitution and is consistent with "
    "the substitute's involvement.",
    HypothesisKind.FORMATION_CHANGE: "The change followed an announced formation change and is "
    "consistent with it.",
    HypothesisKind.OPPONENT_DRIVEN: "The opponent changed first; this change is consistent with "
    "a reaction to it.",
    HypothesisKind.LATE_MATCH_DECLINE: HYPOTHESIS_DESCRIPTIONS[HypothesisKind.LATE_MATCH_DECLINE],
    HypothesisKind.NATURAL_VARIATION: HYPOTHESIS_DESCRIPTIONS[HypothesisKind.NATURAL_VARIATION],
}
"""Claim text below SUPPORTED: precedence and consistency, never causation. Causal wording
(`HYPOTHESIS_DESCRIPTIONS`) is reserved for an explanation whose alternatives all fell."""


class Status(StrEnum):
    """Verification vocabulary, shared by agents' proposals and the verifier's decisions."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ToolName(StrEnum):
    GET_CANDIDATE_ASSESSMENT = "get_candidate_assessment"
    INSPECT_PERSISTENCE = "inspect_persistence"
    CHECK_GAME_STATE_RESPONSE = "check_game_state_response"
    GET_KEY_EVENTS = "get_key_events"
    COMPARE_WINDOWS = "compare_windows"
    GET_SUBSTITUTE_INVOLVEMENT = "get_substitute_involvement"
    GET_WORKLOAD = "get_workload"
    FIND_TEAM_CHANGES = "find_team_changes"


class EvidenceRequest(DomainModel):
    request_id: RequestId
    tool: ToolName
    arguments: Arguments = Field(default_factory=dict)
    hypothesis: HypothesisKind
    purpose: ShortText = ""


class EvidenceItem(DomainModel):
    """A deterministic tool result. `facts` are authoritative; `summary` is templated text."""

    evidence_id: Identifier
    request_id: RequestId
    tool: ToolName
    arguments: Arguments
    team_id: Identifier | None
    span: tuple[int, int] | None = Field(description="Bins the result covers, if any.")
    facts: dict[str, Fact]
    event_ids: tuple[Identifier, ...]
    summary: str


class ToolFailure(DomainModel):
    request_id: RequestId
    tool: str
    error: ShortText


class Comparator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"


class FactAssertion(DomainModel):
    """A checkable claim about one fact of one evidence item."""

    evidence_id: Identifier
    fact: str = Field(max_length=60)
    comparator: Comparator
    value: Fact


class ProposedHypothesis(DomainModel):
    kind: HypothesisKind
    status: Status
    statement: ShortText
    supporting: tuple[FactAssertion, ...] = ()
    contradicting: tuple[FactAssertion, ...] = ()


class InvestigationPlan(DomainModel):
    """Investigator, first turn: which explanations to test, and with what evidence."""

    hypotheses: tuple[HypothesisKind, ...] = Field(min_length=1)
    requests: tuple[EvidenceRequest, ...] = Field(max_length=20)


class Challenge(DomainModel):
    """Challenger: alternatives the assessment has not tested or eliminated, plus evidence
    requests to test them. An empty challenge is a valid answer."""

    alternatives: tuple[HypothesisKind, ...] = ()
    objections: tuple[ShortText, ...] = Field(default=(), max_length=10)
    requests: tuple[EvidenceRequest, ...] = Field(default=(), max_length=20)


class Assessment(DomainModel):
    """Investigator, assessing turn: a status for every hypothesis it tested, and a proposal."""

    hypotheses: tuple[ProposedHypothesis, ...] = Field(min_length=1)
    leading: HypothesisKind | None
    proposed_strength: ClaimStrength
    summary: ShortText = ""


class ClaimType(StrEnum):
    FACTUAL = "factual"
    INTERPRETIVE = "interpretive"
    CAUSAL = "causal"


class AssertionCheck(DomainModel):
    assertion: FactAssertion
    role: Literal["supporting", "contradicting"]
    accepted: bool
    reason: str


class HypothesisVerdict(DomainModel):
    kind: HypothesisKind
    proposed: Status | None = Field(description="None when no agent assessed it.")
    verified: Status
    supporting_evidence_ids: tuple[Identifier, ...]
    contradicting_evidence_ids: tuple[Identifier, ...]
    checks: tuple[AssertionCheck, ...]
    notes: tuple[str, ...]


class EvidenceIntegrity(StrEnum):
    """Whether provenance failures could have changed the insight.

    INTACT: nothing was quarantined. UNAFFECTED: something was quarantined, but nothing that
    could bear on this insight was lost (a duplicate whose original survived, or an uncited
    item whose request replays and whose tool bears on no explanation considered).
    COMPROMISED: evidence that could bear on the insight was lost, so the explanation may be
    incomplete; the claim is capped at HYPOTHESISED.
    """

    INTACT = "intact"
    UNAFFECTED = "unaffected"
    COMPROMISED = "compromised"


INTEGRITY_CAP = ClaimStrength.HYPOTHESISED
"""The strongest claim an insight with compromised evidence integrity may make."""


class VerificationResult(DomainModel):
    hypotheses: tuple[HypothesisVerdict, ...]
    plausible: tuple[HypothesisKind, ...]
    leading: HypothesisKind | None
    proposed_strength: ClaimStrength
    eligible_strength: ClaimStrength
    strength: ClaimStrength = Field(description="min(proposed, eligible): never upgraded.")
    gates: dict[str, bool] = Field(description="Gate name -> passed, for the leading claim.")
    downgrades: tuple[str, ...]
    quarantined: tuple[Identifier, ...] = Field(
        default=(), description="Evidence IDs that failed provenance replay."
    )
    evidence_integrity: EvidenceIntegrity = EvidenceIntegrity.INTACT


class Verdict(StrEnum):
    EXPLAINED = "explained"
    TENTATIVE = "tentative"
    NATURAL_VARIATION = "natural_variation"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNAVAILABLE = "unavailable"


class VerifiedClaim(DomainModel):
    text: str
    claim_type: ClaimType
    hypothesis: HypothesisKind | None
    status: Status
    strength: ClaimStrength
    supporting_evidence_ids: tuple[Identifier, ...]
    contradicting_evidence_ids: tuple[Identifier, ...]
    uncertainty: str


class FinalInsight(DomainModel):
    investigation_id: Identifier
    candidate_id: Identifier
    team_id: Identifier
    at: MatchInstant
    verdict: Verdict
    leading: HypothesisKind | None
    strength: ClaimStrength = Field(description="Strongest claim made, factual or causal.")
    stage3_level: str
    claims: tuple[VerifiedClaim, ...]
    alternatives: tuple[tuple[HypothesisKind, Status], ...]
    evidence: tuple[EvidenceItem, ...]
    event_ids: tuple[Identifier, ...]
    downgrades: tuple[str, ...]
    quarantined: tuple[Identifier, ...] = Field(
        default=(), description="Evidence excluded because it failed provenance replay."
    )
    evidence_integrity: EvidenceIntegrity = EvidenceIntegrity.INTACT
    failure: str | None = None
    narrative: str
