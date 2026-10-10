"""Configuration B': one reasoner investigates, another challenges."""

from matcheyes.agents.reasoning import AgentTask, RuleBasedReasoner, Step
from matcheyes.agents.split import RoleSplitModel
from matcheyes.orchestration.investigation import Orchestrator
from tests.agents.support import RED_CARD, strongest, workspace


class Counting(RuleBasedReasoner):
    def __init__(self, label: str) -> None:
        self.label = label
        self.steps: list[Step] = []

    @property
    def name(self) -> str:
        return self.label

    def respond(self, task: AgentTask) -> str:
        self.steps.append(task.step)
        return super().respond(task)


def test_each_role_is_answered_by_its_own_reasoner() -> None:
    investigator, challenger = Counting("inv"), Counting("chal")
    split = RoleSplitModel(investigator, challenger)
    assert split.name == "investigator=inv+challenger=chal"
    ws = workspace(RED_CARD)
    cid = strongest(ws).candidate_id
    record = Orchestrator(ws, split).investigate(cid)
    assert challenger.steps and set(challenger.steps) == {Step.CHALLENGE}
    assert investigator.steps and Step.CHALLENGE not in investigator.steps
    reference = Orchestrator(ws, RuleBasedReasoner()).investigate(cid)
    assert record.final.model_dump(exclude={"investigation_id"}) == reference.final.model_dump(
        exclude={"investigation_id"}
    )
