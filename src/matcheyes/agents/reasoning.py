"""Reasoning models: what fills the Investigator and Challenger roles.

A `ReasoningModel` receives an `AgentTask` (the case file, the evidence gathered so far and the
step's output contract) and returns raw text that must parse as that contract. Everything a model
returns is untrusted: the roles validate it and the verifier checks every fact it asserts.

`RuleBasedReasoner` is the deterministic reference policy. It is what tests and offline
evaluation run, so results are reproducible and need no credentials. It plays both roles with a
deliberate division of labour: as Investigator it tests the explanations suggested by the
candidate and its nearby key events; as Challenger it insists on every plausible alternative the
Investigator skipped (late-match decline, opponent-driven) and on a persistence check for the
leading explanation. An LLM-backed model (`matcheyes.agents.llm`) fills the same roles through
the same contracts.
"""

from enum import StrEnum
from typing import Protocol

from pydantic import Field

from matcheyes.agents.casefile import CaseFile
from matcheyes.agents.contracts import (
    Assessment,
    Challenge,
    EvidenceItem,
    EvidenceRequest,
    FactAssertion,
    HypothesisKind,
    InvestigationPlan,
    ProposedHypothesis,
    Status,
    ToolName,
)
from matcheyes.agents.hypotheses import (
    CONTRADICTIONS,
    PRIORITY,
    RELEVANT_TOOLS,
    RESIDUAL,
    STRONG_RANK,
    SUPPORT_GROUPS,
    TRIGGERED,
    Rule,
)
from matcheyes.agents.tools import LOOKBACK_BINS
from matcheyes.domain.base import DomainModel
from matcheyes.domain.claims import ClaimStrength

H = HypothesisKind


class Step(StrEnum):
    PLAN = "plan"
    ASSESS = "assess"
    CHALLENGE = "challenge"


ROLE_OF_STEP: dict[Step, str] = {
    Step.PLAN: "investigator",
    Step.ASSESS: "investigator",
    Step.CHALLENGE: "challenger",
}
OUTPUT_OF_STEP: dict[Step, type[DomainModel]] = {
    Step.PLAN: InvestigationPlan,
    Step.ASSESS: Assessment,
    Step.CHALLENGE: Challenge,
}


class AgentTask(DomainModel):
    step: Step
    case: CaseFile
    evidence: tuple[EvidenceItem, ...] = ()
    tested: tuple[HypothesisKind, ...] = Field(default=(), description="Hypotheses under test.")
    assessment: Assessment | None = None
    feedback: str | None = Field(
        default=None, description="Why the previous attempt was rejected, for one retry."
    )


class ModelUnavailableError(Exception):
    """The model could not be reached or refused the request."""


class ReasoningModel(Protocol):
    @property
    def name(self) -> str: ...

    def respond(self, task: AgentTask) -> str: ...


def requests_for(kind: HypothesisKind, case: CaseFile) -> tuple[EvidenceRequest, ...]:
    """The reference policy's evidence requests for one hypothesis."""
    t, n = case.onset_bin, case.match_bins
    lo, hi = max(0, case.trigger_window[0]), min(n, case.trigger_window[1] + 1)
    cid = {"candidate_id": case.candidate_id}

    def req(
        rid: str, tool: ToolName, purpose: str, **arguments: int | float | str
    ) -> EvidenceRequest:
        return EvidenceRequest(
            request_id=rid, tool=tool, arguments=arguments, hypothesis=kind, purpose=purpose
        )

    assessment = req("candidate", ToolName.GET_CANDIDATE_ASSESSMENT, "strength and context", **cid)
    persistence = req("persistence", ToolName.INSPECT_PERSISTENCE, "does it hold", **cid)
    if kind in (H.TACTICAL_CHANGE, H.NATURAL_VARIATION):
        return (assessment, persistence)
    if kind is H.SCORE_STATE_RESPONSE:
        return (
            req("goal-response", ToolName.CHECK_GAME_STATE_RESPONSE, "goal", kind="goal", **cid),
        )
    if kind is H.NUMERICAL_CHANGE:
        return (
            req(
                "dismissal-response",
                ToolName.CHECK_GAME_STATE_RESPONSE,
                "dismissal",
                kind="dismissal",
                **cid,
            ),
        )
    if kind is H.PERSONNEL_CHANGE:
        a_lo, a_hi = case.after_span
        return (
            req(
                "substitutions",
                ToolName.GET_KEY_EVENTS,
                "substitution before the change",
                team_id=case.team_id,
                kind="substitution",
                start_bin=lo,
                end_bin=hi,
                anchor_bin=t,
            ),
            req(
                "substitute-involvement",
                ToolName.GET_SUBSTITUTE_INVOLVEMENT,
                "were substitutes involved",
                team_id=case.team_id,
                metric=case.metric,
                start_bin=a_lo,
                end_bin=a_hi,
            ),
        )
    if kind is H.FORMATION_CHANGE:
        return (
            req(
                "formation",
                ToolName.GET_KEY_EVENTS,
                "formation change before the change",
                team_id=case.team_id,
                kind="formation_change",
                start_bin=lo,
                end_bin=hi,
                anchor_bin=t,
            ),
        )
    if kind is H.OPPONENT_DRIVEN:
        if t == 0:
            return ()
        return (
            req(
                "opponent-changes",
                ToolName.FIND_TEAM_CHANGES,
                "did the opponent change first",
                team_id=case.opponent_id,
                start_bin=max(0, t - LOOKBACK_BINS),
                end_bin=t,
                anchor_bin=t,
                min_level="moderate",
            ),
        )
    return (
        assessment,
        req(
            "workload",
            ToolName.GET_WORKLOAD,
            "exposure of the declining side",
            team_id=case.decline_team_id or case.team_id,
            bin=t,
        ),
    )


def _matches(rule: Rule, items: list[EvidenceItem]) -> EvidenceItem | None:
    return next((i for i in items if i.tool is rule.tool and rule.holds(i.facts)), None)


def _assertion(rule: Rule, item: EvidenceItem) -> FactAssertion:
    return FactAssertion(
        evidence_id=item.evidence_id,
        fact=rule.test.fact,
        comparator=rule.test.comparator,
        value=rule.test.value,
    )


def _evaluate(kind: HypothesisKind, items: list[EvidenceItem]) -> ProposedHypothesis:
    supporting: list[FactAssertion] = []
    met = 0
    for group in SUPPORT_GROUPS[kind]:
        hit = next(((r, i) for r in group if (i := _matches(r, items)) is not None), None)
        if hit is not None:
            met += 1
            supporting.append(_assertion(*hit))
    contradicting = [
        _assertion(r, i) for r in CONTRADICTIONS[kind] if (i := _matches(r, items)) is not None
    ]
    if met == len(SUPPORT_GROUPS[kind]) and not contradicting:
        status = Status.SUPPORTED
    elif contradicting and not met:
        status = Status.CONTRADICTED
    elif met or contradicting:
        status = Status.PARTIALLY_SUPPORTED
    else:
        status = Status.INSUFFICIENT_EVIDENCE
    return ProposedHypothesis(
        kind=kind,
        status=status,
        statement=f"{kind.value.replace('_', ' ')}: {status.value.replace('_', ' ')}",
        supporting=tuple(supporting),
        contradicting=tuple(contradicting),
    )


class RuleBasedReasoner:
    """Deterministic reference policy for both roles. Never sees anything but the task."""

    name = "rule-based-reference-v1"

    def respond(self, task: AgentTask) -> str:
        if task.step is Step.PLAN:
            return self._plan(task.case).model_dump_json()
        if task.step is Step.ASSESS:
            return self._assess(task).model_dump_json()
        return self._challenge(task).model_dump_json()

    def _plan(self, case: CaseFile) -> InvestigationPlan:
        deferred = {H.OPPONENT_DRIVEN, H.LATE_MATCH_DECLINE}
        kinds = tuple(k for k in case.plausible if k not in deferred)
        requests = {r.request_id: r for k in kinds for r in requests_for(k, case)}
        return InvestigationPlan(hypotheses=kinds, requests=tuple(requests.values()))

    def _assess(self, task: AgentTask) -> Assessment:
        case = task.case
        proposed: dict[HypothesisKind, ProposedHypothesis] = {}
        for kind in task.tested:
            ids = {r.request_id for r in requests_for(kind, case)}
            items = [e for e in task.evidence if e.request_id in ids]
            proposed[kind] = _evaluate(kind, items)
        tactical = proposed.get(H.TACTICAL_CHANGE)
        trigger = next(
            (
                p
                for k, p in proposed.items()
                if k in TRIGGERED and k is not H.OPPONENT_DRIVEN and p.status is Status.SUPPORTED
            ),
            None,
        )
        if tactical is not None and trigger is not None:
            tools = {e.evidence_id: e.tool for e in task.evidence}
            relevant = RELEVANT_TOOLS[H.TACTICAL_CHANGE]
            proposed[H.TACTICAL_CHANGE] = tactical.model_copy(
                update={
                    "status": Status.CONTRADICTED,
                    "statement": f"tactical change: an observable trigger "
                    f"({trigger.kind.value}) explains the change",
                    "supporting": (),
                    "contradicting": tuple(
                        a for a in trigger.supporting if tools.get(a.evidence_id) in relevant
                    ),
                }
            )
        leading = next(
            (k for k in PRIORITY if k in proposed and proposed[k].status is Status.SUPPORTED),
            None,
        )
        strength = case.strength
        if leading is H.NATURAL_VARIATION:
            strength = ClaimStrength.OBSERVED
        elif leading is not None:
            others = [p for k, p in proposed.items() if k is not leading]
            eliminated = all(p.status is Status.CONTRADICTED for p in others)
            complete = set(case.plausible) <= set(proposed)
            strength = (
                ClaimStrength.SUPPORTED
                if eliminated
                and complete
                and case.level_rank >= STRONG_RANK
                and leading not in RESIDUAL
                else ClaimStrength.HYPOTHESISED
            )
        ordered = tuple(proposed[k] for k in PRIORITY if k in proposed)
        return Assessment(
            hypotheses=ordered,
            leading=leading,
            proposed_strength=strength,
            summary=f"leading: {leading.value if leading else 'none'}",
        )

    def _challenge(self, task: AgentTask) -> Challenge:
        case, assessment = task.case, task.assessment
        untested = tuple(k for k in case.plausible if k not in task.tested)
        have = {e.request_id for e in task.evidence}
        requests = {
            r.request_id: r
            for k in untested
            for r in requests_for(k, case)
            if r.request_id not in have
        }
        objections = [f"{k.value} is plausible but untested" for k in untested]
        if assessment is not None and assessment.leading not in (None, H.NATURAL_VARIATION):
            if "persistence" not in have:
                check = requests_for(H.NATURAL_VARIATION, case)[1]
                requests.setdefault(check.request_id, check)
            unresolved = [
                p.kind.value
                for p in assessment.hypotheses
                if p.kind is not assessment.leading and p.status is not Status.CONTRADICTED
            ]
            if unresolved:
                objections.append("not eliminated: " + ", ".join(unresolved))
        return Challenge(
            alternatives=untested,
            objections=tuple(objections),
            requests=tuple(requests.values()),
        )
