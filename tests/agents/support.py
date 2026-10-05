"""Shared fixtures for Stage 4 tests: cached workspaces and scripted reasoning models."""

from collections.abc import Callable, Iterator
from functools import cache

from matcheyes.agents.casefile import CaseFile, build_case_file
from matcheyes.agents.contracts import Assessment, EvidenceItem
from matcheyes.agents.reasoning import AgentTask, ModelUnavailableError, RuleBasedReasoner, Step
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.contextual import ContextualCandidate
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from tests.synth.generated import generated

CONTROL = "S01_control_balanced"
RED_CARD = "S05_red_card_reorganisation"
SUBSTITUTION = "S06_impact_substitution"


@cache
def workspace(scenario_id: str) -> MatchWorkspace:
    return MatchWorkspace.build(generated(scenario_id).observable)


def strongest(ws: MatchWorkspace) -> ContextualCandidate:
    return ws.stage3.candidates[0]


def first_at_least(ws: MatchWorkspace, level: EvidenceLevel) -> ContextualCandidate:
    return next(c for c in ws.stage3.candidates if c.level.rank >= level.rank)


def case_for(ws: MatchWorkspace, candidate: ContextualCandidate) -> CaseFile:
    return build_case_file(ws, candidate.candidate_id, f"inv-test-{candidate.candidate_id}")


def reference_run(
    ws: MatchWorkspace, candidate: ContextualCandidate
) -> tuple[CaseFile, tuple[EvidenceItem, ...], Assessment, InvestigationRecord]:
    """A full reference investigation, plus the final assessment and evidence pool it verified."""
    recorder = RecordingModel(RuleBasedReasoner())
    record = Orchestrator(ws, recorder).investigate(candidate.candidate_id)
    task, raw = recorder.last(Step.ASSESS)
    return task.case, record.final.evidence, Assessment.model_validate_json(raw), record


class RecordingModel:
    def __init__(self, inner: RuleBasedReasoner) -> None:
        self.inner = inner
        self.calls: list[tuple[AgentTask, str]] = []
        self.name = "recording"

    def respond(self, task: AgentTask) -> str:
        raw = self.inner.respond(task)
        self.calls.append((task, raw))
        return raw

    def last(self, step: Step) -> tuple[AgentTask, str]:
        return next(c for c in reversed(self.calls) if c[0].step is step)


Reply = str | Exception | Callable[[AgentTask], str]


class ScriptedModel:
    """Replies per step from a script; falls back to the reference reasoner when exhausted."""

    def __init__(self, script: dict[Step, list[Reply]] | None = None) -> None:
        self.script = {k: iter(v) for k, v in (script or {}).items()}
        self.tasks: list[AgentTask] = []
        self.reference = RuleBasedReasoner()
        self.name = "scripted"

    def respond(self, task: AgentTask) -> str:
        self.tasks.append(task)
        replies: Iterator[Reply] | None = self.script.get(task.step)
        reply = next(replies, None) if replies is not None else None
        if reply is None:
            return self.reference.respond(task)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return reply(task)
        return reply


def unavailable() -> ModelUnavailableError:
    return ModelUnavailableError("connection refused")
