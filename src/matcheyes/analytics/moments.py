"""Build evidence objects from shifts and key events, then group them into candidate moments."""

from collections import Counter, defaultdict
from collections.abc import Sequence

from pydantic import Field

from matcheyes.analytics.changepoints import DetectionConfig, Shift
from matcheyes.analytics.evidence import (
    CandidateMoment,
    Evidence,
    EvidenceLabel,
    KeyEventEvidence,
    KeyEventType,
    MetricShiftEvidence,
    MomentKind,
    PlayerInvolvementEvidence,
    RunOfPlayEvidence,
)
from matcheyes.analytics.metrics import METRIC_BY_NAME, MetricKind, Series
from matcheyes.analytics.possessions import ON_BALL
from matcheyes.analytics.summary import DISMISSALS, ratio
from matcheyes.analytics.timeline import BIN_MS, Timeline
from matcheyes.domain.base import DomainModel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.entities import Identifier, MatchInfo
from matcheyes.domain.events import (
    Card,
    FormationChange,
    MatchEvent,
    OwnGoal,
    Shot,
    ShotOutcome,
    Substitution,
)
from matcheyes.domain.time import MatchInstant

KEY_EVENT_SCORES: dict[KeyEventType, float] = {
    KeyEventType.GOAL: 10.0,
    KeyEventType.DISMISSAL: 8.0,
    KeyEventType.FORMATION_CHANGE: 5.0,
    KeyEventType.SUBSTITUTION: 2.0,
}

AGAINST_RUN_MAX_FIELD_TILT = 1 / 3
"""A goal is against the run of play when, in the preceding window, the scorers had at most a
third of the attacking-third actions and fewer shots than the opponent."""


class AnalysisConfig(DomainModel):
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    merge_bins: int = Field(default=5, ge=0, description="Shifts this close form one moment.")
    moment_min_statistic: float = Field(
        default=3.0,
        gt=0,
        description="A shift cluster becomes a candidate moment only if one shift reaches this "
        "statistic or shifts from two or more metric families co-occur. Weaker shifts remain "
        "available as evidence.",
    )
    run_of_play_bins: int = Field(default=10, ge=1)
    involvement_bins: int = Field(default=15, ge=1)


class Names:
    def __init__(self, info: MatchInfo) -> None:
        self.teams = {s.team_id: s.club.name for s in (info.home, info.away)}
        self.players = {p.player_id: p.name for s in (info.home, info.away) for p in s.squad}

    def team(self, team_id: Identifier) -> str:
        return self.teams[team_id]

    def player(self, player_id: Identifier) -> str:
        return self.players.get(player_id, player_id)


def _bin_end(timeline: Timeline, end_bin: int) -> MatchInstant:
    last = timeline.bins[end_bin - 1]
    return MatchInstant(period=last.period, clock_ms=(last.minute + 1) * BIN_MS)


def format_value(metric: str, value: float) -> str:
    spec = METRIC_BY_NAME[metric]
    if spec.kind is MetricKind.PROPORTION:
        return f"{value:.0%}"
    if spec.kind is MetricKind.MEAN:
        return f"{value:.1f} m"
    return f"{value * 10:.1f} per 10 min"


def shift_evidence(
    shift: Shift, series: Series, timeline: Timeline, names: Names
) -> MetricShiftEvidence:
    spec = METRIC_BY_NAME[shift.metric]
    at = timeline.bins[shift.bin_index].start
    end = _bin_end(timeline, shift.after.end_bin)
    start = timeline.bins[shift.before.start_bin].start
    verb = "rose" if shift.delta > 0 else "fell"
    statement = (
        f"{names.team(shift.team_id)} {spec.title} {verb} from "
        f"{format_value(spec.name, shift.before.value)} ({start.display_minute}-"
        f"{at.display_minute}) to {format_value(spec.name, shift.after.value)} "
        f"({at.display_minute}-{end.display_minute})."
    )
    return MetricShiftEvidence(
        evidence_id=f"shift-{shift.team_id}-{shift.metric}-{shift.bin_index}",
        label=EvidenceLabel.ANALYSIS,
        strength=ClaimStrength.OBSERVED,
        team_id=shift.team_id,
        at=at,
        statement=statement,
        event_ids=series.events_between(shift.after.start_bin, shift.after.end_bin),
        metric=shift.metric,
        family=spec.family.value,
        direction="up" if shift.delta > 0 else "down",
        before_start=start,
        after_end=end,
        before_value=shift.before.value,
        after_value=shift.after.value,
        before_sample=shift.before.sample,
        after_sample=shift.after.sample,
        statistic=shift.statistic,
    )


def key_event_evidence(events: Sequence[MatchEvent], info: MatchInfo) -> list[KeyEventEvidence]:
    names = Names(info)
    found = []
    for e in events:
        minute = e.instant.display_minute
        item: tuple[KeyEventType, Identifier, Identifier | None, Identifier | None, str] | None
        item = None
        if isinstance(e, Shot) and e.outcome is ShotOutcome.GOAL:
            item = (
                KeyEventType.GOAL,
                e.team_id,
                e.player_id,
                None,
                f"Goal for {names.team(e.team_id)}, scored by {names.player(e.player_id)} "
                f"({minute}).",
            )
        elif isinstance(e, OwnGoal):
            scorers = info.opponent_of(e.team_id)
            item = (
                KeyEventType.GOAL,
                scorers,
                e.player_id,
                None,
                f"Goal for {names.team(scorers)}, an own goal by {names.player(e.player_id)} "
                f"({minute}).",
            )
        elif isinstance(e, Card) and e.card in DISMISSALS:
            item = (
                KeyEventType.DISMISSAL,
                e.team_id,
                e.player_id,
                None,
                f"{names.player(e.player_id)} ({names.team(e.team_id)}) was sent off ({minute}).",
            )
        elif isinstance(e, Substitution):
            item = (
                KeyEventType.SUBSTITUTION,
                e.team_id,
                e.replacement_id,
                e.player_id,
                f"{names.team(e.team_id)} substitution: {names.player(e.replacement_id)} on for "
                f"{names.player(e.player_id)} ({minute}).",
            )
        elif isinstance(e, FormationChange):
            item = (
                KeyEventType.FORMATION_CHANGE,
                e.team_id,
                None,
                None,
                f"{names.team(e.team_id)} changed to {e.formation} ({minute}).",
            )
        if item is None:
            continue
        kind, team, player, related, statement = item
        found.append(
            KeyEventEvidence(
                evidence_id=f"key-{e.event_id}",
                label=EvidenceLabel.FACT,
                strength=ClaimStrength.OBSERVED,
                team_id=team,
                at=e.instant,
                statement=statement,
                event_ids=(e.event_id,),
                event_type=kind,
                player_id=player,
                related_player_id=related,
                detail=e.formation if isinstance(e, FormationChange) else None,
            )
        )
    return found


def run_of_play_evidence(
    goal: KeyEventEvidence,
    series: dict[tuple[str, Identifier], Series],
    timeline: Timeline,
    info: MatchInfo,
    window: int,
) -> RunOfPlayEvidence:
    names = Names(info)
    team, opponent = goal.team_id, info.opponent_of(goal.team_id)
    end = timeline.index_of(goal.at.period, goal.at.clock_ms)
    start = max(0, end - window)

    def pooled(metric: str) -> float | None:
        s = series[metric, team]
        return ratio(sum(s.numerators[start:end]), sum(s.denominators[start:end]))

    tilt = pooled("field_tilt")
    share = pooled("on_ball_share")
    shots_for = int(sum(series["shots", team].numerators[start:end]))
    shots_against = int(sum(series["shots", opponent].numerators[start:end]))
    against = tilt is not None and tilt <= AGAINST_RUN_MAX_FIELD_TILT and shots_for < shots_against
    tilt_text = "undefined (no attacking-third actions)" if tilt is None else f"{tilt:.0%}"
    if end == start:
        statement = "The goal came in the opening minute; there is no prior play to compare."
    else:
        statement = (
            f"In the {end - start} minutes before the goal, {names.team(team)} had field tilt "
            f"{tilt_text} and {shots_for} shots to {shots_against}"
            + (": the goal came against the run of play." if against else ".")
        )
    return RunOfPlayEvidence(
        evidence_id=f"run-{goal.evidence_id}",
        label=EvidenceLabel.ANALYSIS,
        strength=ClaimStrength.OBSERVED,
        team_id=team,
        at=goal.at,
        statement=statement,
        event_ids=goal.event_ids,
        goal_evidence_id=goal.evidence_id,
        window_minutes=max(end - start, 1),
        field_tilt=tilt,
        on_ball_share=share,
        shots_for=shots_for,
        shots_against=shots_against,
        against_run_of_play=against,
    )


def involvement_evidence(
    sub: KeyEventEvidence,
    events: Sequence[MatchEvent],
    series: dict[tuple[str, Identifier], Series],
    timeline: Timeline,
    info: MatchInfo,
    window: int,
) -> PlayerInvolvementEvidence:
    names = Names(info)
    if sub.player_id is None or sub.related_player_id is None:
        raise ValueError(f"{sub.evidence_id}: substitution without both players")
    incoming, outgoing = sub.player_id, sub.related_player_id
    b = timeline.index_of(sub.at.period, sub.at.clock_ms)
    after = (b, min(len(timeline), b + window))
    before = (max(0, b - window), b)
    per_bin: dict[Identifier, Counter[int]] = defaultdict(Counter)
    for e in events:
        if isinstance(e, ON_BALL) and e.player_id in (incoming, outgoing):
            per_bin[e.player_id][timeline.index_of_event(e)] += 1
    team_actions = series["on_ball_share", sub.team_id].numerators

    def tally(player: Identifier, span: tuple[int, int]) -> tuple[int, float | None]:
        actions = sum(per_bin[player][i] for i in range(*span))
        return actions, ratio(actions, sum(team_actions[span[0] : span[1]]))

    actions, share = tally(incoming, after)
    replaced_actions, replaced_share = tally(outgoing, before)

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    statement = (
        f"{names.player(incoming)} made {actions} on-ball actions ({pct(share)} of "
        f"{names.team(sub.team_id)}'s) in the {window} minutes after coming on; "
        f"{names.player(outgoing)} made {replaced_actions} ({pct(replaced_share)}) in the "
        f"{window} minutes before going off."
    )
    return PlayerInvolvementEvidence(
        evidence_id=f"involvement-{sub.evidence_id}",
        label=EvidenceLabel.ANALYSIS,
        strength=ClaimStrength.OBSERVED,
        team_id=sub.team_id,
        at=sub.at,
        statement=statement,
        event_ids=sub.event_ids,
        player_id=incoming,
        replaced_player_id=outgoing,
        window_minutes=window,
        actions=actions,
        share=share,
        replaced_actions=replaced_actions,
        replaced_share=replaced_share,
    )


def group_shifts(
    shifts: Sequence[MetricShiftEvidence], merge_bins: int, timeline: Timeline
) -> list[list[MetricShiftEvidence]]:
    """Per team, consecutive shifts within `merge_bins` of a cluster's first shift."""
    clusters: list[list[MetricShiftEvidence]] = []
    by_team: dict[Identifier, list[MetricShiftEvidence]] = defaultdict(list)
    for s in shifts:
        by_team[s.team_id].append(s)

    def index(e: MetricShiftEvidence) -> int:
        return timeline.index_of(e.at.period, e.at.clock_ms)

    for team in sorted(by_team):
        current: list[MetricShiftEvidence] = []
        for s in sorted(by_team[team], key=index):
            if current and index(s) - index(current[0]) > merge_bins:
                clusters.append(current)
                current = []
            current.append(s)
        if current:
            clusters.append(current)
    return clusters


def build_moments(
    evidence: Sequence[Evidence],
    timeline: Timeline,
    info: MatchInfo,
    merge_bins: int,
    min_statistic: float = 0.0,
) -> tuple[CandidateMoment, ...]:
    names = Names(info)
    shifts = [e for e in evidence if isinstance(e, MetricShiftEvidence)]
    keys = [e for e in evidence if isinstance(e, KeyEventEvidence)]
    runs = {e.goal_evidence_id: e for e in evidence if isinstance(e, RunOfPlayEvidence)}
    involvement = {
        e.evidence_id.removeprefix("involvement-"): e
        for e in evidence
        if isinstance(e, PlayerInvolvementEvidence)
    }

    def index(at: MatchInstant) -> int:
        return timeline.index_of(at.period, at.clock_ms)

    drafts: list[tuple[MatchInstant, int, dict[str, object]]] = []
    for cluster in group_shifts(shifts, merge_bins, timeline):
        first, last = index(cluster[0].at), index(cluster[-1].at)
        families = tuple(sorted({s.family for s in cluster}))
        strongest = max(abs(s.statistic) for s in cluster)
        if len(families) < 2 and strongest < min_statistic:
            continue
        context = tuple(
            k.evidence_id for k in keys if first - merge_bins <= index(k.at) <= last + merge_bins
        )
        changes = "; ".join(f"{METRIC_BY_NAME[s.metric].title} {s.direction}" for s in cluster)
        drafts.append(
            (
                cluster[0].at,
                1,
                {
                    "kind": MomentKind.METRIC_SHIFTS,
                    "team_id": cluster[0].team_id,
                    "strength": ClaimStrength.ASSOCIATED
                    if len(families) > 1
                    else ClaimStrength.OBSERVED,
                    "headline": f"{names.team(cluster[0].team_id)} from "
                    f"{cluster[0].at.display_minute}: {changes}.",
                    "evidence_ids": tuple(s.evidence_id for s in cluster),
                    "families": families,
                    "score": round(sum(abs(s.statistic) for s in cluster), 3),
                    "context_evidence_ids": context,
                },
            )
        )
    for key in keys:
        b = index(key.at)
        linked: list[str] = [key.evidence_id]
        if key.evidence_id in runs:
            linked.append(runs[key.evidence_id].evidence_id)
        if key.evidence_id in involvement:
            linked.append(involvement[key.evidence_id].evidence_id)
        headline = key.statement
        if key.evidence_id in runs and runs[key.evidence_id].against_run_of_play:
            headline = headline.rstrip(".") + " - against the run of play."
        context = tuple(s.evidence_id for s in shifts if abs(index(s.at) - b) <= merge_bins)
        drafts.append(
            (
                key.at,
                0,
                {
                    "kind": MomentKind.KEY_EVENT,
                    "team_id": key.team_id,
                    "strength": ClaimStrength.OBSERVED,
                    "headline": headline,
                    "evidence_ids": tuple(linked),
                    "families": (key.event_type.value,),
                    "score": KEY_EVENT_SCORES[key.event_type],
                    "context_evidence_ids": context,
                },
            )
        )
    drafts.sort(key=lambda d: (d[0].sort_key, d[1]))
    return tuple(
        CandidateMoment.model_validate({"moment_id": f"m{i:02d}", "at": at, **fields})
        for i, (at, _, fields) in enumerate(drafts, start=1)
    )
