"""The case file: the deterministic "match analyst". It states observable facts about one Stage 3
candidate and the plausible explanations to investigate. It makes no causal claim.

It is code, not an agent, because every field is a lookup or a calculation; a model would add
only the risk of misstating a fact.
"""

from typing import Any

from pydantic import Field

from matcheyes.agents.contracts import HYPOTHESIS_DESCRIPTIONS, HypothesisKind, ToolName
from matcheyes.agents.hypotheses import (
    decline_team,
    screen,
    subject_team,
    trigger_window,
)
from matcheyes.agents.tools import TOOL_SPECS, MatchWorkspace, ToolError
from matcheyes.analytics.metrics import METRIC_BY_NAME
from matcheyes.domain.base import DomainModel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.entities import Identifier

CONTEXT_BEFORE_BINS = 15
CONTEXT_AFTER_BINS = 5


class KeyEventNote(DomainModel):
    event_id: Identifier
    kind: str
    team_role: str = Field(description="'team' (the candidate's team) or 'opponent'.")
    offset: int = Field(description="Bins from the change onset; negative is before.")


class ToolDescriptor(DomainModel):
    name: ToolName
    decision: str
    arguments: dict[str, Any] = Field(description="JSON schema of the arguments.")


class CaseFile(DomainModel):
    investigation_id: Identifier
    candidate_id: Identifier
    team_id: Identifier
    opponent_id: Identifier
    subject_team_id: Identifier
    decline_team_id: Identifier | None
    onset_bin: int
    minute: str
    match_minute: int
    metric: str
    metric_title: str
    direction: str
    statement: str
    level: str
    level_rank: int
    strength: ClaimStrength
    persistence: str
    baseline_kind: str
    context_aligned: bool
    independent_families: int
    contradicting_signals: int
    game_state: str
    goal_difference: int
    before_span: tuple[int, int]
    after_span: tuple[int, int]
    trigger_window: tuple[int, int] = Field(description="Inclusive bins for preceding triggers.")
    match_bins: int
    key_events: tuple[KeyEventNote, ...]
    plausible: tuple[HypothesisKind, ...]
    screen_basis: tuple[tuple[HypothesisKind, str], ...]
    hypotheses: dict[HypothesisKind, str]
    tools: tuple[ToolDescriptor, ...]


def build_case_file(
    ws: MatchWorkspace, candidate_id: Identifier, investigation_id: Identifier
) -> CaseFile:
    c = ws.candidates.get(candidate_id)
    if c is None:
        raise ToolError("unknown candidate_id")
    t = c.bin_index
    lo, hi = t - CONTEXT_BEFORE_BINS, t + CONTEXT_AFTER_BINS
    notes = tuple(
        KeyEventNote(
            event_id=k.event_id,
            kind=k.kind,
            team_role="team" if k.team_id == c.team_id else "opponent",
            offset=k.bin_index - t,
        )
        for k in ws.key_events
        if lo <= k.bin_index <= hi
    )
    basis = screen(ws, c)
    pattern = c.pattern
    return CaseFile(
        investigation_id=investigation_id,
        candidate_id=c.candidate_id,
        team_id=c.team_id,
        opponent_id=ws.info.opponent_of(c.team_id),
        subject_team_id=subject_team(c),
        decline_team_id=decline_team(ws, c),
        onset_bin=t,
        minute=c.at.display_minute,
        match_minute=ws.match_minute(t),
        metric=c.metric,
        metric_title=METRIC_BY_NAME[c.metric].title,
        direction=c.direction,
        statement=c.statement,
        level=c.level.value,
        level_rank=c.level.rank,
        strength=c.strength,
        persistence=c.persistence.persistence.value,
        baseline_kind=c.baseline.kind.value,
        context_aligned=c.baseline.aligned_with is not None,
        independent_families=len(pattern.independent_families) if pattern else 0,
        contradicting_signals=len(pattern.contradictions) if pattern else 0,
        game_state=c.context.game_state.value,
        goal_difference=c.context.goal_difference,
        before_span=c.baseline.before,
        after_span=c.baseline.after,
        trigger_window=trigger_window(c),
        match_bins=ws.bins,
        key_events=notes,
        plausible=tuple(kind for kind, _ in basis),
        screen_basis=basis,
        hypotheses=dict(HYPOTHESIS_DESCRIPTIONS),
        tools=tuple(
            ToolDescriptor(
                name=spec.name,
                decision=spec.decision,
                arguments=spec.arguments.model_json_schema(),
            )
            for spec in TOOL_SPECS.values()
        ),
    )
