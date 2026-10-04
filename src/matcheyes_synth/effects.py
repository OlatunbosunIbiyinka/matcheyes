"""Effect-size checks: is each planted cause detectable in principle?

For a scenario and seed, compare a simple observable proxy in the effect window between the
real match and its *counterfactual twin*: the same scenario and seed with the planted
interventions removed. Because randomness is drawn from named streams, the twin shares the
match up to the first intervention, so the difference isolates the planted cause.

The proxies are deliberately crude, generator-side checks. They are not MatchEyes detectors and
are never used to accept or reject a match (docs/synthetic-data.md#evaluation-hygiene).
"""

from collections.abc import Callable
from dataclasses import dataclass

from matcheyes.domain.events import (
    BallRecovery,
    Interception,
    MatchEvent,
    Pass,
    PassOutcome,
    Pressure,
    Tackle,
)
from matcheyes.domain.match import ObservableMatch
from matcheyes.domain.time import MatchInstant
from matcheyes_synth.generator import generate_match
from matcheyes_synth.truth import ScenarioSpec

Proxy = Callable[[ObservableMatch, str, MatchInstant, MatchInstant], float]


def _window(match: ObservableMatch, start: MatchInstant, end: MatchInstant) -> list[MatchEvent]:
    return [e for e in match.events if start.sort_key <= e.instant.sort_key < end.sort_key]


def _minutes(start: MatchInstant, end: MatchInstant) -> float:
    return max(1.0, (end.clock_ms - start.clock_ms) / 60_000 if start.period == end.period else 1)


def pressures_per_minute(
    match: ObservableMatch, team: str, start: MatchInstant, end: MatchInstant
) -> float:
    events = _window(match, start, end)
    return sum(isinstance(e, Pressure) and e.team_id == team for e in events) / _minutes(start, end)


def high_regains_per_minute(
    match: ObservableMatch, team: str, start: MatchInstant, end: MatchInstant
) -> float:
    regains = (Interception, BallRecovery, Tackle)
    events = _window(match, start, end)
    count = sum(isinstance(e, regains) and e.team_id == team and e.location.x >= 60 for e in events)
    return count / _minutes(start, end)


def mean_defensive_x(
    match: ObservableMatch, team: str, start: MatchInstant, end: MatchInstant
) -> float:
    actions = (Pressure, Tackle, Interception, BallRecovery)
    xs = [
        e.location.x
        for e in _window(match, start, end)
        if isinstance(e, actions) and e.team_id == team
    ]
    return sum(xs) / len(xs) if xs else 0.0


def opponent_completion(
    match: ObservableMatch, team: str, start: MatchInstant, end: MatchInstant
) -> float:
    passes = [e for e in _window(match, start, end) if isinstance(e, Pass) and e.team_id != team]
    return sum(p.outcome is PassOutcome.COMPLETE for p in passes) / max(1, len(passes))


def wide_pass_share(
    match: ObservableMatch, team: str, start: MatchInstant, end: MatchInstant
) -> float:
    passes = [e for e in _window(match, start, end) if isinstance(e, Pass) and e.team_id == team]
    wide = sum(abs(p.end_location.y - 34) > 20 for p in passes)
    return wide / max(1, len(passes))


def press_success(
    match: ObservableMatch, team: str, start: MatchInstant, end: MatchInstant
) -> float:
    """Share of the team's pressures followed within the next 3 events by a regain."""
    events = _window(match, start, end)
    regains = (Interception, BallRecovery, Tackle)
    pressures = successes = 0
    for index, event in enumerate(events):
        if isinstance(event, Pressure) and event.team_id == team:
            pressures += 1
            following = events[index + 1 : index + 4]
            won = any(
                isinstance(f, regains)
                and f.team_id == team
                and (not isinstance(f, Tackle) or f.outcome == "won")
                for f in following
            )
            successes += won
    return successes / max(1, pressures)


def counterfactual(spec: ScenarioSpec) -> ScenarioSpec:
    """The same scenario with every planted intervention removed (scripted events kept)."""
    return spec.model_copy(update={"interventions": (), "expected_insights": ()})


@dataclass(frozen=True)
class EffectResult:
    scenario_id: str
    label: str
    differences: tuple[float, ...]

    @property
    def mean(self) -> float:
        return sum(self.differences) / len(self.differences)

    @property
    def standardised(self) -> float:
        """Mean paired difference divided by its standard error (a paired t statistic)."""
        n = len(self.differences)
        mean = self.mean
        variance = sum((d - mean) ** 2 for d in self.differences) / max(1, n - 1)
        return mean / ((variance / n) ** 0.5 or 1e-9)


@dataclass(frozen=True)
class EffectCheck:
    """`primary` marks the one proxy per scenario that must be detectable; secondary proxies
    are measured and reported, because some mechanisms are small relative to match noise."""

    scenario_id: str
    team_id: str
    start: MatchInstant
    end: MatchInstant
    proxy: Proxy
    expected_sign: int
    label: str
    primary: bool = True


m = MatchInstant.at_minute

EFFECT_CHECKS: tuple[EffectCheck, ...] = (
    EffectCheck(
        "S02_press_surge",
        "kestrel-bay",
        m(60),
        m(75),
        pressures_per_minute,
        1,
        "pressures/min",
        primary=False,
    ),
    EffectCheck(
        "S02_press_surge",
        "kestrel-bay",
        m(60),
        m(75),
        high_regains_per_minute,
        1,
        "high regains/min",
    ),
    EffectCheck(
        "S02_press_surge",
        "kestrel-bay",
        m(60),
        m(75),
        opponent_completion,
        -1,
        "opponent completion",
        primary=False,
    ),
    EffectCheck(
        "S03_game_state_deep_block",
        "northmoor",
        m(54),
        m(70),
        mean_defensive_x,
        -1,
        "mean defensive x",
    ),
    EffectCheck(
        "S04_fatigue_press_decay", "corran-valley", m(70), m(88), press_success, -1, "press success"
    ),
    EffectCheck(
        "S05_red_card_reorganisation",
        "saltmarsh",
        m(38),
        m(45),
        mean_defensive_x,
        -1,
        "mean defensive x",
    ),
    EffectCheck(
        "S06_impact_substitution", "thornvale", m(63), m(80), wide_pass_share, 1, "wide pass share"
    ),
    EffectCheck(
        "S09_halftime_formation_shift",
        "redmarsh",
        m(45),
        m(60),
        mean_defensive_x,
        1,
        "mean defensive x",
    ),
    EffectCheck(
        "S10_comeback_multi_cause",
        "kestrel-bay",
        m(58),
        m(80),
        high_regains_per_minute,
        1,
        "high regains/min",
    ),
)


def measure_effect(spec: ScenarioSpec, check: EffectCheck, seeds: list[int]) -> EffectResult:
    twin = counterfactual(spec)
    differences = []
    for seed in seeds:
        planted = generate_match(spec, seed).observable
        baseline = generate_match(twin, seed).observable
        args = (check.team_id, check.start, check.end)
        differences.append(check.proxy(planted, *args) - check.proxy(baseline, *args))
    return EffectResult(spec.scenario_id, check.label, tuple(differences))
