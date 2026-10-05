"""The hypothesis catalogue: which explanations exist, when each is plausible, and what evidence
is relevant to it. Deterministic; shared by the case file, the reasoner and the verifier.

The plausibility screen fixes the set of alternatives an investigation must address. Agents can
add hypotheses but cannot drop a plausible one: the verifier refuses SUPPORTED while any plausible
alternative is unaddressed (docs/agentic-investigation.md#hypotheses).
"""

from dataclasses import dataclass

from matcheyes.agents.contracts import Comparator, EvidenceItem, Fact, HypothesisKind, ToolName
from matcheyes.agents.tools import (
    LATE_MATCH_MINUTE,
    LOOKBACK_BINS,
    ONSET_TOLERANCE_BINS,
    KeyEvent,
    MatchWorkspace,
    activity_decline_role,
)
from matcheyes.analytics.contextual import ContextualCandidate
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.entities import Identifier

H = HypothesisKind
T = ToolName

SUBSTITUTE_SHARE_RATIO = 2.0
"""Substitutes must produce at least twice the share of a metric's events that their share of
player-minutes predicts. Stage 2's involvement evidence uses 1.25 to *report* a player; an
explanation needs a clearer margin. Reasoned, not tuned."""

MIN_SUBSTITUTE_EVENTS = 3
"""Below three events a share ratio is one or two touches, not involvement."""

EXPOSED_MINUTES = 60.0
"""Mean outfield minutes from which late-match decline is a supported reading of workload."""

REFRESHED_MINUTES = 50.0
"""Mean outfield minutes below which the side is too refreshed for late-match decline."""

STRONG_RANK = EvidenceLevel.STRONG.rank
"""Only a Strong, sustained change weakens natural variation. Stage 3's evaluation found about
2.5 Moderate-or-better moments per control match (docs/stage3-evaluation.md), so Moderate is
within ordinary variation and cannot eliminate it."""

PRIORITY: tuple[HypothesisKind, ...] = (
    H.NUMERICAL_CHANGE,
    H.SCORE_STATE_RESPONSE,
    H.FORMATION_CHANGE,
    H.PERSONNEL_CHANGE,
    H.OPPONENT_DRIVEN,
    H.LATE_MATCH_DECLINE,
    H.TACTICAL_CHANGE,
    H.NATURAL_VARIATION,
)
"""Order of preference among verified-supported explanations: a specific observable trigger
before a residual explanation. Ordering only picks which survivor is reported as leading; it
never makes an explanation eligible for a higher claim."""

TRIGGERED = frozenset(
    {
        H.SCORE_STATE_RESPONSE,
        H.NUMERICAL_CHANGE,
        H.PERSONNEL_CHANGE,
        H.FORMATION_CHANGE,
        H.OPPONENT_DRIVEN,
    }
)
"""Explanations that name a cause which must precede the change (temporal gate)."""

RESIDUAL = frozenset({H.TACTICAL_CHANGE})
"""Explanations whose only support is the change itself (no independent observable evidence of
an instruction exists in event data). Eliminating alternatives leaves them the best remaining
reading, never a supported cause, so they are capped at HYPOTHESISED."""

TRIGGER_FACTS: dict[HypothesisKind, str] = {
    H.SCORE_STATE_RESPONSE: "nearest_event_id",
    H.NUMERICAL_CHANGE: "nearest_event_id",
    H.PERSONNEL_CHANGE: "nearest_event_id",
    H.FORMATION_CHANGE: "nearest_event_id",
    H.OPPONENT_DRIVEN: "earliest_strong_candidate_id",
}
"""Fact naming the cause in a supporting evidence item; the verifier looks its time up itself."""

RELEVANT_TOOLS: dict[HypothesisKind, frozenset[ToolName]] = {
    H.TACTICAL_CHANGE: frozenset(
        {
            T.GET_CANDIDATE_ASSESSMENT,
            T.INSPECT_PERSISTENCE,
            T.COMPARE_WINDOWS,
            T.GET_KEY_EVENTS,
            T.CHECK_GAME_STATE_RESPONSE,
        }
    ),
    H.SCORE_STATE_RESPONSE: frozenset({T.CHECK_GAME_STATE_RESPONSE, T.GET_CANDIDATE_ASSESSMENT}),
    H.NUMERICAL_CHANGE: frozenset({T.CHECK_GAME_STATE_RESPONSE, T.GET_CANDIDATE_ASSESSMENT}),
    H.PERSONNEL_CHANGE: frozenset(
        {T.GET_KEY_EVENTS, T.GET_SUBSTITUTE_INVOLVEMENT, T.GET_CANDIDATE_ASSESSMENT}
    ),
    H.FORMATION_CHANGE: frozenset({T.GET_KEY_EVENTS, T.GET_CANDIDATE_ASSESSMENT}),
    H.OPPONENT_DRIVEN: frozenset({T.FIND_TEAM_CHANGES, T.COMPARE_WINDOWS}),
    H.LATE_MATCH_DECLINE: frozenset(
        {T.GET_WORKLOAD, T.GET_CANDIDATE_ASSESSMENT, T.INSPECT_PERSISTENCE}
    ),
    H.NATURAL_VARIATION: frozenset(
        {T.GET_CANDIDATE_ASSESSMENT, T.INSPECT_PERSISTENCE, T.COMPARE_WINDOWS}
    ),
}


def compare(actual: Fact, comparator: Comparator, value: Fact) -> bool:
    """Strict comparison: booleans only equal booleans; ordering needs two numbers."""
    if isinstance(actual, bool) != isinstance(value, bool):
        return comparator is Comparator.NE
    if comparator is Comparator.EQ:
        return actual == value
    if comparator is Comparator.NE:
        return actual != value
    if not isinstance(actual, int | float) or not isinstance(value, int | float):
        return False
    if comparator is Comparator.GT:
        return actual > value
    if comparator is Comparator.GE:
        return actual >= value
    if comparator is Comparator.LT:
        return actual < value
    return actual <= value


_LOWER = frozenset({Comparator.GE, Comparator.GT})
_UPPER = frozenset({Comparator.LE, Comparator.LT})


def entails(comparator: Comparator, value: Fact, test: "Condition") -> bool:
    """Whether every value satisfying `x <comparator> value` also satisfies the test.

    An assertion counts as material only if what it *states* implies the rule: `level_rank >= 0`
    is true of a Strong change but says nothing about strength, so it is not support."""
    rule, bound = test.comparator, test.value
    if comparator is Comparator.EQ:
        return compare(value, rule, bound)
    if comparator is Comparator.NE:
        return rule is Comparator.NE and compare(value, Comparator.EQ, bound)
    if rule not in _LOWER | _UPPER or isinstance(value, bool) or isinstance(bound, bool):
        return False
    if not isinstance(value, int | float) or not isinstance(bound, int | float):
        return False
    if comparator in _LOWER and rule in _LOWER:
        strict_needed = rule is Comparator.GT and comparator is Comparator.GE
        return value > bound if strict_needed else value >= bound
    if comparator in _UPPER and rule in _UPPER:
        strict_needed = rule is Comparator.LT and comparator is Comparator.LE
        return value < bound if strict_needed else value <= bound
    return False


@dataclass(frozen=True)
class Condition:
    fact: str
    comparator: Comparator
    value: Fact

    def holds(self, facts: dict[str, Fact]) -> bool:
        return self.fact in facts and compare(facts[self.fact], self.comparator, self.value)


@dataclass(frozen=True)
class Rule:
    """A fact of one tool's result that counts as material evidence for or against a hypothesis.

    `absence` rules ("no goal in the window") only count when the tool looked at the whole
    trigger window; the verifier checks the item's span.
    """

    tool: ToolName
    test: Condition
    where: tuple[Condition, ...] = ()
    absence: bool = False

    def holds(self, facts: dict[str, Fact]) -> bool:
        return self.test.holds(facts) and all(w.holds(facts) for w in self.where)


def _rule(
    tool: ToolName,
    fact: str,
    comparator: Comparator,
    value: Fact,
    *where: tuple[str, Comparator, Fact],
    absence: bool = False,
) -> Rule:
    return Rule(
        tool,
        Condition(fact, comparator, value),
        tuple(Condition(*w) for w in where),
        absence,
    )


C = Comparator
_GOAL = ("kind", C.EQ, "goal")
_DISMISSAL = ("kind", C.EQ, "dismissal")
_FULL_LOOKBACK = ("lookback_bins", C.GE, LOOKBACK_BINS)
_SUB = ("kind", C.EQ, "substitution")
_FORMATION = ("kind", C.EQ, "formation_change")
_NOT_STRONG_ONLY = ("min_level", C.NE, "strong")
_SUSTAINED = ("persistence", C.EQ, "sustained")


def _game_state_rules(kind: tuple[str, Comparator, Fact]) -> tuple[Rule, ...]:
    return (
        _rule(
            T.CHECK_GAME_STATE_RESPONSE, "transitions", C.EQ, 0, kind, _FULL_LOOKBACK, absence=True
        ),
        _rule(T.CHECK_GAME_STATE_RESPONSE, "ordinary_response", C.EQ, False, kind),
        _rule(T.CHECK_GAME_STATE_RESPONSE, "same_regime_survived", C.EQ, True, kind),
    )


SUPPORT_GROUPS: dict[HypothesisKind, tuple[tuple[Rule, ...], ...]] = {
    H.SCORE_STATE_RESPONSE: (
        (_rule(T.CHECK_GAME_STATE_RESPONSE, "ordinary_response", C.EQ, True, _GOAL),),
    ),
    H.NUMERICAL_CHANGE: (
        (_rule(T.CHECK_GAME_STATE_RESPONSE, "ordinary_response", C.EQ, True, _DISMISSAL),),
    ),
    H.PERSONNEL_CHANGE: (
        (_rule(T.GET_KEY_EVENTS, "count", C.GE, 1, _SUB),),
        (
            _rule(
                T.GET_SUBSTITUTE_INVOLVEMENT,
                "share_ratio",
                C.GE,
                SUBSTITUTE_SHARE_RATIO,
                ("substitute_events", C.GE, MIN_SUBSTITUTE_EVENTS),
            ),
        ),
    ),
    H.FORMATION_CHANGE: ((_rule(T.GET_KEY_EVENTS, "count", C.GE, 1, _FORMATION),),),
    H.OPPONENT_DRIVEN: (
        (
            _rule(
                T.FIND_TEAM_CHANGES,
                "strong_count",
                C.GE,
                1,
                ("earliest_strong_offset", C.LE, -1),
                ("earliest_strong_offset", C.GE, -LOOKBACK_BINS),
            ),
        ),
    ),
    H.LATE_MATCH_DECLINE: (
        (
            _rule(
                T.GET_WORKLOAD,
                "mean_outfield_minutes",
                C.GE,
                EXPOSED_MINUTES,
                ("late_match", C.EQ, True),
            ),
        ),
        (_rule(T.GET_CANDIDATE_ASSESSMENT, "activity_decline_role", C.NE, None),),
        (_rule(T.GET_CANDIDATE_ASSESSMENT, "level_rank", C.GE, STRONG_RANK),),
    ),
    H.TACTICAL_CHANGE: (
        (_rule(T.GET_CANDIDATE_ASSESSMENT, "level_rank", C.GE, STRONG_RANK),),
        (
            _rule(T.INSPECT_PERSISTENCE, "persistence", C.EQ, "sustained"),
            _rule(T.GET_CANDIDATE_ASSESSMENT, "persistence", C.EQ, "sustained"),
        ),
    ),
    H.NATURAL_VARIATION: (
        (_rule(T.GET_CANDIDATE_ASSESSMENT, "level_rank", C.LE, 1),),
        (
            _rule(T.INSPECT_PERSISTENCE, "persistence", C.EQ, "transient"),
            _rule(T.INSPECT_PERSISTENCE, "persistence", C.EQ, "reversed"),
            _rule(T.GET_CANDIDATE_ASSESSMENT, "persistence", C.EQ, "transient"),
            _rule(T.GET_CANDIDATE_ASSESSMENT, "persistence", C.EQ, "reversed"),
        ),
    ),
}
"""SUPPORTED needs every group matched by a cited, true assertion; PARTIALLY_SUPPORTED needs one.

Untriggered explanations (tactical change, late-match decline, opponent-driven) have no discrete
observable cause, so their support rests on the strength of a change itself - the team's, or
for opponent-driven the opponent's earlier one - and that must be Strong: Moderate changes occur
in control matches at about 2.5 per match (STRONG_RANK). Triggered explanations rest on an
observable cause that precedes the change plus a directional or involvement test."""

CONTRADICTIONS: dict[HypothesisKind, tuple[Rule, ...]] = {
    H.SCORE_STATE_RESPONSE: _game_state_rules(_GOAL),
    H.NUMERICAL_CHANGE: _game_state_rules(_DISMISSAL),
    H.PERSONNEL_CHANGE: (
        _rule(T.GET_KEY_EVENTS, "count", C.EQ, 0, _SUB, absence=True),
        _rule(T.GET_SUBSTITUTE_INVOLVEMENT, "share_ratio", C.LE, 1.0),
    ),
    H.FORMATION_CHANGE: (_rule(T.GET_KEY_EVENTS, "count", C.EQ, 0, _FORMATION, absence=True),),
    H.OPPONENT_DRIVEN: (
        _rule(T.FIND_TEAM_CHANGES, "count", C.EQ, 0, _NOT_STRONG_ONLY, absence=True),
    ),
    H.LATE_MATCH_DECLINE: (
        _rule(T.GET_CANDIDATE_ASSESSMENT, "activity_decline_role", C.EQ, None),
        _rule(T.GET_WORKLOAD, "mean_outfield_minutes", C.LT, REFRESHED_MINUTES),
        _rule(T.GET_WORKLOAD, "late_match", C.EQ, False),
    ),
    H.TACTICAL_CHANGE: (
        _rule(T.GET_CANDIDATE_ASSESSMENT, "persistence", C.EQ, "transient"),
        _rule(T.GET_CANDIDATE_ASSESSMENT, "persistence", C.EQ, "reversed"),
        _rule(T.INSPECT_PERSISTENCE, "persistence", C.EQ, "transient"),
        _rule(T.INSPECT_PERSISTENCE, "persistence", C.EQ, "reversed"),
    ),
    H.NATURAL_VARIATION: (
        _rule(T.GET_CANDIDATE_ASSESSMENT, "level_rank", C.GE, STRONG_RANK, _SUSTAINED),
    ),
}
"""Material contradictions. The verifier also scans the whole evidence pool for these, so an
agent cannot hide a contradiction by not citing it. TACTICAL_CHANGE is additionally contradicted,
by definition, when a triggered explanation is verified as supported."""


def trigger_window(candidate: ContextualCandidate) -> tuple[int, int]:
    """Inclusive bins in which a trigger counts as preceding the change."""
    t = candidate.bin_index
    return (t - LOOKBACK_BINS, t + ONSET_TOLERANCE_BINS)


def subject_team(candidate: ContextualCandidate) -> Identifier:
    return candidate.pattern.subject_team_id if candidate.pattern else candidate.team_id


def decline_team(ws: MatchWorkspace, candidate: ContextualCandidate) -> Identifier | None:
    role = activity_decline_role(candidate)
    if role is None:
        return None
    return candidate.team_id if role == "team" else ws.info.opponent_of(candidate.team_id)


def relevant_teams(
    ws: MatchWorkspace, candidate: ContextualCandidate, kind: HypothesisKind
) -> frozenset[Identifier]:
    team, both = candidate.team_id, frozenset(ws.info.team_ids)
    if kind in (H.PERSONNEL_CHANGE, H.FORMATION_CHANGE):
        return frozenset({team})
    if kind is H.OPPONENT_DRIVEN:
        return frozenset({ws.info.opponent_of(team)})
    if kind is H.LATE_MATCH_DECLINE:
        decliner = decline_team(ws, candidate)
        return both if decliner is None else frozenset({decliner})
    return both


CANDIDATE_TOOLS = frozenset(
    {T.GET_CANDIDATE_ASSESSMENT, T.INSPECT_PERSISTENCE, T.CHECK_GAME_STATE_RESPONSE}
)
"""Tools about the candidate itself; their team is the candidate's by construction."""

RELEVANCE_MARGIN_BINS = 5


def irrelevance(
    ws: MatchWorkspace,
    candidate: ContextualCandidate,
    kind: HypothesisKind,
    item: EvidenceItem,
) -> str | None:
    """Why an evidence item cannot bear on a hypothesis about this candidate, or None."""
    if item.tool not in RELEVANT_TOOLS[kind]:
        return f"{item.tool.value} does not test {kind.value}"
    if item.tool in CANDIDATE_TOOLS:
        if item.arguments.get("candidate_id") != candidate.candidate_id:
            return "about a different candidate"
        return None
    if item.team_id not in relevant_teams(ws, candidate, kind):
        return "about a team this explanation does not concern"
    if item.span is not None:
        lo = candidate.bin_index - LOOKBACK_BINS - RELEVANCE_MARGIN_BINS
        hi = candidate.baseline.after[1] + RELEVANCE_MARGIN_BINS
        if item.span[1] <= lo or item.span[0] >= hi:
            return "outside the period under investigation"
    return None


def absence_span(
    ws: MatchWorkspace, candidate: ContextualCandidate, tool: ToolName
) -> tuple[int, int] | None:
    """The span a tool must have covered for "found nothing" to count as a contradiction."""
    lo, hi = trigger_window(candidate)
    if tool is T.GET_KEY_EVENTS:
        return (max(0, lo), min(ws.bins, hi + 1))
    if tool is T.FIND_TEAM_CHANGES:
        t = candidate.bin_index
        return (max(0, t - LOOKBACK_BINS), t)
    return None


def covers_absence(ws: MatchWorkspace, candidate: ContextualCandidate, item: EvidenceItem) -> bool:
    need = absence_span(ws, candidate, item.tool)
    if need is None:
        return True
    return item.span is not None and item.span[0] <= need[0] and item.span[1] >= need[1]


def triggers_in_window(ws: MatchWorkspace, candidate: ContextualCandidate) -> tuple[KeyEvent, ...]:
    lo, hi = trigger_window(candidate)
    return tuple(k for k in ws.key_events if lo <= k.bin_index <= hi)


def screen(
    ws: MatchWorkspace, candidate: ContextualCandidate
) -> tuple[tuple[HypothesisKind, str], ...]:
    """Plausible explanations and why each is plausible, in priority order."""
    found = triggers_in_window(ws, candidate)
    team = candidate.team_id
    reasons: dict[HypothesisKind, str] = {
        H.TACTICAL_CHANGE: "always considered",
        H.OPPONENT_DRIVEN: "always considered",
        H.NATURAL_VARIATION: "always considered",
    }
    if any(k.kind == "goal" for k in found):
        reasons[H.SCORE_STATE_RESPONSE] = "a goal falls in the trigger window"
    if any(k.kind == "dismissal" for k in found):
        reasons[H.NUMERICAL_CHANGE] = "a dismissal falls in the trigger window"
    if any(k.kind == "substitution" and k.team_id == team for k in found):
        reasons[H.PERSONNEL_CHANGE] = "the team made a substitution in the trigger window"
    if any(k.kind == "formation_change" and k.team_id == team for k in found):
        reasons[H.FORMATION_CHANGE] = "the team changed formation in the trigger window"
    if ws.match_minute(candidate.bin_index) >= LATE_MATCH_MINUTE:
        reasons[H.LATE_MATCH_DECLINE] = f"the change starts after {LATE_MATCH_MINUTE}'"
    return tuple((kind, reasons[kind]) for kind in PRIORITY if kind in reasons)
