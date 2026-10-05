"""The two reasoning roles and the guard around every model call.

* Investigator - proposes which explanations to test and which evidence to fetch, then assesses
  each tested explanation against the evidence and proposes a leading one and a claim strength.
* Challenger - attacks the assessment: names plausible alternatives that were not tested or not
  eliminated, and requests the evidence to test them.

Model output is untrusted. Each call is size-limited, parsed against the step's contract, checked
for internal consistency and retried once with the validation error as feedback (without echoing
the model's own output back). A second failure raises `RoleFailureError`; the orchestrator then
degrades safely instead of inventing an answer.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from matcheyes.agents.casefile import CaseFile
from matcheyes.agents.contracts import (
    Assessment,
    Challenge,
    EvidenceItem,
    HypothesisKind,
    InvestigationPlan,
)
from matcheyes.agents.reasoning import (
    AgentTask,
    ModelUnavailableError,
    ReasoningModel,
    Step,
)
from matcheyes.domain.base import DomainModel

MAX_OUTPUT_CHARS = 100_000
DEFAULT_ATTEMPTS = 2

FailureKind = Literal["model_unavailable", "malformed_output"]


@dataclass(frozen=True)
class Attempt:
    number: int
    ok: bool
    error: str | None
    latency_ms: float


class RoleFailureError(Exception):
    def __init__(self, step: Step, kind: FailureKind, attempts: tuple[Attempt, ...]) -> None:
        super().__init__(f"{step.value}: {kind} after {len(attempts)} attempt(s)")
        self.step = step
        self.kind = kind
        self.attempts = attempts


def _consistency(output: DomainModel) -> str | None:
    """Contract rules that a schema alone cannot express."""
    if isinstance(output, InvestigationPlan):
        if len(set(output.hypotheses)) != len(output.hypotheses):
            return "duplicate hypotheses"
        if len({r.request_id for r in output.requests}) != len(output.requests):
            return "duplicate request_id"
    if isinstance(output, Assessment):
        kinds = [h.kind for h in output.hypotheses]
        if len(set(kinds)) != len(kinds):
            return "duplicate hypotheses"
        if output.leading is not None and output.leading not in kinds:
            return "leading hypothesis was not assessed"
    if isinstance(output, Challenge) and (
        len({r.request_id for r in output.requests}) != len(output.requests)
    ):
        return "duplicate request_id"
    return None


def _describe(exc: ValidationError) -> str:
    """Field paths and messages only: never the offending input."""
    parts = [
        f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}"
        for e in exc.errors(include_input=False, include_url=False)[:5]
    ]
    return "; ".join(parts)


def run_step[M: DomainModel](
    model: ReasoningModel,
    task: AgentTask,
    contract: type[M],
    attempts: int = DEFAULT_ATTEMPTS,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[M, tuple[Attempt, ...]]:
    log: list[Attempt] = []
    failure: FailureKind = "malformed_output"
    for number in range(1, attempts + 1):
        start = clock()
        error: str | None = None
        output: M | None = None
        try:
            raw = model.respond(task)
        except ModelUnavailableError as exc:
            failure, error = "model_unavailable", f"model unavailable: {str(exc)[:200]}"
        else:
            failure = "malformed_output"
            if not isinstance(raw, str) or len(raw) > MAX_OUTPUT_CHARS:
                error = "output missing or too long"
            else:
                try:
                    output = contract.model_validate_json(raw)
                except ValidationError as exc:
                    error = f"output does not match the {contract.__name__} contract: "
                    error += _describe(exc)
                else:
                    error = _consistency(output)
        elapsed = (clock() - start) * 1000
        if error is None and output is not None:
            log.append(Attempt(number, True, None, elapsed))
            return output, tuple(log)
        log.append(Attempt(number, False, error, elapsed))
        task = task.model_copy(update={"feedback": error})
    raise RoleFailureError(task.step, failure, tuple(log))


class Investigator:
    def __init__(self, model: ReasoningModel, attempts: int = DEFAULT_ATTEMPTS) -> None:
        self.model, self.attempts = model, attempts

    def plan(self, case: CaseFile) -> tuple[InvestigationPlan, tuple[Attempt, ...]]:
        task = AgentTask(step=Step.PLAN, case=case)
        return run_step(self.model, task, InvestigationPlan, self.attempts)

    def assess(
        self,
        case: CaseFile,
        evidence: tuple[EvidenceItem, ...],
        tested: tuple[HypothesisKind, ...],
    ) -> tuple[Assessment, tuple[Attempt, ...]]:
        task = AgentTask(step=Step.ASSESS, case=case, evidence=evidence, tested=tested)
        return run_step(self.model, task, Assessment, self.attempts)


class Challenger:
    def __init__(self, model: ReasoningModel, attempts: int = DEFAULT_ATTEMPTS) -> None:
        self.model, self.attempts = model, attempts

    def challenge(
        self,
        case: CaseFile,
        evidence: tuple[EvidenceItem, ...],
        tested: tuple[HypothesisKind, ...],
        assessment: Assessment,
    ) -> tuple[Challenge, tuple[Attempt, ...]]:
        task = AgentTask(
            step=Step.CHALLENGE,
            case=case,
            evidence=evidence,
            tested=tested,
            assessment=assessment,
        )
        return run_step(self.model, task, Challenge, self.attempts)
