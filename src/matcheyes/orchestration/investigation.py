"""Stage 4 orchestration: an explicit, bounded investigation per Stage 3 candidate.

    case file (deterministic) -> Investigator.plan -> tools -> Investigator.assess
    -> Challenger.challenge -> [tools -> Investigator.assess] -> Verifier -> conclusion

Every step is a plain function call with a trace record; there is no open-ended agent loop. The
challenge round runs at most once, tool calls are capped, and a wall-clock deadline is checked
between steps. Failures degrade safely: if the Investigator cannot produce a valid plan or
assessment, the Stage 3 candidate is kept and the explanation is marked unavailable; if the
Challenger fails, the assessment is verified as it stands (untested alternatives then block any
claim above HYPOTHESISED).
"""

import time
from collections.abc import Callable, Iterable

from pydantic import Field

from matcheyes.agents.casefile import CaseFile, build_case_file
from matcheyes.agents.contracts import (
    Assessment,
    Challenge,
    EvidenceItem,
    EvidenceRequest,
    FinalInsight,
    HypothesisKind,
    VerificationResult,
)
from matcheyes.agents.narrative import conclude, unavailable
from matcheyes.agents.reasoning import ReasoningModel, RuleBasedReasoner
from matcheyes.agents.roles import Attempt, Challenger, Investigator, RoleFailureError
from matcheyes.agents.tools import MatchWorkspace, ToolBox, ToolError
from matcheyes.agents.verification import Verifier
from matcheyes.analytics.analysis import MatchAnalysis
from matcheyes.analytics.contextual import ContextualAnalysis
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.match import ObservableMatch
from matcheyes.orchestration.trace import Component, Tracer, TraceRecord, TraceStatus

INVESTIGATION_VERSION = "0.1.0"


class InvestigationConfig(DomainModel):
    min_level: EvidenceLevel = Field(
        default=EvidenceLevel.WEAK,
        description="Candidates below this Stage 3 level are not investigated: context already "
        "removed them.",
    )
    model_attempts: int = Field(default=2, ge=1, le=3)
    max_tool_calls: int = Field(default=30, ge=1, le=100)
    deadline_s: float = Field(default=60.0, gt=0)
    challenge: bool = Field(
        default=True,
        description="Run the Challenger round. Off only to measure what the Challenger adds.",
    )


class InvestigationRecord(DomainModel):
    final: FinalInsight
    verification: VerificationResult | None
    trace: tuple[TraceRecord, ...]


class MatchInvestigation(DomainModel):
    investigation_version: str = INVESTIGATION_VERSION
    match_id: Identifier
    model: str
    config: InvestigationConfig
    records: tuple[InvestigationRecord, ...]
    not_investigated: tuple[Identifier, ...] = Field(description="Below the minimum level.")


class _DeadlineError(Exception):
    pass


class Orchestrator:
    def __init__(
        self,
        workspace: MatchWorkspace,
        model: ReasoningModel,
        config: InvestigationConfig | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.ws = workspace
        self.model = model
        self.config = config or InvestigationConfig()
        self.clock = clock
        self.toolbox = ToolBox(workspace)
        self.verifier = Verifier(workspace)

    def investigate(self, candidate_id: Identifier) -> InvestigationRecord:
        iid = f"inv-{self.ws.info.match_id}-{candidate_id}"
        tracer = Tracer(iid, candidate_id, self.clock)
        start = self.clock()
        deadline = start + self.config.deadline_s
        case = build_case_file(self.ws, candidate_id, iid)
        tracer.record(
            "match_analyst",
            "case_file",
            "ok",
            (self.clock() - start) * 1000,
            detail="plausible: " + ", ".join(k.value for k in case.plausible),
        )
        run = _Run(self, case, tracer, deadline)
        return run.execute()

    def investigate_all(self, candidate_ids: Iterable[Identifier]) -> list[InvestigationRecord]:
        return [self.investigate(c) for c in candidate_ids]


class _Run:
    """State of one investigation. Kept separate so the orchestrator itself stays stateless."""

    def __init__(self, orch: Orchestrator, case: CaseFile, tracer: Tracer, deadline: float) -> None:
        self.orch, self.case, self.tracer, self.deadline = orch, case, tracer, deadline
        self.evidence: list[EvidenceItem] = []
        self.executed: dict[str, EvidenceRequest] = {}
        attempts = orch.config.model_attempts
        self.investigator = Investigator(orch.model, attempts)
        self.challenger = Challenger(orch.model, attempts)

    def _check_deadline(self) -> None:
        if self.orch.clock() > self.deadline:
            raise _DeadlineError

    def _attempts(self, component: Component, action: str, attempts: tuple[Attempt, ...]) -> None:
        for a in attempts:
            status: TraceStatus = (
                "ok" if a.ok else ("retry" if a.number < len(attempts) else "failed")
            )
            self.tracer.record(
                component,
                action,
                status,
                a.latency_ms,
                detail=f"model {self.orch.model.name}; attempt {a.number}"
                + (f"; {a.error}" if a.error else ""),
            )

    def _failure(self, component: Component, action: str, failure: RoleFailureError) -> None:
        self._attempts(component, action, failure.attempts)
        self.tracer.record(
            "orchestrator", "role_failure", "failed", detail=f"{action}: {failure.kind}"
        )

    def _fetch(self, requests: tuple[EvidenceRequest, ...], component: Component) -> None:
        for request in requests:
            inputs = dict(request.arguments)
            if request.request_id in self.executed:
                prior = self.executed[request.request_id]
                same = (prior.tool, prior.arguments) == (request.tool, request.arguments)
                self.tracer.record(
                    "toolbox",
                    "tool_call",
                    "skipped",
                    tool=request.tool.value,
                    inputs=inputs,
                    hypothesis=request.hypothesis.value,
                    detail="already fetched" if same else "request_id reused with new arguments",
                )
                continue
            if len(self.executed) >= self.orch.config.max_tool_calls:
                self.tracer.record(
                    "toolbox",
                    "tool_call",
                    "skipped",
                    tool=request.tool.value,
                    inputs=inputs,
                    detail="tool budget exhausted",
                )
                continue
            self._check_deadline()
            self.executed[request.request_id] = request
            start = self.orch.clock()
            evidence_id = f"ev-{len(self.evidence) + 1:02d}"
            try:
                item = self.orch.toolbox.run(request, evidence_id)
            except ToolError as exc:
                self.tracer.record(
                    "toolbox",
                    "tool_call",
                    "failed",
                    (self.orch.clock() - start) * 1000,
                    tool=request.tool.value,
                    inputs=inputs,
                    hypothesis=request.hypothesis.value,
                    detail=f"requested by {component}: {exc}",
                )
                continue
            self.evidence.append(item)
            self.tracer.record(
                "toolbox",
                "tool_call",
                "ok",
                (self.orch.clock() - start) * 1000,
                tool=request.tool.value,
                inputs=inputs,
                hypothesis=request.hypothesis.value,
                result_ref=evidence_id,
                detail=f"requested by {component}; {len(item.event_ids)} event(s)",
            )

    def _unavailable(self, reason: str) -> InvestigationRecord:
        final = unavailable(self.orch.ws, self.case, reason)
        self.tracer.record("narrative", "conclusion", "ok", detail=f"unavailable: {reason}")
        return InvestigationRecord(final=final, verification=None, trace=tuple(self.tracer.records))

    def execute(self) -> InvestigationRecord:
        case = self.case
        try:
            plan, attempts = self.investigator.plan(case)
        except RoleFailureError as failure:
            self._failure("investigator", "plan", failure)
            return self._unavailable(f"investigator plan: {failure.kind}")
        self._attempts("investigator", "plan", attempts)
        tested: tuple[HypothesisKind, ...] = plan.hypotheses
        try:
            self._fetch(plan.requests, "investigator")
            assessment, attempts = self.investigator.assess(case, tuple(self.evidence), tested)
        except _DeadlineError:
            self.tracer.record("orchestrator", "deadline", "failed", detail="before assessment")
            return self._unavailable("deadline exceeded before assessment")
        except RoleFailureError as failure:
            self._failure("investigator", "assess", failure)
            return self._unavailable(f"investigator assessment: {failure.kind}")
        self._attempts("investigator", "assess", attempts)
        if self.orch.config.challenge:
            assessment = self._challenge_round(assessment, tested)
        return self._verify(assessment)

    def _challenge_round(
        self, assessment: Assessment, tested: tuple[HypothesisKind, ...]
    ) -> Assessment:
        case = self.case
        try:
            self._check_deadline()
            challenge: Challenge
            challenge, attempts = self.challenger.challenge(
                case, tuple(self.evidence), tested, assessment
            )
        except _DeadlineError:
            self.tracer.record("orchestrator", "deadline", "failed", detail="before challenge")
            return assessment
        except RoleFailureError as failure:
            self._failure("challenger", "challenge", failure)
            return assessment
        self._attempts("challenger", "challenge", attempts)
        added = tuple(k for k in challenge.alternatives if k not in tested)
        for kind in added:
            self.tracer.record("challenger", "alternative", "ok", hypothesis=kind.value)
        if not added and not challenge.requests:
            return assessment
        try:
            self._fetch(challenge.requests, "challenger")
            revised, attempts = self.investigator.assess(case, tuple(self.evidence), tested + added)
        except _DeadlineError:
            self.tracer.record("orchestrator", "deadline", "failed", detail="during challenge")
            return assessment
        except RoleFailureError as failure:
            self._failure("investigator", "reassess", failure)
            return assessment
        self._attempts("investigator", "reassess", attempts)
        return revised

    def _verify(self, assessment: Assessment) -> InvestigationRecord:
        start = self.orch.clock()
        evidence = tuple(self.evidence)
        verification = self.orch.verifier.verify(self.case, evidence, assessment)
        failed = [g for g, ok in verification.gates.items() if not ok]
        self.tracer.record(
            "verifier",
            "verify",
            "downgraded" if verification.downgrades else "ok",
            (self.orch.clock() - start) * 1000,
            hypothesis=verification.leading.value if verification.leading else None,
            detail=f"strength {verification.strength.value}; failed gates: "
            f"{', '.join(failed) or 'none'}; downgrades: {len(verification.downgrades)}; "
            f"quarantined: {len(verification.quarantined)}",
        )
        final = conclude(self.orch.ws, self.case, evidence, verification)
        self.tracer.record(
            "narrative",
            "conclusion",
            "ok",
            hypothesis=final.leading.value if final.leading else None,
            detail=f"{final.verdict.value}; {final.strength.value}",
        )
        return InvestigationRecord(
            final=final, verification=verification, trace=tuple(self.tracer.records)
        )


def investigate_match(
    match: ObservableMatch,
    model: ReasoningModel | None = None,
    config: InvestigationConfig | None = None,
    stage2: MatchAnalysis | None = None,
    stage3: ContextualAnalysis | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> MatchInvestigation:
    config = config or InvestigationConfig()
    model = model or RuleBasedReasoner()
    ws = MatchWorkspace.build(match, stage2, stage3)
    eligible = [c for c in ws.stage3.candidates if c.level.rank >= config.min_level.rank]
    skipped = tuple(
        c.candidate_id for c in ws.stage3.candidates if c.level.rank < config.min_level.rank
    )
    orchestrator = Orchestrator(ws, model, config, clock)
    records = orchestrator.investigate_all(c.candidate_id for c in eligible)
    return MatchInvestigation(
        match_id=match.info.match_id,
        model=model.name,
        config=config,
        records=tuple(records),
        not_investigated=skipped,
    )
