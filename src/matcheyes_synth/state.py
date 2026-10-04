"""Hidden team state at any instant (docs/synthetic-data.md#hidden-state-model).

    state = clamp(baseline style + planted interventions x ramp + game state + fatigue)

Game state and fatigue are background dynamics present in every match. Every contribution is
reported as a driver so the answer key can say *why* the state differs from baseline.
"""

from dataclasses import dataclass

from matcheyes.domain.time import MatchInstant
from matcheyes_synth.truth import HiddenTeamState, Intervention, StateDriver

DIMENSIONS: tuple[str, ...] = tuple(HiddenTeamState.model_fields)

FATIGUE_THRESHOLD = 0.25
DRIVER_EPSILON = 0.005


@dataclass(frozen=True)
class StateContext:
    goal_difference: int
    match_minute: float
    average_fatigue: float


@dataclass(frozen=True)
class ResolvedState:
    state: HiddenTeamState
    drivers: tuple[StateDriver, ...]
    intervention_ids: tuple[str, ...]


def match_minute(instant: MatchInstant) -> float:
    return instant.clock_ms / 60_000 + (0.0 if instant.period == 1 else 45.0)


def is_active(intervention: Intervention, instant: MatchInstant) -> bool:
    if instant.sort_key < intervention.start.sort_key:
        return False
    return intervention.end is None or instant.sort_key < intervention.end.sort_key


def ramp_fraction(intervention: Intervention, instant: MatchInstant) -> float:
    if not is_active(intervention, instant):
        return 0.0
    if intervention.ramp_s == 0:
        return 1.0
    elapsed_s = (match_minute(instant) - match_minute(intervention.start)) * 60
    return min(1.0, max(0.0, elapsed_s / intervention.ramp_s))


def game_state_adjustment(context: StateContext) -> dict[str, float]:
    """Trailing teams take more risk and push up; leading teams protect, more so late on."""
    diff = context.goal_difference
    if diff == 0:
        return {}
    urgency = min(1.0, max(0.0, (context.match_minute - 30) / 60))
    weight = urgency * (0.6 if abs(diff) == 1 else 1.0)
    if weight == 0:
        return {}
    if diff < 0:
        return {
            "risk_appetite": 0.20 * weight,
            "defensive_line": 0.12 * weight,
            "press_intensity": 0.12 * weight,
            "directness": 0.08 * weight,
            "tempo": 0.08 * weight,
        }
    return {
        "risk_appetite": -0.15 * weight,
        "defensive_line": -0.10 * weight,
        "press_intensity": -0.08 * weight,
        "tempo": -0.06 * weight,
    }


def fatigue_adjustment(context: StateContext) -> dict[str, float]:
    excess = max(0.0, context.average_fatigue - FATIGUE_THRESHOLD)
    if excess == 0:
        return {}
    return {
        "press_intensity": -0.25 * excess,
        "execution": -0.30 * excess,
        "tempo": -0.10 * excess,
    }


def resolve_state(
    baseline: HiddenTeamState,
    interventions: list[Intervention],
    instant: MatchInstant,
    context: StateContext,
) -> ResolvedState:
    values = baseline.model_dump()
    drivers: list[StateDriver] = []
    active: list[str] = []

    for intervention in interventions:
        if not is_active(intervention, instant):
            continue
        fraction = ramp_fraction(intervention, instant)
        active.append(intervention.intervention_id)
        for name, delta in intervention.delta.model_dump().items():
            if delta is not None:
                values[name] += delta * fraction
    if active:
        drivers.append(StateDriver.INTERVENTION)

    for driver, adjustment in (
        (StateDriver.GAME_STATE, game_state_adjustment(context)),
        (StateDriver.FATIGUE, fatigue_adjustment(context)),
    ):
        if any(abs(v) > DRIVER_EPSILON for v in adjustment.values()):
            drivers.append(driver)
        for name, delta in adjustment.items():
            values[name] += delta

    clamped = {name: round(min(1.0, max(0.0, values[name])), 4) for name in DIMENSIONS}
    return ResolvedState(
        state=HiddenTeamState(**clamped),
        drivers=tuple(drivers) or (StateDriver.BASELINE,),
        intervention_ids=tuple(active),
    )
