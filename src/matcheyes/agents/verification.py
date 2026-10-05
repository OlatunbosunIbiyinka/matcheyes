"""The verifier: deterministic gates between agent proposals and any claim a reader sees.

It is code, not an agent. A model checking a model's reasoning shares its failure modes; these
gates re-derive everything from tool results and Stage 3, and can only keep or downgrade.

Gates (docs/agentic-investigation.md#verification):

1. factual - every cited assertion names a real evidence item and fact, and is true.
2. traceable - a supported explanation rests on at least one evidence item with event IDs.
3. temporal - a triggered explanation's cause (looked up by the verifier) precedes the change.
4. relevant - cited evidence comes from a tool, team, candidate and period that bear on it.
5. contradictions - material contradictions anywhere in the evidence pool (cited or not) block
   SUPPORTED; Stage 3's own contradictions (transient, reversed, contradicted pattern) cap a
   causal explanation at PARTIALLY_SUPPORTED.
6. alternatives - every plausible alternative was assessed.
7. strength_eligible - the proposed claim strength is within what the evidence allows.
8. no_unsupported_assertions - no cited assertion was rejected.
9. provenance - every evidence item replays: re-running its recorded request against the match
   reproduces it exactly. Items that do not (altered facts, swapped or fabricated event IDs,
   evidence from another match) are quarantined: removed from the pool and from the final
   insight, and any assertion citing them is rejected. If the lost evidence could bear on the
   insight, its evidence integrity is COMPROMISED and the claim is capped at HYPOTHESISED:
   compromised evidence can never produce a stronger claim (see `Verifier.integrity`).

Statuses: an assertion counts only if it is true *and* material: what it states must entail a
rule in `hypotheses.SUPPORT_GROUPS` / `CONTRADICTIONS`, and the rule's conditions must hold on
the actual facts. A true but trivial assertion cannot promote an explanation.
"""

from dataclasses import dataclass, field

from pydantic import ValidationError

from matcheyes.agents.casefile import CaseFile
from matcheyes.agents.contracts import (
    AGENT_CLAIM_CEILING,
    INTEGRITY_CAP,
    AssertionCheck,
    Assessment,
    EvidenceIntegrity,
    EvidenceItem,
    EvidenceRequest,
    FactAssertion,
    HypothesisKind,
    HypothesisVerdict,
    ProposedHypothesis,
    Status,
    VerificationResult,
)
from matcheyes.agents.hypotheses import (
    CONTRADICTIONS,
    PRIORITY,
    RELEVANT_TOOLS,
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
from matcheyes.agents.tools import MatchWorkspace, ToolBox, ToolError
from matcheyes.analytics.contextual import ContextualCandidate
from matcheyes.analytics.persistence import Persistence
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.claims import ClaimStrength

H = HypothesisKind
GATES = (
    "factual",
    "traceable",
    "temporal",
    "relevant",
    "contradictions",
    "alternatives",
    "strength_eligible",
    "no_unsupported_assertions",
    "provenance",
)
NOT_ASSESSED = "not assessed by any agent"
DUPLICATE = "duplicate evidence id"
NO_REPLAY = "request does not replay on this match"
REPLAY_DIFFERS = "replay differs from the recorded result"


@dataclass
class _Reading:
    checks: list[AssertionCheck] = field(default_factory=list)
    groups: set[int] = field(default_factory=set)
    support_items: dict[str, EvidenceItem] = field(default_factory=dict)
    contra_ids: set[str] = field(default_factory=set)
    cited_contra: bool = False
    false_or_unknown: bool = False
    irrelevant: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return any(not c.accepted for c in self.checks)


def _material(
    rules: tuple[Rule, ...],
    item: EvidenceItem,
    assertion: FactAssertion,
    ws: MatchWorkspace,
    c: ContextualCandidate,
) -> bool:
    return any(
        r.tool is item.tool
        and r.test.fact == assertion.fact
        and entails(assertion.comparator, assertion.value, r.test)
        and r.holds(item.facts)
        and (not r.absence or covers_absence(ws, c, item))
        for r in rules
    )


def _replay_request(item: EvidenceItem) -> EvidenceRequest:
    return EvidenceRequest(
        request_id=item.request_id,
        tool=item.tool,
        arguments=item.arguments,
        hypothesis=H.NATURAL_VARIATION,
    )


class Verifier:
    def __init__(self, ws: MatchWorkspace) -> None:
        self.ws = ws
        self.toolbox = ToolBox(ws)

    def provenance(
        self, evidence: tuple[EvidenceItem, ...]
    ) -> tuple[tuple[EvidenceItem, ...], dict[str, str]]:
        """Items that replay exactly, and the reason each other item was quarantined."""
        kept: list[EvidenceItem] = []
        quarantined: dict[str, str] = {}
        seen: set[str] = set()
        for item in evidence:
            if item.evidence_id in seen:
                quarantined[item.evidence_id] = DUPLICATE
                continue
            seen.add(item.evidence_id)
            try:
                replay = self.toolbox.run(_replay_request(item), item.evidence_id)
            except (ToolError, ValidationError):
                quarantined[item.evidence_id] = NO_REPLAY
                continue
            if replay != item:
                quarantined[item.evidence_id] = REPLAY_DIFFERS
                continue
            kept.append(item)
        return tuple(kept), quarantined

    def _check(
        self,
        kind: HypothesisKind,
        c: ContextualCandidate,
        pool: dict[str, EvidenceItem],
        assertion: FactAssertion,
        role: str,
        reading: _Reading,
        quarantined: dict[str, str],
    ) -> None:
        def record(accepted: bool, reason: str) -> None:
            reading.checks.append(
                AssertionCheck(
                    assertion=assertion,
                    role="supporting" if role == "supporting" else "contradicting",
                    accepted=accepted,
                    reason=reason,
                )
            )

        if assertion.evidence_id in quarantined:
            reading.false_or_unknown = True
            cause = quarantined[assertion.evidence_id]
            return record(False, f"evidence failed provenance: {cause}")
        item = pool.get(assertion.evidence_id)
        if item is None:
            reading.false_or_unknown = True
            return record(False, "unknown evidence id")
        if assertion.fact not in item.facts:
            reading.false_or_unknown = True
            return record(False, f"{item.tool.value} reports no fact '{assertion.fact}'")
        actual = item.facts[assertion.fact]
        if not compare(actual, assertion.comparator, assertion.value):
            reading.false_or_unknown = True
            return record(False, f"false: actual value is {actual!r}")
        why = irrelevance(self.ws, c, kind, item)
        if why is not None:
            reading.irrelevant = True
            return record(False, f"irrelevant: {why}")
        if role == "supporting":
            hit = False
            for index, group in enumerate(SUPPORT_GROUPS[kind]):
                if _material(group, item, assertion, self.ws, c):
                    reading.groups.add(index)
                    hit = True
            if hit:
                reading.support_items[item.evidence_id] = item
                return record(True, "true and material")
            return record(True, "true but not material support")
        if _material(CONTRADICTIONS[kind], item, assertion, self.ws, c):
            reading.contra_ids.add(item.evidence_id)
            reading.cited_contra = True
            return record(True, "true and material")
        return record(True, "true but not a material contradiction")

    def _scan(
        self,
        kind: HypothesisKind,
        c: ContextualCandidate,
        evidence: tuple[EvidenceItem, ...],
        reading: _Reading,
    ) -> None:
        """Material contradictions anywhere in the pool, whether or not an agent cited them."""
        for item in evidence:
            if irrelevance(self.ws, c, kind, item) is not None:
                continue
            for rule in CONTRADICTIONS[kind]:
                if (
                    rule.tool is item.tool
                    and rule.holds(item.facts)
                    and (not rule.absence or covers_absence(self.ws, c, item))
                ):
                    reading.contra_ids.add(item.evidence_id)

    def _temporal(self, kind: HypothesisKind, c: ContextualCandidate, reading: _Reading) -> bool:
        if kind not in TRIGGERED:
            return True
        lo, hi = trigger_window(c)
        if kind is H.OPPONENT_DRIVEN:
            hi = c.bin_index - 1
        fact = TRIGGER_FACTS[kind]
        for item in reading.support_items.values():
            cause = item.facts.get(fact)
            if not isinstance(cause, str):
                continue
            at = self.ws.bin_of(cause)
            if at is not None and lo <= at <= hi:
                return True
        return False

    def _stage3_contradicted(self, c: ContextualCandidate) -> str | None:
        if c.persistence.persistence in (Persistence.TRANSIENT, Persistence.REVERSED):
            return f"the change itself was {c.persistence.persistence.value}"
        if c.pattern is not None and c.pattern.contradicted:
            return "related signals contradict the change"
        return None

    def _status(
        self,
        kind: HypothesisKind,
        proposal: ProposedHypothesis | None,
        c: ContextualCandidate,
        reading: _Reading,
    ) -> tuple[Status, bool]:
        """Verified status and whether the temporal gate passed."""
        if proposal is None:
            reading.notes.append(NOT_ASSESSED)
            return Status.INSUFFICIENT_EVIDENCE, True
        temporal = self._temporal(kind, c, reading)
        groups = len(SUPPORT_GROUPS[kind])
        proposed = proposal.status
        if proposed is Status.SUPPORTED:
            blockers = []
            if len(reading.groups) < groups:
                blockers.append(f"material support for {len(reading.groups)} of {groups} tests")
            if reading.contra_ids:
                blockers.append(f"material contradiction in {sorted(reading.contra_ids)}")
            if reading.rejected:
                blockers.append("a cited assertion was rejected")
            if not temporal:
                blockers.append("no cited cause precedes the change")
            if not any(i.event_ids for i in reading.support_items.values()):
                blockers.append("no supporting evidence resolves to events")
            stage3 = None if kind is H.NATURAL_VARIATION else self._stage3_contradicted(c)
            if stage3:
                blockers.append(stage3)
            if not blockers:
                return Status.SUPPORTED, temporal
            reading.notes.extend(blockers)
            if reading.groups:
                return Status.PARTIALLY_SUPPORTED, temporal
            return Status.INSUFFICIENT_EVIDENCE, temporal
        if proposed is Status.PARTIALLY_SUPPORTED:
            if reading.groups or reading.contra_ids:
                return Status.PARTIALLY_SUPPORTED, temporal
            reading.notes.append("no material evidence either way")
            return Status.INSUFFICIENT_EVIDENCE, temporal
        if proposed is Status.CONTRADICTED:
            if reading.cited_contra and not reading.rejected:
                return Status.CONTRADICTED, temporal
            reading.notes.append("no cited material contradiction")
            return Status.INSUFFICIENT_EVIDENCE, temporal
        return Status.INSUFFICIENT_EVIDENCE, temporal

    def verify(
        self,
        case: CaseFile,
        evidence: tuple[EvidenceItem, ...],
        assessment: Assessment,
    ) -> VerificationResult:
        c = self.ws.candidates[case.candidate_id]
        submitted = evidence
        evidence, quarantined = self.provenance(evidence)
        pool = {e.evidence_id: e for e in evidence}
        proposals = {h.kind: h for h in assessment.hypotheses}
        kinds = [k for k in PRIORITY if k in proposals or k in case.plausible]
        integrity = self.integrity(submitted, pool, quarantined, kinds, assessment)
        readings: dict[HypothesisKind, _Reading] = {}
        statuses: dict[HypothesisKind, Status] = {}
        temporal: dict[HypothesisKind, bool] = {}
        for kind in kinds:
            reading = readings[kind] = _Reading()
            proposal = proposals.get(kind)
            if proposal is not None:
                for a in proposal.supporting:
                    self._check(kind, c, pool, a, "supporting", reading, quarantined)
                for a in proposal.contradicting:
                    self._check(kind, c, pool, a, "contradicting", reading, quarantined)
            self._scan(kind, c, evidence, reading)
            statuses[kind], temporal[kind] = self._status(kind, proposal, c, reading)
        self._tactical(proposals, readings, statuses)

        downgrades: list[str] = [
            f"{eid} quarantined: {why}" for eid, why in sorted(quarantined.items())
        ]
        verdicts = []
        for kind in kinds:
            proposal, reading = proposals.get(kind), readings[kind]
            proposed = proposal.status if proposal else None
            if proposed is not None and proposed is not statuses[kind]:
                downgrades.append(
                    f"{kind.value}: {proposed.value} -> {statuses[kind].value} "
                    f"({'; '.join(reading.notes) or 'gates'})"
                )
            verdicts.append(
                HypothesisVerdict(
                    kind=kind,
                    proposed=proposed,
                    verified=statuses[kind],
                    supporting_evidence_ids=tuple(sorted(reading.support_items)),
                    contradicting_evidence_ids=tuple(sorted(reading.contra_ids)),
                    checks=tuple(reading.checks),
                    notes=tuple(reading.notes),
                )
            )

        leading = assessment.leading
        if leading is not None and statuses.get(leading) is not Status.SUPPORTED:
            downgrades.append(f"leading {leading.value} not verified as supported; dropped")
            leading = None
        eligible = self._eligible(case, c, leading, statuses)
        if integrity is EvidenceIntegrity.COMPROMISED and eligible.rank > INTEGRITY_CAP.rank:
            downgrades.append(
                f"evidence integrity compromised: eligible {eligible.value} -> "
                f"{INTEGRITY_CAP.value}"
            )
            eligible = INTEGRITY_CAP
        proposed_strength = assessment.proposed_strength
        strength = min(proposed_strength, eligible, AGENT_CLAIM_CEILING, key=lambda s: s.rank)
        if strength is not proposed_strength:
            downgrades.append(f"strength {proposed_strength.value} -> {strength.value}")

        lead = readings.get(leading) if leading else None
        checks = [chk for r in readings.values() for chk in r.checks]
        gates = {
            "factual": not any(r.false_or_unknown for r in readings.values()),
            "traceable": lead is None or any(i.event_ids for i in lead.support_items.values()),
            "temporal": leading is None or temporal[leading],
            "relevant": not any(r.irrelevant for r in readings.values()),
            "contradictions": lead is None or not lead.contra_ids,
            "alternatives": set(case.plausible) <= set(proposals),
            "strength_eligible": proposed_strength.rank <= eligible.rank,
            "no_unsupported_assertions": all(chk.accepted for chk in checks),
            "provenance": not quarantined,
        }
        return VerificationResult(
            hypotheses=tuple(verdicts),
            plausible=case.plausible,
            leading=leading,
            proposed_strength=proposed_strength,
            eligible_strength=eligible,
            strength=strength,
            gates=gates,
            downgrades=tuple(downgrades),
            quarantined=tuple(sorted(quarantined)),
            evidence_integrity=integrity,
        )

    def integrity(
        self,
        submitted: tuple[EvidenceItem, ...],
        kept: dict[str, EvidenceItem],
        quarantined: dict[str, str],
        kinds: list[HypothesisKind],
        assessment: Assessment,
    ) -> EvidenceIntegrity:
        """COMPROMISED unless every quarantined item demonstrably could not bear on the insight.

        A quarantined item's own content is untrusted, so relevance is never judged from its
        facts: only from whether an agent cited it, whether its original survived, and whether
        its recorded request still replays with a tool that bears on no explanation considered.
        """
        if not quarantined:
            return EvidenceIntegrity.INTACT
        cited = {
            a.evidence_id for h in assessment.hypotheses for a in (*h.supporting, *h.contradicting)
        }
        bearing = {tool for kind in kinds for tool in RELEVANT_TOOLS[kind]}
        for eid, why in quarantined.items():
            if eid in cited:
                return EvidenceIntegrity.COMPROMISED
            if why == DUPLICATE and eid in kept:
                continue
            copies = [e for e in submitted if e.evidence_id == eid]
            if why == REPLAY_DIFFERS and all(e.tool not in bearing for e in copies):
                continue
            return EvidenceIntegrity.COMPROMISED
        return EvidenceIntegrity.UNAFFECTED

    def _tactical(
        self,
        proposals: dict[HypothesisKind, ProposedHypothesis],
        readings: dict[HypothesisKind, _Reading],
        statuses: dict[HypothesisKind, Status],
    ) -> None:
        """TACTICAL_CHANGE means *untriggered*: a verified trigger contradicts it by definition."""
        if H.TACTICAL_CHANGE not in statuses:
            return
        trigger = next(
            (
                k
                for k in PRIORITY
                if k in TRIGGERED
                and k is not H.OPPONENT_DRIVEN
                and statuses.get(k) is Status.SUPPORTED
            ),
            None,
        )
        if trigger is None:
            return
        reading, proposal = readings[H.TACTICAL_CHANGE], proposals.get(H.TACTICAL_CHANGE)
        status = statuses[H.TACTICAL_CHANGE]
        if status is Status.SUPPORTED:
            statuses[H.TACTICAL_CHANGE] = Status.PARTIALLY_SUPPORTED
            reading.notes.append(f"an observable trigger ({trigger.value}) is verified")
            return
        cited = [chk for chk in reading.checks if chk.role == "contradicting"]
        if (
            proposal is not None
            and proposal.status is Status.CONTRADICTED
            and cited
            and all(chk.accepted for chk in cited)
        ):
            statuses[H.TACTICAL_CHANGE] = Status.CONTRADICTED
            reading.notes[:] = [f"explained by a verified observable trigger ({trigger.value})"]

    def _eligible(
        self,
        case: CaseFile,
        c: ContextualCandidate,
        leading: HypothesisKind | None,
        statuses: dict[HypothesisKind, Status],
    ) -> ClaimStrength:
        """The claim ladder. Confidence alone never reaches SUPPORTED: alternatives must fall."""
        if leading is None:
            return case.strength
        if leading is H.NATURAL_VARIATION:
            return ClaimStrength.OBSERVED
        if c.level.rank < EvidenceLevel.WEAK.rank:
            return case.strength
        if leading in RESIDUAL:
            return ClaimStrength.HYPOTHESISED
        alternatives = [k for k in case.plausible if k is not leading]
        eliminated = all(statuses.get(k) is Status.CONTRADICTED for k in alternatives)
        # Natural variation is always plausible and only a Strong change weakens it; the level
        # check states that dependency explicitly rather than leaving it implicit in the rules.
        if eliminated and c.level is EvidenceLevel.STRONG:
            return ClaimStrength.SUPPORTED
        return ClaimStrength.HYPOTHESISED
