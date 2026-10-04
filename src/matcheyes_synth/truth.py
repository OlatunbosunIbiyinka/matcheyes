"""Hidden ground truth: the causes behind a synthetic match and the explanations they imply.

Only the evaluation system may read these objects. They describe *why* the observable events
look the way they do, so that MatchEyes's inferred explanations can be scored objectively.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.time import MatchInstant

Unit = float
Delta = float


class TruthModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HiddenTeamState(TruthModel):
    """Latent team behaviour that drives event generation. Never observable directly."""

    press_intensity: Unit = Field(ge=0.0, le=1.0)
    defensive_line: Unit = Field(ge=0.0, le=1.0, description="0 = deep block, 1 = very high")
    tempo: Unit = Field(ge=0.0, le=1.0)
    directness: Unit = Field(ge=0.0, le=1.0)
    width: Unit = Field(ge=0.0, le=1.0)
    risk_appetite: Unit = Field(ge=0.0, le=1.0)
    execution: Unit = Field(ge=0.0, le=1.0, description="Technical execution quality")


class StateDelta(TruthModel):
    press_intensity: Delta | None = Field(default=None, ge=-1.0, le=1.0)
    defensive_line: Delta | None = Field(default=None, ge=-1.0, le=1.0)
    tempo: Delta | None = Field(default=None, ge=-1.0, le=1.0)
    directness: Delta | None = Field(default=None, ge=-1.0, le=1.0)
    width: Delta | None = Field(default=None, ge=-1.0, le=1.0)
    risk_appetite: Delta | None = Field(default=None, ge=-1.0, le=1.0)
    execution: Delta | None = Field(default=None, ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("a state delta must change at least one dimension")
        return self


class PlayerAttributes(TruthModel):
    player_id: str
    passing: Unit = Field(ge=0.0, le=1.0)
    finishing: Unit = Field(ge=0.0, le=1.0)
    pace: Unit = Field(ge=0.0, le=1.0)
    stamina: Unit = Field(ge=0.0, le=1.0)
    pressing: Unit = Field(ge=0.0, le=1.0)
    composure: Unit = Field(ge=0.0, le=1.0)


class ScriptedEventKind(StrEnum):
    """Observable events the generator is forced to produce. That they were *forced* is hidden."""

    GOAL = "goal"
    RED_CARD = "red_card"
    SUBSTITUTION = "substitution"
    FORMATION_CHANGE = "formation_change"
    TURNOVER_CLUSTER = "turnover_cluster"


class ScriptedEvent(TruthModel):
    ref: str = Field(min_length=1)
    kind: ScriptedEventKind
    team_id: str
    at: MatchInstant
    until: MatchInstant | None = None
    formation: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.kind is ScriptedEventKind.TURNOVER_CLUSTER:
            if self.until is None or self.until.sort_key <= self.at.sort_key:
                raise ValueError(f"{self.ref}: a turnover cluster needs a later `until`")
        elif self.until is not None:
            raise ValueError(f"{self.ref}: only turnover clusters span a window")
        if (self.kind is ScriptedEventKind.FORMATION_CHANGE) != (self.formation is not None):
            raise ValueError(f"{self.ref}: `formation` is required only for formation changes")
        return self


class InterventionKind(StrEnum):
    PRESS_SURGE = "press_surge"
    DEEP_BLOCK = "deep_block"
    FATIGUE_DECAY = "fatigue_decay"
    RED_CARD_REORGANISATION = "red_card_reorganisation"
    IMPACT_SUBSTITUTION = "impact_substitution"
    FORMATION_SHIFT = "formation_shift"


class InterventionTrigger(StrEnum):
    MANAGER_INSTRUCTION = "manager_instruction"
    GAME_STATE = "game_state"
    FATIGUE = "fatigue"
    OBSERVABLE_EVENT = "observable_event"


_TRIGGERS_WITH_EVENT = {InterventionTrigger.GAME_STATE, InterventionTrigger.OBSERVABLE_EVENT}


class Intervention(TruthModel):
    """A planted cause: a change to a team's hidden state from `start`, ramping in over
    `ramp_s` seconds, lasting until `end` (or full time)."""

    intervention_id: str = Field(min_length=1)
    team_id: str
    kind: InterventionKind
    trigger: InterventionTrigger
    trigger_ref: str | None = None
    start: MatchInstant
    ramp_s: int = Field(ge=0, le=900)
    end: MatchInstant | None = None
    delta: StateDelta

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if (self.trigger in _TRIGGERS_WITH_EVENT) != (self.trigger_ref is not None):
            raise ValueError(f"{self.intervention_id}: trigger_ref iff triggered by an event")
        if self.end is not None and self.end.sort_key <= self.start.sort_key:
            raise ValueError(f"{self.intervention_id}: end must be after start")
        return self


class MechanismSignal(StrEnum):
    """Observable signatures a correct explanation should cite. Each maps to a Stage 2 metric."""

    HIGH_RECOVERIES_UP = "high_recoveries_up"
    OPPONENT_PASS_COMPLETION_DOWN = "opponent_pass_completion_down"
    TERRITORY_SHIFT = "territory_shift"
    POSSESSION_SHARE_SHIFT = "possession_share_shift"
    DEFENSIVE_LINE_DEEPER = "defensive_line_deeper"
    PRESS_SUCCESS_DOWN = "press_success_down"
    OPPONENT_PROGRESSIONS_UP = "opponent_progressions_up"
    WIDE_PROGRESSIONS_UP = "wide_progressions_up"
    PLAYER_INVOLVEMENT_UP = "player_involvement_up"
    SHOT_VOLUME_UP = "shot_volume_up"


class InsightKind(StrEnum):
    MOMENTUM_SHIFT = "momentum_shift"
    CONTROL_LOSS = "control_loss"
    TACTICAL_REORGANISATION = "tactical_reorganisation"
    PLAYER_IMPACT = "player_impact"
    AGAINST_THE_RUN_OF_PLAY = "against_the_run_of_play"


class ExpectedInsight(TruthModel):
    """What a correct engine should conclude, and the strongest claim the truth justifies."""

    insight_id: str = Field(min_length=1)
    kind: InsightKind
    team_id: str = Field(description="The team whose behaviour or fortunes changed")
    window_start: MatchInstant
    window_end: MatchInstant
    primary_cause: str | None = None
    secondary_causes: tuple[str, ...] = ()
    mechanisms: tuple[MechanismSignal, ...] = ()
    supporting_mechanisms: tuple[MechanismSignal, ...] = Field(
        default=(),
        description=(
            "Plausible side effects that are not reliably detectable from observable data. "
            "Never scored: an engine is not penalised for missing them."
        ),
    )
    expected_strength: ClaimStrength
    max_detection_latency_s: int = Field(ge=0, le=1800)

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.window_end.sort_key <= self.window_start.sort_key:
            raise ValueError(f"{self.insight_id}: window_end must be after window_start")
        causal = self.expected_strength.rank >= ClaimStrength.HYPOTHESISED.rank
        if causal and (self.primary_cause is None or not self.mechanisms):
            raise ValueError(f"{self.insight_id}: causal insights need a cause and mechanisms")
        if not causal and (self.primary_cause is not None or self.secondary_causes):
            raise ValueError(f"{self.insight_id}: descriptive insights cannot name causes")
        if set(self.mechanisms) & set(self.supporting_mechanisms):
            raise ValueError(f"{self.insight_id}: a mechanism is either scored or supporting")
        return self


class DecoyKind(StrEnum):
    COINCIDENT_EVENT = "coincident_event"
    AGAINST_RUN_GOAL = "against_run_goal"
    NOISE_CLUSTER = "noise_cluster"


class Decoy(TruthModel):
    """A window where something salient happens *without* a planted cause. Claims about this
    team in this window must not exceed `max_claim_strength`; stronger claims are false
    positives."""

    decoy_id: str = Field(min_length=1)
    kind: DecoyKind
    team_id: str
    window_start: MatchInstant
    window_end: MatchInstant
    max_claim_strength: ClaimStrength
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.window_end.sort_key <= self.window_start.sort_key:
            raise ValueError(f"{self.decoy_id}: window_end must be after window_start")
        if self.max_claim_strength.rank > ClaimStrength.ASSOCIATED.rank:
            raise ValueError(f"{self.decoy_id}: decoys cap claims at ASSOCIATED or below")
        return self


def _overlaps(a: tuple[MatchInstant, MatchInstant], b: tuple[MatchInstant, MatchInstant]) -> bool:
    return a[0].sort_key < b[1].sort_key and b[0].sort_key < a[1].sort_key


class ScenarioSpec(TruthModel):
    """A reproducible, labelled match recipe. The spec itself is hidden ground truth."""

    scenario_id: str = Field(pattern=r"^S\d{2}_[a-z0-9_]+$")
    title: str
    purpose: str
    home_club_id: str
    away_club_id: str
    default_seed: int = Field(ge=0)
    scripted_events: tuple[ScriptedEvent, ...] = ()
    interventions: tuple[Intervention, ...] = ()
    expected_insights: tuple[ExpectedInsight, ...] = ()
    decoys: tuple[Decoy, ...] = ()

    @property
    def is_control(self) -> bool:
        return not self.interventions and not self.scripted_events

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        teams = {self.home_club_id, self.away_club_id}
        if len(teams) != 2:
            raise ValueError(f"{self.scenario_id}: home and away must differ")

        ids = (
            [e.ref for e in self.scripted_events]
            + [i.intervention_id for i in self.interventions]
            + [x.insight_id for x in self.expected_insights]
            + [d.decoy_id for d in self.decoys]
        )
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.scenario_id}: identifiers must be unique")

        referenced_teams = (
            {e.team_id for e in self.scripted_events}
            | {i.team_id for i in self.interventions}
            | {x.team_id for x in self.expected_insights}
            | {d.team_id for d in self.decoys}
        )
        if not referenced_teams <= teams:
            raise ValueError(f"{self.scenario_id}: unknown team {referenced_teams - teams}")

        scripted = {e.ref: e for e in self.scripted_events}
        interventions = {i.intervention_id: i for i in self.interventions}

        for intervention in self.interventions:
            if intervention.trigger_ref is None:
                continue
            trigger = scripted.get(intervention.trigger_ref)
            if trigger is None:
                raise ValueError(f"{intervention.intervention_id}: unknown trigger_ref")
            if intervention.start.sort_key < trigger.at.sort_key:
                raise ValueError(f"{intervention.intervention_id}: starts before its trigger")

        for insight in self.expected_insights:
            causes = [c for c in (insight.primary_cause, *insight.secondary_causes) if c]
            for cause in causes:
                if cause not in interventions:
                    raise ValueError(f"{insight.insight_id}: unknown cause {cause}")
            if insight.primary_cause is not None:
                cause_start = interventions[insight.primary_cause].start
                if insight.window_start.sort_key < cause_start.sort_key:
                    raise ValueError(f"{insight.insight_id}: effect window precedes its cause")

        for decoy in self.decoys:
            decoy_window = (decoy.window_start, decoy.window_end)
            for insight in self.expected_insights:
                causal = insight.expected_strength.rank >= ClaimStrength.HYPOTHESISED.rank
                same_team = insight.team_id == decoy.team_id
                window = (insight.window_start, insight.window_end)
                if causal and same_team and _overlaps(decoy_window, window):
                    raise ValueError(f"{decoy.decoy_id}: overlaps {insight.insight_id}")
        return self


class StateDriver(StrEnum):
    """Why a team's hidden state differs from its baseline style during a segment.

    GAME_STATE and FATIGUE are *background* dynamics present in every match, including
    controls. An engine that correctly attributes a change to them is not wrong.
    """

    BASELINE = "baseline"
    INTERVENTION = "intervention"
    GAME_STATE = "game_state"
    FATIGUE = "fatigue"


class TeamStateSegment(TruthModel):
    team_id: str
    start: MatchInstant
    end: MatchInstant
    state: HiddenTeamState
    drivers: tuple[StateDriver, ...] = Field(min_length=1)
    intervention_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_drivers(self) -> Self:
        planted = StateDriver.INTERVENTION in self.drivers
        if planted != bool(self.intervention_ids):
            raise ValueError("intervention_ids are required iff INTERVENTION is a driver")
        return self


class ResolvedScriptedEvent(TruthModel):
    """Links a scripted event to the observable event the generator emitted for it."""

    ref: str
    event_ids: tuple[str, ...] = Field(min_length=1)


class InterventionOnset(TruthModel):
    """When a planted cause actually began. For event-triggered interventions this is the later
    of the scheduled start and the emitted trigger event, so a cause never precedes its trigger."""

    intervention_id: str
    start: MatchInstant


class GroundTruth(TruthModel):
    """The answer key for one generated match. Written only under a `truth/` directory."""

    generator_version: str
    scenario: ScenarioSpec
    seed: int = Field(ge=0)
    match_id: str
    player_attributes: tuple[PlayerAttributes, ...]
    state_timeline: tuple[TeamStateSegment, ...]
    resolved_events: tuple[ResolvedScriptedEvent, ...] = ()
    intervention_onsets: tuple[InterventionOnset, ...] = ()
