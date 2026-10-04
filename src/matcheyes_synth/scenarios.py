"""Scenario catalogue (docs/synthetic-data.md#scenario-catalogue).

Each scenario plants zero or more causes and states what a correct engine should conclude.
Decoys plant salient moments *without* a cause, to measure false causal claims. The catalogue
is hidden ground truth: it is never visible to MatchEyes at inference time.
"""

from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.time import MatchInstant
from matcheyes_synth.truth import (
    Decoy,
    DecoyKind,
    ExpectedInsight,
    InsightKind,
    Intervention,
    InterventionKind,
    InterventionTrigger,
    MechanismSignal,
    ScenarioSpec,
    ScriptedEvent,
    ScriptedEventKind,
    StateDelta,
)

m = MatchInstant.at_minute
Mech = MechanismSignal
SUPPORTED = ClaimStrength.SUPPORTED

S01_CONTROL = ScenarioSpec(
    scenario_id="S01_control_balanced",
    title="Control: evenly matched, nothing planted",
    purpose=(
        "False-positive baseline. A SUPPORTED causal claim is wrong unless it matches a "
        "background effect (game state, fatigue) recorded in the answer key."
    ),
    home_club_id="thornvale",
    away_club_id="brightwater",
    default_seed=101,
)

S02_PRESS_SURGE = ScenarioSpec(
    scenario_id="S02_press_surge",
    title="Away side raises the press on the hour",
    purpose="Detect a pressing-driven momentum shift and attribute it to the press.",
    home_club_id="thornvale",
    away_club_id="kestrel-bay",
    default_seed=202,
    interventions=(
        Intervention(
            intervention_id="I1",
            team_id="kestrel-bay",
            kind=InterventionKind.PRESS_SURGE,
            trigger=InterventionTrigger.MANAGER_INSTRUCTION,
            start=m(60),
            ramp_s=120,
            delta=StateDelta(press_intensity=0.15, defensive_line=0.15),
        ),
    ),
    expected_insights=(
        ExpectedInsight(
            insight_id="E1",
            kind=InsightKind.MOMENTUM_SHIFT,
            team_id="kestrel-bay",
            window_start=m(60),
            window_end=m(75),
            primary_cause="I1",
            mechanisms=(Mech.HIGH_RECOVERIES_UP, Mech.TERRITORY_SHIFT),
            # Unscored: below match noise in a 15-minute window (ADR-0007).
            supporting_mechanisms=(Mech.OPPONENT_PASS_COMPLETION_DOWN,),
            expected_strength=SUPPORTED,
            max_detection_latency_s=480,
        ),
    ),
)

S03_GAME_STATE_DEEP_BLOCK = ScenarioSpec(
    scenario_id="S03_game_state_deep_block",
    title="Home side scores, then sits deep to protect the lead",
    purpose="Attribute a territorial swing to score effects, not to the trailing side's quality.",
    home_club_id="northmoor",
    away_club_id="saltmarsh",
    default_seed=303,
    scripted_events=(
        ScriptedEvent(ref="G1", kind=ScriptedEventKind.GOAL, team_id="northmoor", at=m(52)),
    ),
    interventions=(
        Intervention(
            intervention_id="I1",
            team_id="northmoor",
            kind=InterventionKind.DEEP_BLOCK,
            trigger=InterventionTrigger.GAME_STATE,
            trigger_ref="G1",
            start=m(54),
            ramp_s=180,
            delta=StateDelta(defensive_line=-0.25, press_intensity=-0.15, risk_appetite=-0.2),
        ),
    ),
    expected_insights=(
        ExpectedInsight(
            insight_id="E1",
            kind=InsightKind.TACTICAL_REORGANISATION,
            team_id="northmoor",
            window_start=m(54),
            window_end=m(70),
            primary_cause="I1",
            mechanisms=(
                Mech.DEFENSIVE_LINE_DEEPER,
                Mech.POSSESSION_SHARE_SHIFT,
                Mech.TERRITORY_SHIFT,
            ),
            expected_strength=SUPPORTED,
            max_detection_latency_s=600,
        ),
    ),
)

S04_FATIGUE = ScenarioSpec(
    scenario_id="S04_fatigue_press_decay",
    title="High-pressing side runs out of legs",
    purpose="Separate effort from effectiveness: presses continue but stop winning the ball.",
    home_club_id="corran-valley",
    away_club_id="brightwater",
    default_seed=404,
    interventions=(
        Intervention(
            intervention_id="I1",
            team_id="corran-valley",
            kind=InterventionKind.FATIGUE_DECAY,
            trigger=InterventionTrigger.FATIGUE,
            start=m(68),
            ramp_s=600,
            delta=StateDelta(execution=-0.25, press_intensity=-0.05),
        ),
    ),
    expected_insights=(
        ExpectedInsight(
            insight_id="E1",
            kind=InsightKind.CONTROL_LOSS,
            team_id="corran-valley",
            window_start=m(70),
            window_end=m(88),
            primary_cause="I1",
            mechanisms=(Mech.PRESS_SUCCESS_DOWN, Mech.OPPONENT_PROGRESSIONS_UP),
            expected_strength=SUPPORTED,
            max_detection_latency_s=720,
        ),
    ),
)

S05_RED_CARD = ScenarioSpec(
    scenario_id="S05_red_card_reorganisation",
    title="Early red card forces a reshape",
    purpose="Attribute the shift to the dismissal and the tactical response, not to the opponent.",
    home_club_id="saltmarsh",
    away_club_id="redmarsh",
    default_seed=505,
    scripted_events=(
        ScriptedEvent(ref="R1", kind=ScriptedEventKind.RED_CARD, team_id="saltmarsh", at=m(38)),
        ScriptedEvent(
            ref="F1",
            kind=ScriptedEventKind.FORMATION_CHANGE,
            team_id="saltmarsh",
            at=m(40),
            formation="4-4-1",
        ),
    ),
    interventions=(
        Intervention(
            intervention_id="I1",
            team_id="saltmarsh",
            kind=InterventionKind.RED_CARD_REORGANISATION,
            trigger=InterventionTrigger.OBSERVABLE_EVENT,
            trigger_ref="R1",
            start=m(38),
            ramp_s=60,
            delta=StateDelta(defensive_line=-0.25, press_intensity=-0.25, tempo=-0.1),
        ),
    ),
    expected_insights=(
        ExpectedInsight(
            insight_id="E1",
            kind=InsightKind.TACTICAL_REORGANISATION,
            team_id="saltmarsh",
            window_start=m(38),
            window_end=m(55),
            primary_cause="I1",
            mechanisms=(Mech.POSSESSION_SHARE_SHIFT, Mech.DEFENSIVE_LINE_DEEPER),
            expected_strength=SUPPORTED,
            max_detection_latency_s=420,
        ),
    ),
)

S06_IMPACT_SUB = ScenarioSpec(
    scenario_id="S06_impact_substitution",
    title="A substitute winger changes the flank",
    purpose="Player-level attribution: link a change in wide progression to one player.",
    home_club_id="elderfield",
    away_club_id="thornvale",
    default_seed=606,
    scripted_events=(
        ScriptedEvent(ref="S1", kind=ScriptedEventKind.SUBSTITUTION, team_id="thornvale", at=m(63)),
    ),
    interventions=(
        Intervention(
            intervention_id="I1",
            team_id="thornvale",
            kind=InterventionKind.IMPACT_SUBSTITUTION,
            trigger=InterventionTrigger.OBSERVABLE_EVENT,
            trigger_ref="S1",
            start=m(63),
            ramp_s=0,
            delta=StateDelta(width=0.2, directness=0.1),
        ),
    ),
    expected_insights=(
        ExpectedInsight(
            insight_id="E1",
            kind=InsightKind.PLAYER_IMPACT,
            team_id="thornvale",
            window_start=m(63),
            window_end=m(80),
            primary_cause="I1",
            mechanisms=(Mech.WIDE_PROGRESSIONS_UP, Mech.PLAYER_INVOLVEMENT_UP),
            expected_strength=SUPPORTED,
            max_detection_latency_s=600,
        ),
    ),
)

S07_AGAINST_THE_RUN = ScenarioSpec(
    scenario_id="S07_against_the_run_of_play",
    title="Dominant side concedes to a rare counter",
    purpose="Describe the goal as against the run of play; claim no causal shift for the scorer.",
    home_club_id="kestrel-bay",
    away_club_id="redmarsh",
    default_seed=707,
    scripted_events=(
        ScriptedEvent(ref="G1", kind=ScriptedEventKind.GOAL, team_id="redmarsh", at=m(71)),
    ),
    expected_insights=(
        ExpectedInsight(
            insight_id="E1",
            kind=InsightKind.AGAINST_THE_RUN_OF_PLAY,
            team_id="redmarsh",
            window_start=m(71),
            window_end=m(72),
            expected_strength=ClaimStrength.OBSERVED,
            max_detection_latency_s=120,
        ),
    ),
    decoys=(
        Decoy(
            decoy_id="D1",
            kind=DecoyKind.AGAINST_RUN_GOAL,
            team_id="redmarsh",
            window_start=m(65),
            window_end=m(80),
            max_claim_strength=ClaimStrength.ASSOCIATED,
            rationale="The goal is forced with no change to Redmarsh's hidden state.",
        ),
    ),
)

S08_COINCIDENCE = ScenarioSpec(
    scenario_id="S08_coincidence_decoy",
    title="Turnover cluster coincides with a substitution",
    purpose="Temporal proximity is not causation: the substitution did not cause the turnovers.",
    home_club_id="brightwater",
    away_club_id="elderfield",
    default_seed=808,
    scripted_events=(
        ScriptedEvent(
            ref="S1", kind=ScriptedEventKind.SUBSTITUTION, team_id="brightwater", at=m(58)
        ),
        ScriptedEvent(
            ref="T1",
            kind=ScriptedEventKind.TURNOVER_CLUSTER,
            team_id="brightwater",
            at=m(58.5),
            until=m(63),
        ),
    ),
    decoys=(
        Decoy(
            decoy_id="D1",
            kind=DecoyKind.COINCIDENT_EVENT,
            team_id="brightwater",
            window_start=m(58),
            window_end=m(66),
            max_claim_strength=ClaimStrength.ASSOCIATED,
            rationale="Turnovers are forced noise; no hidden state changes at the substitution.",
        ),
    ),
)

S09_FORMATION_SHIFT = ScenarioSpec(
    scenario_id="S09_halftime_formation_shift",
    title="Half-time switch from a back five to a back four",
    purpose="Link an observable formation change to its territorial and pressing effects.",
    home_club_id="redmarsh",
    away_club_id="saltmarsh",
    default_seed=909,
    scripted_events=(
        ScriptedEvent(
            ref="F1",
            kind=ScriptedEventKind.FORMATION_CHANGE,
            team_id="redmarsh",
            at=m(45),
            formation="4-3-3",
        ),
    ),
    interventions=(
        Intervention(
            intervention_id="I1",
            team_id="redmarsh",
            kind=InterventionKind.FORMATION_SHIFT,
            trigger=InterventionTrigger.OBSERVABLE_EVENT,
            trigger_ref="F1",
            start=m(45),
            ramp_s=60,
            delta=StateDelta(defensive_line=0.2, width=0.15, press_intensity=0.15),
        ),
    ),
    expected_insights=(
        ExpectedInsight(
            insight_id="E1",
            kind=InsightKind.TACTICAL_REORGANISATION,
            team_id="redmarsh",
            window_start=m(45),
            window_end=m(60),
            primary_cause="I1",
            mechanisms=(
                Mech.TERRITORY_SHIFT,
                Mech.WIDE_PROGRESSIONS_UP,
                Mech.HIGH_RECOVERIES_UP,
            ),
            expected_strength=SUPPORTED,
            max_detection_latency_s=600,
        ),
    ),
)

S10_COMEBACK = ScenarioSpec(
    scenario_id="S10_comeback_multi_cause",
    title="Two goals down, a press and a substitute spark a fightback",
    purpose="Rank causes: the press is primary; the substitute and opponent fatigue are secondary.",
    home_club_id="northmoor",
    away_club_id="kestrel-bay",
    default_seed=1010,
    scripted_events=(
        ScriptedEvent(ref="G1", kind=ScriptedEventKind.GOAL, team_id="northmoor", at=m(20)),
        ScriptedEvent(ref="G2", kind=ScriptedEventKind.GOAL, team_id="northmoor", at=m(35)),
        ScriptedEvent(
            ref="S1", kind=ScriptedEventKind.SUBSTITUTION, team_id="kestrel-bay", at=m(62)
        ),
    ),
    interventions=(
        Intervention(
            intervention_id="I1",
            team_id="kestrel-bay",
            kind=InterventionKind.PRESS_SURGE,
            trigger=InterventionTrigger.MANAGER_INSTRUCTION,
            start=m(58),
            ramp_s=120,
            delta=StateDelta(press_intensity=0.15, defensive_line=0.15, risk_appetite=0.2),
        ),
        Intervention(
            intervention_id="I2",
            team_id="kestrel-bay",
            kind=InterventionKind.IMPACT_SUBSTITUTION,
            trigger=InterventionTrigger.OBSERVABLE_EVENT,
            trigger_ref="S1",
            start=m(62),
            ramp_s=0,
            delta=StateDelta(width=0.15),
        ),
        Intervention(
            intervention_id="I3",
            team_id="northmoor",
            kind=InterventionKind.FATIGUE_DECAY,
            trigger=InterventionTrigger.FATIGUE,
            start=m(70),
            ramp_s=600,
            delta=StateDelta(execution=-0.15),
        ),
    ),
    expected_insights=(
        ExpectedInsight(
            insight_id="E1",
            kind=InsightKind.MOMENTUM_SHIFT,
            team_id="kestrel-bay",
            window_start=m(58),
            window_end=m(80),
            primary_cause="I1",
            secondary_causes=("I2", "I3"),
            mechanisms=(
                Mech.HIGH_RECOVERIES_UP,
                Mech.SHOT_VOLUME_UP,
                Mech.TERRITORY_SHIFT,
            ),
            expected_strength=SUPPORTED,
            max_detection_latency_s=600,
        ),
    ),
)

CATALOGUE: tuple[ScenarioSpec, ...] = (
    S01_CONTROL,
    S02_PRESS_SURGE,
    S03_GAME_STATE_DEEP_BLOCK,
    S04_FATIGUE,
    S05_RED_CARD,
    S06_IMPACT_SUB,
    S07_AGAINST_THE_RUN,
    S08_COINCIDENCE,
    S09_FORMATION_SHIFT,
    S10_COMEBACK,
)


def scenario(scenario_id: str) -> ScenarioSpec:
    for spec in CATALOGUE:
        if spec.scenario_id == scenario_id:
            return spec
    raise KeyError(scenario_id)
