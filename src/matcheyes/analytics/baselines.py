"""Contextual baselines: is a Stage 2 shift still unusual once game state is held fixed?

A Stage 2 comparison window can straddle a goal or a dismissal, so a "shift" may simply be the
difference between two game states. Each shift is re-assessed in one of four ways:

* UNCHANGED - no goal or dismissal inside its comparison span: the Stage 2 comparison already
  stays within one game state, so its statistic stands.
* SAME_REGIME - a transition lies inside the span but at least `coincidence_bins` from the shift.
  Both windows are cut back to the regime of the bins next to the boundary and the comparison is
  recomputed. The shift survives only if it stays significant, material and in the same direction.
* REMOVED - as SAME_REGIME, but the shift does not survive: it came from mixing game states.
* COINCIDENT - a transition is within `coincidence_bins` of the shift, so no same-regime
  comparison of useful length exists. The shift is kept with its Stage 2 statistic, and is
  marked *context-aligned* when its direction is the ordinary response to that transition
  (table below). A context-aligned shift has a known, ordinary explanation; it is still
  observable evidence, but not evidence of anything unusual.

Alignment table (football rationale; docs/contextual-evidence.md#score-and-numbers-effects):
after a goal the conceding team tends to push (more of the ball, territory, entries, shots, a
higher and more active press) and the scoring team to protect (deeper, less pressing, fewer
attacks). After a dismissal the team with the extra player tends to push and the short-handed
team to protect. Magnitudes are not modelled; only directions.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from matcheyes.analytics.changepoints import compare_spans, is_material
from matcheyes.analytics.context import MatchContext, Transition
from matcheyes.analytics.metrics import MetricSpec, Series
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier

Direction = Literal["up", "down"]
Span = tuple[int, int]

PUSH: frozenset[tuple[str, Direction]] = frozenset(
    {
        ("on_ball_share", "up"),
        ("field_tilt", "up"),
        ("shots", "up"),
        ("attacking_third_entries", "up"),
        ("progressive_actions", "up"),
        ("defensive_action_height", "up"),
        ("pressures", "up"),
        ("high_regains", "up"),
    }
)
RETREAT: frozenset[tuple[str, Direction]] = frozenset(
    {
        ("shots", "down"),
        ("attacking_third_entries", "down"),
        ("progressive_actions", "down"),
        ("defensive_action_height", "down"),
        ("pressures", "down"),
        ("high_regains", "down"),
    }
)
"""Complementary metrics (on-ball share, field tilt) appear only as gains: Stage 2 reports a
fall for one team as a rise for the other, so the protecting side's loss is the pusher's gain."""


class BaselineKind(StrEnum):
    UNCHANGED = "unchanged"
    SAME_REGIME = "same_regime"
    REMOVED = "removed"
    COINCIDENT = "coincident"


class ShiftSpan(DomainModel):
    """The Stage 2 shift being re-assessed, as bin spans."""

    team_id: Identifier
    metric: str
    direction: Direction
    before: Span
    after: Span
    statistic: float

    @property
    def boundary(self) -> int:
        return self.after[0]


class BaselineAssessment(DomainModel):
    kind: BaselineKind
    before: Span = Field(description="Before-window actually used.")
    after: Span = Field(description="After-window actually used.")
    statistic: float | None = Field(description="Statistic on the windows used, if computable.")
    transition_ids: tuple[Identifier, ...] = Field(description="Transitions inside the span.")
    aligned_with: Identifier | None = Field(
        default=None, description="Coincident transition whose ordinary response matches."
    )

    @property
    def unusual(self) -> bool:
        return self.kind is not BaselineKind.REMOVED


def expected_response(transition: Transition, team_id: Identifier) -> frozenset[tuple[str, str]]:
    """Directions in which `team_id` ordinarily responds to the transition."""
    protecting = team_id == transition.team_id
    return RETREAT if protecting else PUSH


def is_aligned(transition: Transition, shift: ShiftSpan) -> bool:
    return (shift.metric, shift.direction) in expected_response(transition, shift.team_id)


def assess_baseline(
    shift: ShiftSpan,
    series: Series,
    spec: MetricSpec,
    context: MatchContext,
    z_threshold: float,
    coincidence_bins: int,
) -> BaselineAssessment:
    lo, t = shift.before
    hi = shift.after[1]
    inside = context.transitions_within(lo, hi)
    ids = tuple(x.event_id for x in inside)
    if not inside:
        return BaselineAssessment(
            kind=BaselineKind.UNCHANGED,
            before=shift.before,
            after=shift.after,
            statistic=shift.statistic,
            transition_ids=ids,
        )
    coincident = [x for x in inside if abs(x.bin_index - t) < coincidence_bins]
    if coincident:
        aligned = next((x.event_id for x in coincident if is_aligned(x, shift)), None)
        return BaselineAssessment(
            kind=BaselineKind.COINCIDENT,
            before=shift.before,
            after=shift.after,
            statistic=shift.statistic,
            transition_ids=ids,
            aligned_with=aligned,
        )
    before, after = context.regime_before(lo, t), context.regime_after(t, hi)
    recomputed = compare_spans(series, spec, before, after)
    sign = 1 if shift.direction == "up" else -1
    survives = (
        recomputed is not None
        and recomputed.statistic * sign >= z_threshold
        and is_material(spec, recomputed)
    )
    return BaselineAssessment(
        kind=BaselineKind.SAME_REGIME if survives else BaselineKind.REMOVED,
        before=before,
        after=after,
        statistic=None if recomputed is None else recomputed.statistic,
        transition_ids=ids,
    )
