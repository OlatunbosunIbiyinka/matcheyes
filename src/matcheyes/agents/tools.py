"""Deterministic investigation tools: the only way an agent can look at a match.

Every tool is a pure function of the observable match and its Stage 2/3 analyses, takes a closed,
validated argument model, and returns an `EvidenceItem` whose `facts` are computed here, never by
a model. There is no free-form query, no code execution and nothing outside the observable match.

Each tool enables one investigation decision (docs/agentic-investigation.md#tools):

* get_candidate_assessment - how strong is the change, and does Stage 3 context already explain it?
* inspect_persistence - did the change hold, or fade/reverse (natural variation)?
* check_game_state_response - is there a goal/dismissal just before, and is this its ordinary
  response (score-state / numerical explanations)?
* get_key_events - is there a substitution or formation change just before (personnel /
  formation explanations)?
* compare_windows - did a related metric move too (supporting or contradicting signal)?
* get_substitute_involvement - were incoming players disproportionately involved (personnel)?
* get_workload - how long have this team's players been on (late-match decline)?
* find_team_changes - did the other team change first (opponent-driven)?
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, ValidationError

from matcheyes.agents.contracts import EvidenceItem, EvidenceRequest, Fact, ToolName
from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.baselines import BaselineKind, expected_response
from matcheyes.analytics.changepoints import compare_spans, is_material
from matcheyes.analytics.context import MatchContext, TransitionKind, build_context
from matcheyes.analytics.contextual import (
    ContextualAnalysis,
    ContextualCandidate,
    analyse_contextual,
)
from matcheyes.analytics.evidence import Evidence
from matcheyes.analytics.metrics import METRIC_BY_NAME, Series
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.analytics.summary import DISMISSALS
from matcheyes.analytics.timeline import Timeline
from matcheyes.analytics.workload import WorkloadSnapshot, build_workload
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier, MatchInfo
from matcheyes.domain.events import Card, FormationChange, MatchEvent, Substitution
from matcheyes.domain.match import ObservableMatch

LOOKBACK_BINS = 10
"""How far before a change an observable trigger is looked for. A response to a goal, card,
substitution or formation change is expected within minutes; ten minutes is two Stage 2 merge
windows. Reasoned, not tuned."""

ONSET_TOLERANCE_BINS = 2
"""A trigger up to two minutes after the detected onset still counts as preceding it: Stage 2
boundaries are minute bins and the scorer's early tolerance is 120 s (ADR-0007)."""

LATE_MATCH_MINUTE = 60
"""Late-match decline is only a candidate explanation from the hour mark."""

MAX_SPAN_BINS = 45

DECLINE_SIGNALS: frozenset[tuple[str, str]] = frozenset(
    {
        ("pressures", "down"),
        ("high_regains", "down"),
        ("pressure_regain_rate", "down"),
        ("defensive_action_height", "down"),
        ("progressive_actions", "down"),
        ("attacking_third_entries", "down"),
        ("shots", "down"),
        ("pass_completion", "down"),
        ("turnovers", "up"),
    }
)
"""Changes that are a fall in the team's own activity or execution."""

DECLINE_PATTERNS = frozenset({"pressing_decline", "attacking_decline", "ball_security_loss"})

KeyEventKind = Literal["goal", "dismissal", "substitution", "formation_change"]
MIN_LEVELS: dict[str, EvidenceLevel] = {level.value: level for level in EvidenceLevel}


class ToolError(Exception):
    """Invalid arguments or an unanswerable request. Recorded; never fatal to an investigation."""


@dataclass(frozen=True)
class KeyEvent:
    event_id: Identifier
    kind: KeyEventKind
    team_id: Identifier
    bin_index: int
    formation: str | None = None


@dataclass(frozen=True)
class MatchWorkspace:
    """Everything the tools may read: the observable match and its deterministic analyses."""

    info: MatchInfo
    events: dict[Identifier, MatchEvent]
    timeline: Timeline
    context: MatchContext
    workload: dict[Identifier, tuple[WorkloadSnapshot, ...]]
    series: dict[tuple[str, Identifier], Series]
    stage2_evidence: dict[Identifier, Evidence]
    stage3: ContextualAnalysis
    candidates: dict[Identifier, ContextualCandidate]
    key_events: tuple[KeyEvent, ...]
    rosters: dict[Identifier, tuple[frozenset[Identifier], ...]]
    starters: dict[Identifier, frozenset[Identifier]]

    @classmethod
    def build(
        cls,
        match: ObservableMatch,
        stage2: MatchAnalysis | None = None,
        stage3: ContextualAnalysis | None = None,
    ) -> "MatchWorkspace":
        stage2 = stage2 or analyse_match(match)
        stage3 = stage3 or analyse_contextual(match, stage2)
        info, events = match.info, match.events
        timeline = Timeline(events)
        context = build_context(events, timeline, info)
        keys = [
            KeyEvent(
                t.event_id,
                "goal" if t.kind is TransitionKind.GOAL else "dismissal",
                t.team_id,
                t.bin_index,
            )
            for t in context.transitions
        ]
        for event in events:
            if isinstance(event, Substitution):
                keys.append(
                    KeyEvent(
                        event.event_id,
                        "substitution",
                        event.team_id,
                        timeline.index_of_event(event),
                    )
                )
            elif isinstance(event, FormationChange):
                keys.append(
                    KeyEvent(
                        event.event_id,
                        "formation_change",
                        event.team_id,
                        timeline.index_of_event(event),
                        event.formation,
                    )
                )
        rosters, starters = _rosters(events, timeline, info)
        return cls(
            info=info,
            events={e.event_id: e for e in events},
            timeline=timeline,
            context=context,
            workload=build_workload(events, timeline, info, stage3.config.workload_window_bins),
            series={(s.metric, s.team_id): s for s in stage2.series},
            stage2_evidence={e.evidence_id: e for e in stage2.evidence},
            stage3=stage3,
            candidates=stage3.candidate_by_id(),
            key_events=tuple(sorted(keys, key=lambda k: (k.bin_index, k.event_id))),
            rosters=rosters,
            starters=starters,
        )

    @property
    def bins(self) -> int:
        return len(self.timeline)

    def match_minute(self, index: int) -> int:
        state = self.context.bins[index]
        return state.minute + (45 if state.period == 2 else 0)

    def bin_of(self, event_id: Identifier) -> int | None:
        key = next((k for k in self.key_events if k.event_id == event_id), None)
        if key is not None:
            return key.bin_index
        candidate = self.candidates.get(event_id)
        return None if candidate is None else candidate.bin_index


def _rosters(
    events: tuple[MatchEvent, ...], timeline: Timeline, info: MatchInfo
) -> tuple[dict[Identifier, tuple[frozenset[Identifier], ...]], dict[Identifier, frozenset[str]]]:
    """Players on the pitch at the start of each bin. Same conventions as `workload`: a substitute
    counts from the bin after coming on; a dismissed player leaves after his bin."""
    per_bin: list[list[MatchEvent]] = [[] for _ in range(len(timeline))]
    for event in events:
        per_bin[timeline.index_of_event(event)].append(event)
    rosters: dict[Identifier, tuple[frozenset[Identifier], ...]] = {}
    starters: dict[Identifier, frozenset[Identifier]] = {}
    for team in info.team_ids:
        on = {p.player_id for p in info.sheet(team).starting_xi}
        starters[team] = frozenset(on)
        states = []
        for bin_events in per_bin:
            states.append(frozenset(on))
            for event in bin_events:
                if isinstance(event, Substitution) and event.team_id == team:
                    on.discard(event.player_id)
                    on.add(event.replacement_id)
                elif isinstance(event, Card) and event.card in DISMISSALS and event.team_id == team:
                    on.discard(event.player_id)
        rosters[team] = tuple(states)
    return rosters, starters


def activity_decline_role(c: ContextualCandidate) -> Literal["team", "opponent"] | None:
    """Whose activity the change shows falling, if anyone's."""
    if (c.metric, c.direction) in DECLINE_SIGNALS:
        return "team"
    if c.pattern is not None and c.pattern.pattern in DECLINE_PATTERNS:
        return "team" if c.pattern.subject_team_id == c.team_id else "opponent"
    return None


class CandidateArgs(DomainModel):
    candidate_id: Identifier = Field(max_length=80)


class GameStateArgs(DomainModel):
    candidate_id: Identifier = Field(max_length=80)
    kind: Literal["goal", "dismissal"]
    lookback_bins: int = Field(default=LOOKBACK_BINS, ge=1, le=15)


class KeyEventArgs(DomainModel):
    team_id: Identifier = Field(max_length=80)
    kind: KeyEventKind
    start_bin: int = Field(ge=0)
    end_bin: int = Field(ge=1)
    anchor_bin: int = Field(ge=0)


class CompareArgs(DomainModel):
    team_id: Identifier = Field(max_length=80)
    metric: str = Field(max_length=40)
    before_start: int = Field(ge=0)
    before_end: int = Field(ge=1)
    after_start: int = Field(ge=0)
    after_end: int = Field(ge=1)


class InvolvementArgs(DomainModel):
    team_id: Identifier = Field(max_length=80)
    metric: str = Field(max_length=40)
    start_bin: int = Field(ge=0)
    end_bin: int = Field(ge=1)


class WorkloadArgs(DomainModel):
    team_id: Identifier = Field(max_length=80)
    bin: int = Field(ge=0)


class TeamChangesArgs(DomainModel):
    team_id: Identifier = Field(max_length=80)
    start_bin: int = Field(ge=0)
    end_bin: int = Field(ge=1)
    anchor_bin: int = Field(ge=0)
    min_level: Literal["weak", "moderate", "strong"] = "moderate"


@dataclass(frozen=True)
class ToolSpec:
    name: ToolName
    arguments: type[DomainModel]
    decision: str


TOOL_SPECS: dict[ToolName, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            ToolName.GET_CANDIDATE_ASSESSMENT,
            CandidateArgs,
            "How strong is the change, and does Stage 3 context already explain it?",
        ),
        ToolSpec(
            ToolName.INSPECT_PERSISTENCE,
            CandidateArgs,
            "Did the change hold across its window, or fade or reverse?",
        ),
        ToolSpec(
            ToolName.CHECK_GAME_STATE_RESPONSE,
            GameStateArgs,
            "Was there a goal or dismissal just before, and is the change its ordinary response?",
        ),
        ToolSpec(
            ToolName.GET_KEY_EVENTS,
            KeyEventArgs,
            "Which goals, dismissals, substitutions or formation changes fall in a span?",
        ),
        ToolSpec(
            ToolName.COMPARE_WINDOWS,
            CompareArgs,
            "Did a metric change between two spans (supporting or contradicting signal)?",
        ),
        ToolSpec(
            ToolName.GET_SUBSTITUTE_INVOLVEMENT,
            InvolvementArgs,
            "Were substitutes involved in a metric's events more than their minutes predict?",
        ),
        ToolSpec(
            ToolName.GET_WORKLOAD,
            WorkloadArgs,
            "How long have this team's outfield players been on, and how active were they?",
        ),
        ToolSpec(
            ToolName.FIND_TEAM_CHANGES,
            TeamChangesArgs,
            "Did a team show a graded Stage 3 change in a span (e.g. the opponent, first)?",
        ),
    )
}


@dataclass(frozen=True)
class _Result:
    team_id: Identifier | None
    span: tuple[int, int] | None
    facts: dict[str, Fact]
    event_ids: tuple[Identifier, ...]
    summary: str


class ToolBox:
    """Validates arguments against the match, runs one tool, returns an evidence item."""

    def __init__(self, workspace: MatchWorkspace) -> None:
        self.ws = workspace
        self._handlers: dict[ToolName, Callable[[Any], _Result]] = {
            ToolName.GET_CANDIDATE_ASSESSMENT: self._candidate_assessment,
            ToolName.INSPECT_PERSISTENCE: self._persistence,
            ToolName.CHECK_GAME_STATE_RESPONSE: self._game_state_response,
            ToolName.GET_KEY_EVENTS: self._key_events,
            ToolName.COMPARE_WINDOWS: self._compare_windows,
            ToolName.GET_SUBSTITUTE_INVOLVEMENT: self._substitute_involvement,
            ToolName.GET_WORKLOAD: self._workload,
            ToolName.FIND_TEAM_CHANGES: self._team_changes,
        }

    def run(self, request: EvidenceRequest, evidence_id: Identifier) -> EvidenceItem:
        spec = TOOL_SPECS[request.tool]
        try:
            args = spec.arguments.model_validate(request.arguments)
        except ValidationError as exc:
            raise ToolError(f"invalid arguments: {exc.error_count()} error(s)") from exc
        result = self._handlers[request.tool](args)
        return EvidenceItem(
            evidence_id=evidence_id,
            request_id=request.request_id,
            tool=request.tool,
            arguments=dict(request.arguments),
            team_id=result.team_id,
            span=result.span,
            facts=result.facts,
            event_ids=result.event_ids,
            summary=result.summary,
        )

    # --- validation helpers -------------------------------------------------------------

    def _team(self, team_id: Identifier) -> Identifier:
        if team_id not in self.ws.info.team_ids:
            raise ToolError("unknown team_id")
        return team_id

    def _metric(self, metric: str) -> str:
        if metric not in METRIC_BY_NAME:
            raise ToolError("unknown metric")
        return metric

    def _span(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= self.ws.bins:
            raise ToolError("span outside the match timeline")
        if end - start > MAX_SPAN_BINS:
            raise ToolError(f"span longer than {MAX_SPAN_BINS} bins")
        return (start, end)

    def _bin(self, index: int) -> int:
        if not 0 <= index < self.ws.bins:
            raise ToolError("bin outside the match timeline")
        return index

    def _candidate(self, candidate_id: Identifier) -> ContextualCandidate:
        candidate = self.ws.candidates.get(candidate_id)
        if candidate is None:
            raise ToolError("unknown candidate_id")
        return candidate

    def _role(self, team_id: Identifier, reference: Identifier) -> str:
        return "team" if team_id == reference else "opponent"

    # --- tools --------------------------------------------------------------------------

    def _candidate_assessment(self, args: CandidateArgs) -> _Result:
        c = self._candidate(args.candidate_id)
        side = self.ws.info.team_ids.index(c.team_id)
        state = self.ws.context.bins[c.bin_index]
        pattern = c.pattern
        facts: dict[str, Fact] = {
            "team_id": c.team_id,
            "opponent_id": self.ws.info.opponent_of(c.team_id),
            "subject_team_id": pattern.subject_team_id if pattern else c.team_id,
            "metric": c.metric,
            "family": c.family,
            "direction": c.direction,
            "onset_bin": c.bin_index,
            "match_minute": self.ws.match_minute(c.bin_index),
            "period": state.period,
            "level": c.level.value,
            "level_rank": c.level.rank,
            "strength": c.strength.value,
            "stage2_statistic": round(c.stage2_statistic, 3),
            "baseline_kind": c.baseline.kind.value,
            "aligned_with": c.baseline.aligned_with,
            "pattern": pattern.pattern if pattern else None,
            "independent_families": len(pattern.independent_families) if pattern else 0,
            "supporting_signals": len(pattern.supports) if pattern else 0,
            "contradicting_signals": len(pattern.contradictions) if pattern else 0,
            "persistence": c.persistence.persistence.value,
            "activity_decline_role": activity_decline_role(c),
            "game_state": c.context.game_state.value,
            "goal_difference": c.context.goal_difference,
            "players": state.players[side],
            "opponent_players": state.players[1 - side],
        }
        return _Result(
            team_id=c.team_id,
            span=(c.baseline.before[0], c.baseline.after[1]),
            facts=facts,
            event_ids=c.event_ids,
            summary=f"{c.statement} Stage 3: {c.level.value} ({'; '.join(c.level_basis)}).",
        )

    def _persistence(self, args: CandidateArgs) -> _Result:
        c = self._candidate(args.candidate_id)
        windows = c.persistence.sub_windows
        measured = [w for w in windows if w.status != "unavailable"]
        facts: dict[str, Fact] = {
            "persistence": c.persistence.persistence.value,
            "sub_windows": len(windows),
            "sub_windows_measured": len(measured),
            "holds": sum(1 for w in measured if w.status == "hold"),
            "against": sum(1 for w in measured if w.status == "against"),
            "last_status": windows[-1].status if windows else None,
            "continues": c.persistence.continues,
        }
        core = self.ws.series[c.metric, c.team_id]
        return _Result(
            team_id=c.team_id,
            span=c.baseline.after,
            facts=facts,
            event_ids=core.events_between(*c.baseline.after),
            summary=f"The change was {c.persistence.persistence.value} across "
            f"{len(measured)} measured sub-windows.",
        )

    def _game_state_response(self, args: GameStateArgs) -> _Result:
        c = self._candidate(args.candidate_id)
        t = c.bin_index
        lo, hi = t - args.lookback_bins, t + ONSET_TOLERANCE_BINS
        kind = TransitionKind(args.kind)
        found = [
            x for x in self.ws.context.transitions if x.kind is kind and lo <= x.bin_index <= hi
        ]
        aligned = [x for x in found if (c.metric, c.direction) in expected_response(x, c.team_id)]
        pool = aligned or found
        nearest = min(pool, key=lambda x: (abs(x.bin_index - t), x.bin_index), default=None)
        coincidence = self.ws.stage3.config.coincidence_bins
        facts: dict[str, Fact] = {
            "kind": args.kind,
            "lookback_bins": args.lookback_bins,
            "transitions": len(found),
            "nearest_event_id": nearest.event_id if nearest else None,
            "nearest_offset": nearest.bin_index - t if nearest else None,
            "nearest_team_role": self._role(nearest.team_id, c.team_id) if nearest else None,
            "ordinary_response": bool(aligned) if found else None,
            "coincident": abs(nearest.bin_index - t) < coincidence if nearest else None,
            "baseline_kind": c.baseline.kind.value,
            "same_regime_survived": c.baseline.kind is BaselineKind.SAME_REGIME,
        }
        if nearest is None:
            summary = f"No {args.kind} from {args.lookback_bins} minutes before the change."
        else:
            relation = "the ordinary response" if aligned else "not the ordinary response"
            summary = (
                f"{args.kind.capitalize()} {nearest.bin_index - t:+d} min from the change "
                f"({facts['nearest_team_role']}); the change is {relation}."
            )
        return _Result(
            team_id=c.team_id,
            span=(max(0, lo), min(self.ws.bins, hi + 1)),
            facts=facts,
            event_ids=tuple(x.event_id for x in found),
            summary=summary,
        )

    def _key_events(self, args: KeyEventArgs) -> _Result:
        team = self._team(args.team_id)
        span = self._span(args.start_bin, args.end_bin)
        anchor = self._bin(args.anchor_bin)
        found = [
            k
            for k in self.ws.key_events
            if k.kind == args.kind and k.team_id == team and span[0] <= k.bin_index < span[1]
        ]
        nearest = min(found, key=lambda k: (abs(k.bin_index - anchor), k.bin_index), default=None)
        facts: dict[str, Fact] = {
            "kind": args.kind,
            "count": len(found),
            "nearest_event_id": nearest.event_id if nearest else None,
            "nearest_offset": nearest.bin_index - anchor if nearest else None,
            "first_offset": found[0].bin_index - anchor if found else None,
            "last_offset": found[-1].bin_index - anchor if found else None,
            "nearest_formation": nearest.formation if nearest else None,
        }
        return _Result(
            team_id=team,
            span=span,
            facts=facts,
            event_ids=tuple(k.event_id for k in found),
            summary=f"{len(found)} {args.kind.replace('_', ' ')} event(s) in the span"
            + (
                f"; nearest {nearest.bin_index - anchor:+d} min from the anchor."
                if nearest
                else "."
            ),
        )

    def _compare_windows(self, args: CompareArgs) -> _Result:
        team = self._team(args.team_id)
        metric = self._metric(args.metric)
        before = self._span(args.before_start, args.before_end)
        after = self._span(args.after_start, args.after_end)
        if before[1] > after[0]:
            raise ToolError("before-window must end at or before the after-window starts")
        series, spec = self.ws.series[metric, team], METRIC_BY_NAME[metric]
        shift = compare_spans(series, spec, before, after)
        facts: dict[str, Fact] = {
            "metric": metric,
            "before_value": round(shift.before.value, 4) if shift else None,
            "after_value": round(shift.after.value, 4) if shift else None,
            "delta": round(shift.delta, 4) if shift else None,
            "statistic": round(shift.statistic, 3) if shift else None,
            "material": is_material(spec, shift) if shift else None,
            "before_sample": shift.before.sample if shift else None,
            "after_sample": shift.after.sample if shift else None,
        }
        summary = (
            f"{spec.title}: {shift.before.value:.3g} to {shift.after.value:.3g} "
            f"(z {shift.statistic:+.2f})."
            if shift
            else f"{spec.title}: too few samples to compare."
        )
        return _Result(
            team_id=team,
            span=(before[0], after[1]),
            facts=facts,
            event_ids=series.events_between(*after),
            summary=summary,
        )

    def _substitute_involvement(self, args: InvolvementArgs) -> _Result:
        team = self._team(args.team_id)
        metric = self._metric(args.metric)
        lo, hi = self._span(args.start_bin, args.end_bin)
        starters = self.ws.starters[team]
        rosters = self.ws.rosters[team][lo:hi]
        player_minutes = sum(len(r) for r in rosters)
        sub_minutes = sum(len(r - starters) for r in rosters)
        substitutes = sorted({p for r in rosters for p in r - starters})
        ids = self.ws.series[metric, team].events_between(lo, hi)
        by_player: dict[Identifier, list[Identifier]] = {}
        for event_id in ids:
            player = getattr(self.ws.events[event_id], "player_id", None)
            if player is not None:
                by_player.setdefault(player, []).append(event_id)
        attributed = sum(len(v) for v in by_player.values())
        sub_events = [e for p in substitutes for e in by_player.get(p, [])]
        expected = sub_minutes / player_minutes if player_minutes else 0.0
        share = len(sub_events) / attributed if attributed else None
        top = max(substitutes, key=lambda p: (len(by_player.get(p, [])), p), default=None)
        facts: dict[str, Fact] = {
            "metric": metric,
            "substitutes": len(substitutes),
            "metric_events": attributed,
            "substitute_events": len(sub_events),
            "substitute_share": round(share, 4) if share is not None else None,
            "expected_share": round(expected, 4),
            "share_ratio": round(share / expected, 3) if share is not None and expected else None,
            "top_substitute_id": top,
        }
        return _Result(
            team_id=team,
            span=(lo, hi),
            facts=facts,
            event_ids=tuple(sub_events),
            summary=f"Substitutes: {len(sub_events)} of {attributed} {metric} events against an "
            f"expected share of {expected:.0%} of player-minutes.",
        )

    def _workload(self, args: WorkloadArgs) -> _Result:
        team = self._team(args.team_id)
        b = self._bin(args.bin)
        snapshot = self.ws.workload[team][b]
        side = self.ws.info.team_ids.index(team)
        minute = self.ws.match_minute(b)
        facts: dict[str, Fact] = {
            "match_minute": minute,
            "late_match": minute >= LATE_MATCH_MINUTE,
            "outfield_players": snapshot.outfield_players,
            "mean_outfield_minutes": round(snapshot.mean_outfield_minutes, 2),
            "substitutions_made": self.ws.context.bins[b].substitutions[side],
            "recent_pressures_per_player": round(snapshot.recent_pressures_per_player, 3),
            "recent_actions_per_player": round(snapshot.recent_actions_per_player, 3),
        }
        subs = tuple(
            k.event_id
            for k in self.ws.key_events
            if k.kind == "substitution" and k.team_id == team and k.bin_index < b
        )
        return _Result(
            team_id=team,
            span=(snapshot.bin_index - snapshot.recent_minutes, b),
            facts=facts,
            event_ids=subs,
            summary=f"At {minute}': outfield players on for {snapshot.mean_outfield_minutes:.0f} "
            f"min on average; {facts['substitutions_made']} substitution(s) made.",
        )

    def _team_changes(self, args: TeamChangesArgs) -> _Result:
        team = self._team(args.team_id)
        lo, hi = self._span(args.start_bin, args.end_bin)
        anchor = self._bin(args.anchor_bin)
        floor = MIN_LEVELS[args.min_level].rank
        found = sorted(
            (
                c
                for c in self.ws.stage3.candidates
                if c.team_id == team and lo <= c.bin_index < hi and c.level.rank >= floor
            ),
            key=lambda c: (c.bin_index, c.rank),
        )
        first = found[0] if found else None
        strong = [c for c in found if c.level is EvidenceLevel.STRONG]
        first_strong = strong[0] if strong else None
        facts: dict[str, Fact] = {
            "min_level": args.min_level,
            "count": len(found),
            "earliest_candidate_id": first.candidate_id if first else None,
            "earliest_offset": first.bin_index - anchor if first else None,
            "earliest_metric": first.metric if first else None,
            "earliest_direction": first.direction if first else None,
            "earliest_level": first.level.value if first else None,
            "strong_count": len(strong),
            "earliest_strong_candidate_id": first_strong.candidate_id if first_strong else None,
            "earliest_strong_offset": first_strong.bin_index - anchor if first_strong else None,
        }
        shown = first_strong or first
        return _Result(
            team_id=team,
            span=(lo, hi),
            facts=facts,
            event_ids=shown.event_ids if shown else (),
            summary=f"{len(found)} change(s) at {args.min_level} or above in the span"
            + (f"; earliest {first.statement}" if first else "."),
        )
