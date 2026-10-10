"""One reasoner per role: the Investigator's steps from one model, the Challenger's from another.

Stage 9 configuration B' is `RoleSplitModel(RuleBasedReasoner(), hosted)`: the deterministic
reference plans and assesses, the hosted model challenges (proposes alternatives and the evidence
to test them). Every answer, from either side, is still validated by `run_step` and checked by
the verifier; nothing about verification depends on which reasoner answered.
"""

from matcheyes.agents.reasoning import AgentTask, ReasoningModel, RuleBasedReasoner, Step

ROLES = ("model", "challenger")
"""Which roles a hosted model plays: both (configuration B), or only the Challenger, with the
reference reasoner as Investigator (configuration B')."""


class RoleSplitModel:
    def __init__(self, investigator: ReasoningModel, challenger: ReasoningModel) -> None:
        self.investigator = investigator
        self.challenger = challenger

    @property
    def name(self) -> str:
        return f"investigator={self.investigator.name}+challenger={self.challenger.name}"

    def respond(self, task: AgentTask) -> str:
        role = self.challenger if task.step is Step.CHALLENGE else self.investigator
        return role.respond(task)


def with_roles(roles: str, model: ReasoningModel) -> ReasoningModel:
    """`model` in the given roles; the reference reasoner takes the others."""
    if roles not in ROLES:
        raise ValueError(f"roles must be one of {ROLES}")
    return model if roles == "model" else RoleSplitModel(RuleBasedReasoner(), model)
