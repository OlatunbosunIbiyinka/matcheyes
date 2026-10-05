"""Stage 3 entry point: re-assess every Stage 2 metric shift in context, grade it, rank it.

Stage 3 does not detect anything new. It takes the frozen Stage 2 shifts and asks, for each:
is it still unusual within the same game state (baselines), do related signals move with it
(patterns), does it hold (persistence)? The answers set an evidence level, a claim strength
capped at ASSOCIATED, and a rank. Match context and workload proxies are attached as context;
they never count as evidence.
"""

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import Field, model_validator

from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.baselines import (
    BaselineAssessment,
    ShiftSpan,
    assess_baseline,
)
from matcheyes.analytics.context import GameState, MatchContext, Phase, build_context
from matcheyes.analytics.evidence import (
    ANALYTICS_CLAIM_CEILING,
    EvidenceLabel,
    MetricShiftEvidence,
)
from matcheyes.analytics.metrics import METRIC_BY_NAME, Series
from matcheyes.analytics.moments import Names
from matcheyes.analytics.patterns import PatternAssessment, assess_patterns
from matcheyes.analytics.persistence import PersistenceAssessment, assess_persistence
from matcheyes.analytics.strength import EvidenceLevel, claim_strength, grade, rank_key
from matcheyes.analytics.timeline import Timeline
from matcheyes.analytics.workload import RECENT_WINDOW_BINS, WorkloadSnapshot, build_workload
from matcheyes.domain.base import DomainModel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.entities import Identifier
from matcheyes.domain.match import ObservableMatch
from matcheyes.domain.time import MatchInstant, Period

CONTEXTUAL_VERSION = "0.2.0"


class ContextualConfig(DomainModel):
    """Values are reasoned, not tuned (docs/contextual-evidence.md); none were searched."""

    coincidence_bins: int = Field(
        default=5,
        ge=1,
        description="A goal or dismissal this close to a shift cannot be separated from it; "
        "also the shortest same-regime window. Equal to the Stage 2 merge window.",
    )
    support_z: float = Field(default=1.5, gt=0)
    contradiction_z: float = Field(default=1.0, gt=0)
    sub_window_bins: int = Field(default=5, ge=2, description="A third of the after-window.")
    hold_z: float = Field(default=1.0, gt=0)
    moment_level: EvidenceLevel = EvidenceLevel.MODERATE
    moment_merge_bins: int = Field(default=5, ge=0)
    workload_window_bins: int = Field(default=RECENT_WINDOW_BINS, ge=1)


class ContextNote(DomainModel):
    """Observable match state at the shift, for the shift's team. Context, never evidence."""

    label: Literal[EvidenceLabel.FACT] = EvidenceLabel.FACT
    period: Period
    phase: Phase
    game_state: GameState
    goal_difference: int
    players: int
    opponent_players: int
    substitutions_made: int
    minutes_since_goal: int | None
    workload: WorkloadSnapshot
    nearby_key_event_ids: tuple[Identifier, ...] = Field(
        description="Key events near the shift: temporal context only, never a cause."
    )


class ContextualCandidate(DomainModel):
    candidate_id: Identifier
    shift_evidence_id: Identifier
    label: Literal[EvidenceLabel.ANALYSIS] = EvidenceLabel.ANALYSIS
    strength: ClaimStrength
    team_id: Identifier
    at: MatchInstant
    bin_index: int = Field(ge=0)
    statement: str = Field(min_length=1)
    metric: str
    family: str
    direction: Literal["up", "down"]
    stage2_statistic: float
    baseline: BaselineAssessment
    pattern: PatternAssessment | None
    persistence: PersistenceAssessment
    level: EvidenceLevel
    level_basis: tuple[str, ...] = Field(min_length=1)
    rank: int = Field(ge=1)
    rank_basis: str
    context: ContextNote
    event_ids: tuple[Identifier, ...] = Field(
        min_length=1,
        description="Core metric events in the Stage 2 and contextual windows, plus the "
        "after-window events of supporting signals.",
    )

    @model_validator(mode="after")
    def _within_ceiling(self) -> Self:
        if self.strength.rank > ANALYTICS_CLAIM_CEILING.rank:
            raise ValueError(f"{self.candidate_id}: deterministic analytics cannot claim causes")
        return self


class ContextualMoment(DomainModel):
    moment_id: Identifier
    team_id: Identifier
    at: MatchInstant
    label: Literal[EvidenceLabel.ANALYSIS] = EvidenceLabel.ANALYSIS
    level: EvidenceLevel
    strength: ClaimStrength
    headline: str = Field(min_length=1)
    candidate_ids: tuple[Identifier, ...] = Field(
        min_length=1, description="Highest-ranked first; the rest are within the merge window."
    )
    context_event_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _within_ceiling(self) -> Self:
        if self.strength.rank > ANALYTICS_CLAIM_CEILING.rank:
            raise ValueError(f"{self.moment_id}: deterministic analytics cannot claim causes")
        return self


class ContextualAnalysis(DomainModel):
    analytics_version: str = CONTEXTUAL_VERSION
    stage2_version: str
    match_id: Identifier
    config: ContextualConfig
    context: MatchContext
    candidates: tuple[ContextualCandidate, ...] = Field(description="Best-ranked first.")
    moments: tuple[ContextualMoment, ...]

    def candidate_by_id(self) -> dict[Identifier, ContextualCandidate]:
        return {c.candidate_id: c for c in self.candidates}


def shift_span(evidence: MetricShiftEvidence, timeline: Timeline) -> ShiftSpan:
    end = evidence.after_end
    return ShiftSpan(
        team_id=evidence.team_id,
        metric=evidence.metric,
        direction=evidence.direction,
        before=(
            timeline.index_of(evidence.before_start.period, evidence.before_start.clock_ms),
            timeline.index_of(evidence.at.period, evidence.at.clock_ms),
        ),
        after=(
            timeline.index_of(evidence.at.period, evidence.at.clock_ms),
            timeline.index_of(end.period, end.clock_ms - 1) + 1,
        ),
        statistic=evidence.statistic,
    )


def _event_ids(
    span: ShiftSpan,
    baseline: BaselineAssessment,
    pattern: PatternAssessment | None,
    series: dict[tuple[str, Identifier], Series],
) -> tuple[Identifier, ...]:
    core = series[span.metric, span.team_id]
    windows = {span.before, span.after, baseline.before, baseline.after}
    ids = [i for window in sorted(windows) for i in core.events_between(*window)]
    if pattern is not None:
        for reading in pattern.supports:
            ids.extend(
                series[reading.signal.metric, reading.team_id].events_between(*baseline.after)
            )
    return tuple(dict.fromkeys(ids))


def _context_note(
    span: ShiftSpan,
    context: MatchContext,
    workload: dict[Identifier, tuple[WorkloadSnapshot, ...]],
    near: int,
) -> ContextNote:
    t, team = span.boundary, span.team_id
    side = context.team_ids.index(team)
    state = context.bins[t]
    lo, hi = max(0, t - near), min(len(context.bins), t + near + 1)
    return ContextNote(
        period=state.period,
        phase=state.phase,
        game_state=context.game_state(team, t),
        goal_difference=context.goal_difference(team, t),
        players=state.players[side],
        opponent_players=state.players[1 - side],
        substitutions_made=state.substitutions[side],
        minutes_since_goal=state.minutes_since_goal,
        workload=workload[team][t],
        nearby_key_event_ids=tuple(i for b in context.bins[lo:hi] for i in b.key_event_ids),
    )


def _moments(
    candidates: Sequence[ContextualCandidate], config: ContextualConfig, names: Names
) -> tuple[ContextualMoment, ...]:
    groups: list[list[ContextualCandidate]] = []
    for c in candidates:
        if c.level.rank < config.moment_level.rank:
            continue
        home = next(
            (
                g
                for g in groups
                if g[0].team_id == c.team_id
                and abs(g[0].bin_index - c.bin_index) <= config.moment_merge_bins
            ),
            None,
        )
        if home is None:
            groups.append([c])
        else:
            home.append(c)
    moments = []
    for i, group in enumerate(groups, start=1):
        lead = group[0]
        changes = "; ".join(
            f"{METRIC_BY_NAME[c.metric].title} {c.direction}"
            for c in sorted(group, key=lambda c: c.bin_index)
        )
        pattern = f" ({lead.pattern.pattern.replace('_', ' ')} pattern)" if lead.pattern else ""
        moments.append(
            ContextualMoment(
                moment_id=f"cm{i:02d}",
                team_id=lead.team_id,
                at=lead.at,
                level=lead.level,
                strength=max((c.strength for c in group), key=lambda s: s.rank),
                headline=f"{names.team(lead.team_id)} from {lead.at.display_minute}: "
                f"{changes}{pattern}.",
                candidate_ids=tuple(c.candidate_id for c in group),
                context_event_ids=lead.context.nearby_key_event_ids,
            )
        )
    return tuple(moments)


def analyse_contextual(
    match: ObservableMatch,
    stage2: MatchAnalysis | None = None,
    config: ContextualConfig | None = None,
) -> ContextualAnalysis:
    config = config or ContextualConfig()
    stage2 = stage2 or analyse_match(match)
    info, events = match.info, match.events
    timeline = Timeline(events)
    context = build_context(events, timeline, info)
    workload = build_workload(events, timeline, info, config.workload_window_bins)
    series = {(s.metric, s.team_id): s for s in stage2.series}
    names = Names(info)
    z_threshold = stage2.config.detection.z_threshold

    drafts = []
    for evidence in stage2.evidence:
        if not isinstance(evidence, MetricShiftEvidence):
            continue
        span = shift_span(evidence, timeline)
        spec = METRIC_BY_NAME[span.metric]
        core = series[span.metric, span.team_id]
        baseline = assess_baseline(span, core, spec, context, z_threshold, config.coincidence_bins)
        spans = (baseline.before, baseline.after)
        pattern = assess_patterns(
            span.team_id,
            span.metric,
            span.direction,
            spans,
            series,
            info,
            config.support_z,
            config.contradiction_z,
        )
        persistence = assess_persistence(
            core,
            spec,
            span.direction,
            *spans,
            context,
            config.sub_window_bins,
            config.hold_z,
        )
        level, basis = grade(baseline, pattern, persistence)
        key = rank_key(level, pattern, persistence, baseline.statistic)
        drafts.append((key, span, evidence, baseline, pattern, persistence, level, basis))

    drafts.sort(key=lambda d: (d[0], d[1].boundary, d[1].team_id, d[1].metric))
    candidates = []
    for rank, (key, span, evidence, baseline, pattern, persistence, level, basis) in enumerate(
        drafts, start=1
    ):
        families = -key[2]
        candidates.append(
            ContextualCandidate(
                candidate_id=f"ctx-{evidence.evidence_id}",
                shift_evidence_id=evidence.evidence_id,
                strength=claim_strength(level, pattern),
                team_id=span.team_id,
                at=evidence.at,
                bin_index=span.boundary,
                statement=evidence.statement,
                metric=span.metric,
                family=evidence.family,
                direction=span.direction,
                stage2_statistic=evidence.statistic,
                baseline=baseline,
                pattern=pattern,
                persistence=persistence,
                level=level,
                level_basis=basis,
                rank=rank,
                rank_basis=f"{level.value}; {persistence.persistence.value}; {families} "
                f"independent families; {key[3]} contradictions; |z| {-key[4]:.2f}",
                context=_context_note(span, context, workload, config.coincidence_bins),
                event_ids=_event_ids(span, baseline, pattern, series),
            )
        )
    return ContextualAnalysis(
        stage2_version=stage2.analytics_version,
        match_id=info.match_id,
        config=config,
        context=context,
        candidates=tuple(candidates),
        moments=_moments(candidates, config, names),
    )
