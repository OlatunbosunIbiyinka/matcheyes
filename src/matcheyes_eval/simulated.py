"""SIMULATED reasoning profiles for exercising the LLM evaluation harness without a live model.

These are NOT language models and their results are NOT evidence about any LLM. Each wraps the
deterministic reference reasoner and perturbs its output with seeded, known failure behaviour,
so the harness can be shown to measure what it claims to measure (an overclaiming profile must
score worse on calibration than a faithful one; an unreliable one must show failures and
retries). Every result produced with them is labelled SIMULATED.
"""

import random
from dataclasses import dataclass

from matcheyes.agents.contracts import (
    Assessment,
    Challenge,
    InvestigationPlan,
    ProposedHypothesis,
)
from matcheyes.agents.reasoning import AgentTask, ModelUnavailableError, RuleBasedReasoner, Step
from matcheyes.domain.claims import ClaimStrength
from matcheyes_eval.stage4 import FAULTS


@dataclass(frozen=True)
class Profile:
    name: str
    overclaim: float = 0.0
    omission: float = 0.0
    malformed: float = 0.0
    unavailable: float = 0.0
    causal_wording: float = 0.0


PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile("faithful"),
        Profile("overclaiming", overclaim=0.35, causal_wording=0.5),
        Profile("omissive", omission=0.35),
        Profile("unreliable", malformed=0.15, unavailable=0.05),
    )
}

NEUTRAL_WORDING = (
    "The evidence is consistent with this explanation.",
    "Observed evidence bears on this explanation as stated in the assertions.",
    "Assessment based on the cited tool results.",
)
CAUSAL_WORDING = (
    "The substitution caused the change.",
    "This clearly shows the team changed because of the goal.",
    "The manager decided to press higher, which led to the shift.",
)
OVERCLAIMS = (
    "unsupported_promotion",
    "inflated_strength",
    "hidden_contradiction",
    "forged_eliminations",
    "dropped_alternatives",
)


class SimulatedModel:
    """SIMULATED: the reference reasoner plus seeded perturbations. Not a language model."""

    def __init__(self, profile: Profile, seed: str) -> None:
        self.profile = profile
        self.base = RuleBasedReasoner()
        self.rng = random.Random(seed)  # noqa: S311 - reproducible simulation, not security
        self.name = f"SIMULATED-{profile.name}"

    def respond(self, task: AgentTask) -> str:
        p, rng = self.profile, self.rng
        if rng.random() < p.unavailable:
            raise ModelUnavailableError("simulated outage")
        if rng.random() < p.malformed:
            return '{"hypotheses": [ "truncated'
        raw = self.base.respond(task)
        if task.step is Step.PLAN:
            return self._plan(InvestigationPlan.model_validate_json(raw)).model_dump_json()
        if task.step is Step.CHALLENGE:
            if rng.random() < p.omission:
                return Challenge().model_dump_json()
            return raw
        return self._assess(Assessment.model_validate_json(raw), task).model_dump_json()

    def _plan(self, plan: InvestigationPlan) -> InvestigationPlan:
        if self.rng.random() >= self.profile.omission or len(plan.requests) < 2:
            return plan
        keep = plan.requests[: max(1, len(plan.requests) // 2)]
        return plan.model_copy(update={"requests": keep})

    def _assess(self, a: Assessment, task: AgentTask) -> Assessment:
        p, rng = self.profile, self.rng
        if rng.random() < p.overclaim:
            mutated = FAULTS[rng.choice(OVERCLAIMS)](a, task)
            if mutated is not None:
                a = mutated
        if rng.random() < p.omission and a.leading is not None and len(a.hypotheses) > 1:
            kept = tuple(h for h in a.hypotheses if h.kind is a.leading)
            a = a.model_copy(update={"hypotheses": kept})
        wording = CAUSAL_WORDING if rng.random() < p.causal_wording else NEUTRAL_WORDING
        hypotheses = tuple(self._reword(h, wording) for h in a.hypotheses)
        summary = rng.choice(wording)
        strength = a.proposed_strength
        if wording is CAUSAL_WORDING and a.leading is not None:
            strength = max(strength, ClaimStrength.HYPOTHESISED, key=lambda s: s.rank)
        return a.model_copy(
            update={"hypotheses": hypotheses, "summary": summary, "proposed_strength": strength}
        )

    def _reword(self, h: ProposedHypothesis, wording: tuple[str, ...]) -> ProposedHypothesis:
        return h.model_copy(update={"statement": self.rng.choice(wording)})
