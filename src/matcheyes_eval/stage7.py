"""Stage 7 evaluation: the snapshot-anchored insight lifecycle under realistic delivery.

Each case is replayed through `LifecycleEngine` as delivered in sequence order (the reference)
and under transport perturbations (`transport.py`). Measures, by property:

 1 ordering        - bounded reorder and full shuffle give the reference canonical state.
 2 replay          - a fresh replay is byte-identical to the reference.
 3 duplicates      - re-delivered events add no storyline, revision or snapshot.
 4 conflicts       - a changed payload under an existing event ID is rejected; state unchanged.
 5 gap             - a withheld event holds the watermark and reports DATA_INCOMPLETE.
 6 late fill       - the late event releases the buffer; the state equals the reference.
 7 no leakage      - history up to a snapshot is the same whatever came later (gap prefix
                     against reference) and every cited event is in its revision's snapshot.
 8 identity        - storylines against planted truth: fragmentation (storylines per detected
                     expected insight) and purity (expected insights per storyline).
 9 reanchoring     - re-anchored revisions; each within the suppression distance (audit).
10 opposite        - opposite-direction withdrawals and their linked successors (audit).
11 reinstatement   - reinstated revisions keep their storyline ID.
12 append-only     - every intermediate state's history is a prefix of every later one.
13 own snapshot    - the independent lifecycle audit, incl. reproduction of every snapshot.
14 stale views     - a view of a superseded revision is flagged stale; a current one is not.
15 Stage 6         - audience feeds show only current revisions; no view is withheld.
16 unavailable     - a pipeline failure on one snapshot is FAILED, the feed UNAVAILABLE, no
                     revision is anchored there; deterministic across two runs.
17 integrity       - evidence tampered on the latest snapshot: compromised current revisions,
                     their integrity change noticed on that snapshot and warned in every view.
18 corrections     - a late changed payload, or a reused sequence, is rejected; state unchanged.
19 security        - no planted-truth vocabulary or scenario ID in any canonical state; an event
                     of another match is rejected.

Lifecycle faults are injected into genuine states (`model_copy`, which skips validators); each
must be caught by `audit_lifecycle`.

Snapshot evaluations are cached by snapshot ID. A snapshot ID is a content address (match, team
sheet, prefix digest, closed minute), so a cached evaluation is exactly the pipeline's output for
that snapshot; fresh, uncached replays are used where determinism itself is measured.

No targets are set: results are reported as measured.
"""

import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from matcheyes.agents.contracts import (
    EvidenceIntegrity,
    EvidenceItem,
    EvidenceRequest,
    FinalInsight,
)
from matcheyes.agents.tools import MatchWorkspace, ToolBox
from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.events import MatchEvent
from matcheyes.ingestion.log import DataStatus, IngestOutcome
from matcheyes.lifecycle.audit import LIFECYCLE_CHECKS, LifecycleAuditor
from matcheyes.lifecycle.contracts import (
    GENESIS,
    ChangeKind,
    LifecycleState,
    Revision,
    SnapshotOutcome,
    Storyline,
    StorylineState,
    chain_fingerprint,
)
from matcheyes.lifecycle.engine import LifecycleEngine, replay
from matcheyes.lifecycle.evaluate import Evaluator, SnapshotEvaluation, evaluate_snapshot
from matcheyes.lifecycle.feed import (
    CurrentStatus,
    audience_feed,
    current_revisions,
    lifecycle_feed,
    view_is_current,
)
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.orchestration.investigation import investigate_match
from matcheyes.personalization.contracts import (
    Audience,
    PersonalizationProfile,
    SectionKind,
    fingerprint,
)
from matcheyes.personalization.render import personalize
from matcheyes_eval import transport
from matcheyes_eval.redteam import EVIDENCE_FAULTS, Donors, TamperingToolBox
from matcheyes_eval.scoring import Clock
from matcheyes_eval.stage2 import Case, Rate
from matcheyes_eval.stage3 import _has_metric_mechanism, _matches
from matcheyes_eval.stage6 import ONE_ITEM, _only

OTHER_MATCH = "another-match-000"
TRUTH_VOCABULARY = ("scenario_id", "answer_key", "planted", "decoy", "expected_insight")

# --- evaluation cache ----------------------------------------------------------------------------


class CachedEvaluator:
    """Memoises an evaluator by snapshot ID (a content address of the snapshot)."""

    def __init__(self, base: Evaluator = evaluate_snapshot) -> None:
        self.base = base
        self.cache: dict[str, SnapshotEvaluation] = {}
        self.misses = 0

    def __call__(self, snapshot: Snapshot) -> SnapshotEvaluation:
        key = snapshot.header.snapshot_id
        if key not in self.cache:
            self.misses += 1
            self.cache[key] = self.base(snapshot)
        return self.cache[key]


def canonical(state: LifecycleState) -> bytes:
    return state.model_dump_json().encode("utf-8")


class _Failing(ToolBox):
    """A tool backend that is down: an infrastructure error, not a tool's `ToolError`."""

    def run(self, request: EvidenceRequest, evidence_id: str) -> EvidenceItem:
        raise RuntimeError("tool backend unavailable")


def failing_on(watermark: int, base: Evaluator) -> Evaluator:
    """The real evaluator, except that on the snapshot at `watermark` every tool call fails."""

    def evaluate(snapshot: Snapshot) -> SnapshotEvaluation:
        if snapshot.header.watermark == watermark:
            return evaluate_snapshot(snapshot, toolbox=_Failing)
        return base(snapshot)

    return evaluate


def tampered_on(watermark: int, base: Evaluator) -> Evaluator:
    """The real evaluator, except that on the snapshot at `watermark` one evidence value is
    altered after the tool ran (Stage 5 layer B tampering), so the verifier sees a mismatch."""
    tamper = _only(ONE_ITEM, EVIDENCE_FAULTS["altered_metric_value"])

    def toolbox(ws: MatchWorkspace) -> ToolBox:
        return TamperingToolBox(Donors(ws, None, None), tamper)

    def evaluate(snapshot: Snapshot) -> SnapshotEvaluation:
        if snapshot.header.watermark == watermark:
            return evaluate_snapshot(snapshot, toolbox=toolbox)
        return base(snapshot)

    return evaluate


# --- lifecycle faults ----------------------------------------------------------------------------

LifecycleFault = Callable[[LifecycleState, Sequence[MatchEvent]], LifecycleState | None]


def _with(state: LifecycleState, storylines: Iterable[Storyline]) -> LifecycleState:
    return state.model_copy(update={"storylines": tuple(storylines)})


def _replace(state: LifecycleState, s: Storyline) -> LifecycleState:
    return _with(state, (s if x.storyline_id == s.storyline_id else x for x in state.storylines))


def _rechained(revisions: Sequence[Revision]) -> tuple[Revision, ...]:
    """Recompute insight fingerprints and the chain, as a careful forger would."""
    out: list[Revision] = []
    previous = GENESIS
    for r in revisions:
        fp = fingerprint(r.final) if r.final is not None else None
        chain = chain_fingerprint(previous, r.snapshot_id, fp, r.state)
        out.append(
            r.model_copy(
                update={
                    "insight_fingerprint": fp,
                    "previous_chain_fingerprint": previous,
                    "chain_fingerprint": chain,
                }
            )
        )
        previous = chain
    return tuple(out)


def _revised(s: Storyline, index: int, revision: Revision, rechain: bool = True) -> Storyline:
    revisions = [*s.revisions[:index], revision, *s.revisions[index + 1 :]]
    return s.model_copy(
        update={"revisions": _rechained(revisions) if rechain else tuple(revisions)}
    )


def _open_with_history(state: LifecycleState) -> Storyline | None:
    current = {r.storyline_id for r in current_revisions(state)}
    return next(
        (
            s
            for s in state.storylines
            if s.storyline_id in current
            and len(s.revisions) >= 2
            and s.revisions[-2].state is StorylineState.OPEN
        ),
        None,
    )


def _stale_current(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    """A superseded revision presented as current."""
    s = _open_with_history(state)
    if s is None:
        return None
    return _replace(state, s.model_copy(update={"revisions": s.revisions[:-1]}))


def _forged_storyline_id(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    if not state.storylines:
        return None
    s = state.storylines[0]
    forged = "sl-forged-storyline"
    revisions = tuple(r.model_copy(update={"storyline_id": forged}) for r in s.revisions)
    renamed = s.model_copy(update={"storyline_id": forged, "revisions": revisions})
    return _with(state, (renamed, *state.storylines[1:]))


def _first_open_revision(state: LifecycleState) -> tuple[Storyline, int, FinalInsight] | None:
    for s in state.storylines:
        for i, r in enumerate(s.revisions):
            if r.final is not None:
                return s, i, r.final
    return None


def _strength_raised(final: FinalInsight) -> FinalInsight:
    stronger = (
        ClaimStrength.SUPPORTED
        if final.strength is not ClaimStrength.SUPPORTED
        else ClaimStrength.OBSERVED
    )
    return final.model_copy(update={"strength": stronger})


def _rewritten_history(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    """An earlier insight edited in place, fingerprints left as they were."""
    found = _first_open_revision(state)
    if found is None:
        return None
    s, i, final = found
    r = s.revisions[i].model_copy(update={"final": _strength_raised(final)})
    return _replace(state, _revised(s, i, r, rechain=False))


def _rewritten_history_rechained(
    state: LifecycleState, _: Sequence[MatchEvent]
) -> LifecycleState | None:
    """An earlier insight edited in place, with every fingerprint and chain link recomputed."""
    found = _first_open_revision(state)
    if found is None:
        return None
    s, i, final = found
    r = s.revisions[i].model_copy(update={"final": _strength_raised(final)})
    return _replace(state, _revised(s, i, r))


def _reordered_numbering(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    s = next((x for x in state.storylines if len(x.revisions) >= 2), None)
    if s is None:
        return None
    first, second = s.revisions[0], s.revisions[1]
    swapped = (
        first.model_copy(update={"number": 2}),
        second.model_copy(update={"number": 1}),
        *s.revisions[2:],
    )
    return _replace(state, s.model_copy(update={"revisions": swapped}))


def _removed_storyline(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    current = {r.storyline_id for r in current_revisions(state)}
    target = next((s for s in state.storylines if s.storyline_id in current), None)
    if target is None:
        return None
    return _with(state, (s for s in state.storylines if s is not target))


def _integrity_upgraded(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    for s in state.storylines:
        for i, r in enumerate(s.revisions):
            if r.final is not None and r.evidence_integrity is EvidenceIntegrity.COMPROMISED:
                intact = EvidenceIntegrity.INTACT
                final = r.final.model_copy(update={"evidence_integrity": intact})
                forged = r.model_copy(update={"final": final, "evidence_integrity": intact})
                return _replace(state, _revised(s, i, forged))
    return None


def _future_evidence(state: LifecycleState, events: Sequence[MatchEvent]) -> LifecycleState | None:
    """An insight citing an event after its own snapshot, re-fingerprinted."""
    for s in state.storylines:
        for i, r in enumerate(s.revisions):
            if r.final is not None and r.watermark < len(events):
                future = events[r.watermark].event_id
                final = r.final.model_copy(update={"event_ids": (*r.final.event_ids, future)})
                return _replace(state, _revised(s, i, r.model_copy(update={"final": final})))
    return None


def _forged_reopening(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    """A withdrawn storyline shown as open again without any revision."""
    last = state.last_snapshot
    s = next((x for x in state.storylines if x.state is StorylineState.WITHDRAWN), None)
    if s is None or last is None:
        return None
    reopened = s.model_copy(
        update={"state": StorylineState.OPEN, "verified_through": last.header.snapshot_id}
    )
    return _replace(state, reopened)


def _silent_withdrawal_removal(
    state: LifecycleState, _: Sequence[MatchEvent]
) -> LifecycleState | None:
    """The withdrawal revision deleted, so the storyline silently stays current."""
    last = state.last_snapshot
    s = next(
        (
            x
            for x in state.storylines
            if x.state is StorylineState.WITHDRAWN and x.revisions[-2].final is not None
        ),
        None,
    )
    if s is None or last is None:
        return None
    kept = s.revisions[:-1]
    return _replace(
        state,
        s.model_copy(
            update={
                "revisions": kept,
                "state": StorylineState.OPEN,
                "verified_through": last.header.snapshot_id,
            }
        ),
    )


def _wrong_snapshot(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    """A revision moved onto a neighbouring evaluated snapshot, chain recomputed."""
    evaluated = [r.header for r in state.snapshots if r.outcome is SnapshotOutcome.EVALUATED]
    index = {h.snapshot_id: i for i, h in enumerate(evaluated)}
    for s in state.storylines:
        for i, r in enumerate(s.revisions):
            k = index.get(r.snapshot_id)
            if r.final is None or k is None or k == 0:
                continue
            other = evaluated[k - 1]
            if i > 0 and s.revisions[i - 1].watermark >= other.watermark:
                continue
            moved = r.model_copy(
                update={
                    "snapshot_id": other.snapshot_id,
                    "watermark": other.watermark,
                    "as_of": other.as_of,
                }
            )
            return _replace(state, _revised(s, i, moved))
    return None


def _forged_link(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    unlinked = [s for s in state.storylines if s.linked_to is None]
    if len(unlinked) < 2:
        return None
    a, b = unlinked[0], unlinked[1]
    return _replace(state, b.model_copy(update={"linked_to": a.storyline_id}))


def _unlinked_opposite(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    s = next((x for x in state.storylines if x.linked_to is not None), None)
    if s is None:
        return None
    return _replace(state, s.model_copy(update={"linked_to": None}))


def _forged_change_kinds(state: LifecycleState, _: Sequence[MatchEvent]) -> LifecycleState | None:
    for s in state.storylines:
        for i, r in enumerate(s.revisions):
            if i == 0 or r.final is None:
                continue
            kinds = set(r.change_kinds) ^ {ChangeKind.VERDICT_CHANGED}
            forged = r.model_copy(update={"change_kinds": tuple(sorted(kinds))})
            return _replace(state, _revised(s, i, forged, rechain=False))
    return None


LIFECYCLE_FAULTS: dict[str, tuple[frozenset[str], LifecycleFault]] = {
    "stale_current": (frozenset({"current"}), _stale_current),
    "forged_storyline_id": (frozenset({"identity"}), _forged_storyline_id),
    "rewritten_history": (frozenset({"fingerprint", "chain"}), _rewritten_history),
    "rewritten_history_rechained": (
        frozenset({"reproduction", "revision_audit", "change_kinds"}),
        _rewritten_history_rechained,
    ),
    "reordered_numbering": (frozenset({"numbering"}), _reordered_numbering),
    "removed_storyline": (frozenset({"snapshot", "current"}), _removed_storyline),
    "integrity_upgraded": (
        frozenset({"integrity", "reproduction", "revision_audit"}),
        _integrity_upgraded,
    ),
    "future_evidence": (frozenset({"anchoring"}), _future_evidence),
    "forged_reopening": (frozenset({"transitions", "current"}), _forged_reopening),
    "silent_withdrawal_removal": (
        frozenset({"transitions", "current", "linkage"}),
        _silent_withdrawal_removal,
    ),
    "wrong_snapshot": (frozenset({"numbering", "reproduction"}), _wrong_snapshot),
    "forged_link": (frozenset({"linkage"}), _forged_link),
    "unlinked_opposite": (frozenset({"linkage"}), _unlinked_opposite),
    "forged_change_kinds": (frozenset({"change_kinds"}), _forged_change_kinds),
}
"""Each fault, the lifecycle audit check(s) that must catch it, and how it is injected."""


def caught(findings: list[str], expected: frozenset[str]) -> bool:
    return any(f.split(":", 1)[0] in expected for f in findings)


# --- results -------------------------------------------------------------------------------------


@dataclass
class FaultRow:
    injected: int = 0
    caught: int = 0
    caught_by_expected: int = 0


@dataclass
class Stage7Results:
    split: str
    seeds: int
    matches: int = 0
    snapshots: int = 0
    storylines: int = 0
    revisions: int = 0
    replay_seconds: list[float] = field(default_factory=list)
    outcomes: Counter[str] = field(default_factory=Counter)
    change_kinds: Counter[str] = field(default_factory=Counter)
    withdrawals: Counter[str] = field(default_factory=Counter)
    properties: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))
    audit_findings: Counter[str] = field(default_factory=Counter)
    revisions_audit_flagged: int = 0
    fresh_replays: int = 0
    expected_insights: int = 0
    detected: int = 0
    fragmentation: list[int] = field(default_factory=list)
    purity: Counter[int] = field(default_factory=Counter)
    latency_s: list[float] = field(default_factory=list)
    stale_views: Rate = field(default_factory=Rate)
    current_views: Rate = field(default_factory=Rate)
    stage6_views: int = 0
    stage6_withheld: int = 0
    stage6_compromised_warned: Rate = field(default_factory=Rate)
    compromised_current: int = 0
    integrity_noticed: Rate = field(default_factory=Rate)
    faults: dict[str, FaultRow] = field(default_factory=lambda: defaultdict(FaultRow))

    def check(self, name: str, ok: bool) -> None:
        self.properties[name].add(ok)


# --- measures ------------------------------------------------------------------------------------


def _history_is_prefix(earlier: LifecycleState, later: LifecycleState) -> bool:
    """Every storyline of `earlier` exists in `later` with `earlier`'s revisions as a prefix,
    and `earlier`'s snapshots are a prefix of `later`'s."""
    if later.snapshots[: len(earlier.snapshots)] != earlier.snapshots:
        return False
    by_id = {s.storyline_id: s for s in later.storylines}
    for s in earlier.storylines:
        other = by_id.get(s.storyline_id)
        if other is None or other.revisions[: len(s.revisions)] != s.revisions:
            return False
    return True


def _incremental(
    case: Case, delivery: Sequence[MatchEvent], evaluator: Evaluator
) -> tuple[LifecycleEngine, bool]:
    """Replay `delivery`, checking after each reconciled snapshot that history only grows."""
    engine = LifecycleEngine(case.match.info, evaluator)
    previous = engine.state
    append_only = True
    for event in delivery:
        engine.ingest(event)
        if len(engine.state.snapshots) != len(previous.snapshots):
            append_only &= _history_is_prefix(previous, engine.state)
            previous = engine.state
    return engine, append_only


def _same(case: Case, delivery: Sequence[MatchEvent], evaluator: Evaluator, ref: bytes) -> bool:
    return canonical(replay(case.match.info, delivery, evaluator).state) == ref


def _transport(case: Case, ref: LifecycleEngine, cached: CachedEvaluator, r: Stage7Results) -> None:
    events, info = case.match.events, case.match.info
    reference = canonical(ref.state)
    r.check(
        "1 ordering: bounded reorder",
        _same(case, transport.bounded_reorder(events, 25, 1), cached, reference),
    )
    r.check("1 ordering: shuffled", _same(case, transport.shuffled(events, 2), cached, reference))

    delivery, count = transport.duplicated(events, every=7, delay=11)
    dup = replay(info, delivery, cached)
    r.check("3 duplicates: state unchanged", canonical(dup.state) == reference)
    r.check("3 duplicates: counted as no-ops", dup.status().duplicates == count)

    middle = len(events) // 2
    conflict = replay(info, transport.conflicting(events, middle), cached)
    r.check("4 conflicting duplicate rejected", conflict.status().conflicts == 1)
    r.check("4 conflicting duplicate: state unchanged", canonical(conflict.state) == reference)

    gap = replay(info, transport.withheld(events, middle), cached)
    status = gap.status()
    r.check(
        "5 gap: DATA_INCOMPLETE at the gap",
        status.data_status is DataStatus.DATA_INCOMPLETE
        and status.missing_from == middle + 1
        and status.buffered == len(events) - middle - 1,
    )
    r.check(
        "5 gap: no snapshot beyond the watermark",
        all(s.header.watermark <= middle for s in gap.state.snapshots),
    )
    prefix_only = replay(info, events[:middle], cached)
    r.check(
        "5 gap: state equals the prefix replay",
        canonical(gap.state) == canonical(prefix_only.state),
    )
    r.check(
        "7 no leakage: history up to the gap is unchanged by later events",
        _history_is_prefix(gap.state, ref.state),
    )

    filled = LifecycleEngine(info, cached)
    for event in transport.withheld(events, middle):
        filled.ingest(event)
    outcome = filled.ingest(events[middle])
    r.check(
        "6 late fill: watermark advances, state equals reference",
        outcome is IngestOutcome.ACCEPTED
        and filled.status().data_status is DataStatus.CONTIGUOUS
        and canonical(filled.state) == reference,
    )

    late = replay(info, transport.conflicting(events, 10, delay=len(events)), cached)
    r.check(
        "18 corrections: late changed payload rejected",
        late.status().conflicts == 1 and canonical(late.state) == reference,
    )
    reused = replay(info, transport.reused_sequence(events, middle), cached)
    r.check(
        "18 corrections: reused sequence rejected",
        reused.status().conflicts == 1 and canonical(reused.state) == reference,
    )
    foreign = replay(info, transport.cross_match(events, middle, OTHER_MATCH), cached)
    r.check(
        "19 security: event of another match rejected",
        foreign.status().rejected == 1 and canonical(foreign.state) == reference,
    )


def _truth(case: Case, state: LifecycleState, r: Stage7Results) -> None:
    """Storylines against the planted expected insights (evaluation only)."""
    if case.variant != "planted":
        return
    clock = Clock(case.match)
    headers = {s.header.snapshot_id: s.header for s in state.snapshots}
    matched_by: dict[str, set[str]] = defaultdict(set)
    for insight in case.spec.expected_insights:
        if not _has_metric_mechanism(insight):
            continue
        r.expected_insights += 1
        first: float | None = None
        for s in state.storylines:
            for rev in s.revisions:
                if rev.final is None or not _matches(
                    insight, clock, s.team_id, s.metric, s.direction, rev.final.at
                ):
                    continue
                matched_by[s.storyline_id].add(insight.insight_id)
                seen = clock.seconds(headers[rev.snapshot_id].as_of)
                first = seen if first is None else min(first, seen)
        hits = sum(1 for ids in matched_by.values() if insight.insight_id in ids)
        if hits:
            r.detected += 1
            r.fragmentation.append(hits)
        if first is not None:
            r.latency_s.append(first - clock.seconds(insight.window_start))
    for s in state.storylines:
        r.purity[len(matched_by.get(s.storyline_id, set()))] += 1


def _views(state: LifecycleState, auditor: LifecycleAuditor, r: Stage7Results) -> None:
    profile = PersonalizationProfile(audience=Audience.ANALYST)
    current = {rev.insight_fingerprint for rev in current_revisions(state)}
    for s in state.storylines:
        for rev in s.revisions:
            if rev.final is None:
                continue
            view = personalize(rev.final, profile, auditor.workspace(rev.snapshot_id))
            if rev.insight_fingerprint in current:
                r.current_views.add(view_is_current(view, state))
            else:
                r.stale_views.add(not view_is_current(view, state))
    last = state.last_snapshot
    if last is None or last.outcome is not SnapshotOutcome.EVALUATED:
        return
    ws = auditor.workspace(last.header.snapshot_id)
    for audience in Audience:
        feed = audience_feed(state, PersonalizationProfile(audience=audience), ws)
        views = [*feed.primary, *feed.secondary]
        r.stage6_views += len(views)
        r.stage6_withheld += len(feed.withheld)
        r.check(
            "15 Stage 6: every view is of a current revision",
            all(view_is_current(v, state) for v in views),
        )
        for v in views:
            if v.source.evidence_integrity is EvidenceIntegrity.COMPROMISED:
                r.stage6_compromised_warned.add(
                    any(x.kind is SectionKind.INTEGRITY and x.mandatory for x in v.sections)
                )


def _failure(
    case: Case,
    ref: LifecycleEngine,
    cached: CachedEvaluator,
    shared: LifecycleAuditor,
    r: Stage7Results,
) -> None:
    investigated = [s for s in ref.state.snapshots if s.insights > 0]
    if not investigated:
        return
    target = investigated[len(investigated) // 2].header
    evaluator = failing_on(target.watermark, cached)
    engine = LifecycleEngine(case.match.info, evaluator)
    unavailable_at_failure = False
    for event in case.match.events:
        engine.ingest(event)
        last = engine.state.last_snapshot
        if last is not None and last.header.snapshot_id == target.snapshot_id:
            feed = lifecycle_feed(engine.state, engine.status())
            unavailable_at_failure = (
                feed.status is CurrentStatus.UNAVAILABLE
                and not feed.current
                and feed.unavailable_reason is not None
            )
    record = next(s for s in engine.state.snapshots if s.header.snapshot_id == target.snapshot_id)
    anchored = any(
        rev.snapshot_id == target.snapshot_id
        for s in engine.state.storylines
        for rev in s.revisions
    )
    r.check(
        "16 unavailable: failed snapshot recorded FAILED", record.outcome is SnapshotOutcome.FAILED
    )
    r.check(
        "16 unavailable: feed UNAVAILABLE, no fallback to earlier revisions", unavailable_at_failure
    )
    r.check("16 unavailable: no revision anchored on the failed snapshot", not anchored)
    again = replay(case.match.info, case.match.events, failing_on(target.watermark, cached))
    r.check("16 unavailable: deterministic", canonical(again.state) == canonical(engine.state))
    auditor = LifecycleAuditor(
        case.match.info, case.match.events, evaluator, workspaces=shared.workspaces
    )
    r.check("16 unavailable: lifecycle audit clean", not auditor.audit(engine.state))


def _integrity(
    case: Case,
    ref: LifecycleEngine,
    cached: CachedEvaluator,
    shared: LifecycleAuditor,
    r: Stage7Results,
) -> tuple[LifecycleState, LifecycleAuditor] | None:
    last = ref.state.last_snapshot
    if last is None:
        return None
    evaluator = tampered_on(last.header.watermark, cached)
    state = replay(case.match.info, case.match.events, evaluator).state
    auditor = LifecycleAuditor(
        case.match.info, case.match.events, evaluator, workspaces=shared.workspaces
    )
    r.check("17 integrity: tampered run audits clean", not auditor.audit(state))
    feed = lifecycle_feed(state, ref.status())
    for rev in feed.current:
        if rev.evidence_integrity is not EvidenceIntegrity.COMPROMISED:
            continue
        r.compromised_current += 1
        noticed = any(
            n.storyline_id == rev.storyline_id
            and (
                ChangeKind.INTEGRITY_CHANGED in n.change_kinds
                or ChangeKind.CREATED in n.change_kinds
            )
            for n in feed.notices
        )
        r.integrity_noticed.add(noticed and rev.snapshot_id == last.header.snapshot_id)
    _views(state, auditor, r)
    return state, auditor


def _inject(
    audited: Sequence[tuple[LifecycleState, LifecycleAuditor]],
    events: Sequence[MatchEvent],
    r: Stage7Results,
) -> None:
    """Each fault once per case, on the first genuine state it applies to (each state's own
    auditor has already found that state clean)."""
    for name, (expected, fault) in LIFECYCLE_FAULTS.items():
        for state, auditor in audited:
            forged = fault(state, events)
            if forged is None or forged == state:
                continue
            findings = auditor.audit(forged)
            row = r.faults[name]
            row.injected += 1
            row.caught += int(bool(findings))
            row.caught_by_expected += int(caught(findings, expected))
            break


def _leaks(case: Case, states: Sequence[LifecycleState]) -> bool:
    tokens = (case.spec.scenario_id, *TRUTH_VOCABULARY)
    return any(t.encode() in canonical(s) for s in states for t in tokens)


def evaluate_case(case: Case, r: Stage7Results, fresh_replay: bool) -> None:
    cached = CachedEvaluator()
    info, events = case.match.info, case.match.events
    started = time.perf_counter()
    ref, append_only = _incremental(case, events, cached)
    r.replay_seconds.append(time.perf_counter() - started)
    state = ref.state
    r.matches += 1
    r.snapshots += len(state.snapshots)
    r.storylines += len(state.storylines)
    r.revisions += sum(len(s.revisions) for s in state.storylines)
    r.outcomes.update(s.outcome.value for s in state.snapshots)
    for s in state.storylines:
        for rev in s.revisions:
            r.change_kinds.update(k.value for k in rev.change_kinds)
            if rev.withdrawal_reason is not None:
                r.withdrawals[rev.withdrawal_reason.value] += 1
            r.revisions_audit_flagged += int(bool(rev.audit_findings))
    r.check("12 append-only history", append_only)

    if fresh_replay:
        r.fresh_replays += 1
        r.check(
            "2 replay: fresh replay byte-identical",
            canonical(replay(info, events).state) == canonical(state),
        )
    r.check(
        "2 replay: repeated replay byte-identical",
        canonical(replay(info, events, cached).state) == canonical(state),
    )

    _transport(case, ref, cached, r)

    auditor = LifecycleAuditor(info, events, cached)
    findings = auditor.audit(state)
    r.audit_findings.update(f.split(":", 1)[0] for f in findings)
    r.check("13 lifecycle audit clean (own snapshots, reproduction)", not findings)
    cited_ok = all(
        set(rev.final.event_ids) <= {e.event_id for e in events[: rev.watermark]}
        for s in state.storylines
        for rev in s.revisions
        if rev.final is not None
    )
    r.check("7 no leakage: every cited event is in its snapshot", cited_ok)
    r.check(
        "9 reanchoring: storyline keeps its ID across re-anchoring",
        not any(f.startswith("identity:") for f in findings),
    )
    r.check(
        "10 opposite: withdrawals and links correspond",
        not any(f.startswith("linkage:") for f in findings),
    )
    reinstated_ok = all(
        rev.storyline_id == s.storyline_id
        for s in state.storylines
        for rev in s.revisions
        if ChangeKind.REINSTATED in rev.change_kinds
    )
    r.check("11 reinstatement keeps the storyline ID", reinstated_ok)
    batch = investigate_match(case.match)
    r.check(
        "full time: current revisions equal the batch Stage 4 investigation",
        {rev.insight_fingerprint for rev in current_revisions(state)}
        == {fingerprint(rec.final) for rec in batch.records},
    )

    _truth(case, state, r)
    _views(state, auditor, r)
    _failure(case, ref, cached, auditor, r)
    audited = [(state, auditor)]
    tampered = _integrity(case, ref, cached, auditor, r)
    if tampered is not None:
        audited.append(tampered)
    r.check(
        "19 security: no planted-truth vocabulary in canonical state",
        not _leaks(case, [s for s, _ in audited]),
    )
    _inject(audited, events, r)


def evaluate_stage7(
    cases: Sequence[Case], split: str, seeds: int, fresh_replays: int = 1
) -> Stage7Results:
    """Every case; a fresh (uncached) determinism replay on the first `fresh_replays`."""
    results = Stage7Results(split=split, seeds=seeds)
    for index, case in enumerate(cases):
        evaluate_case(case, results, fresh_replay=index < fresh_replays)
    return results


# --- report --------------------------------------------------------------------------------------


def _median(values: list[float]) -> str:
    return f"{statistics.median(values):.0f}" if values else "n/a"


def format_stage7(r: Stage7Results) -> str:
    lines = [
        f"Stage 7 lifecycle - {r.split} split, {r.seeds} seeds per scenario, {r.matches} matches "
        f"(snapshot-anchored; one snapshot per closed minute; deterministic recomputation)",
        f"  snapshots {r.snapshots} {dict(sorted(r.outcomes.items()))}; storylines "
        f"{r.storylines}; revisions {r.revisions}; revisions with audit findings "
        f"{r.revisions_audit_flagged}",
        f"  change kinds: {dict(sorted(r.change_kinds.items()))}",
        f"  withdrawals: {dict(sorted(r.withdrawals.items()))}",
        f"  reference replay seconds per match: median {_median(r.replay_seconds)}; fresh "
        f"determinism replays: {r.fresh_replays}",
        "",
        "properties (passed / checked):",
    ]
    lines += [f"  {name:<74} {rate}" for name, rate in sorted(r.properties.items(), key=_order)]
    lines += [
        f"  lifecycle audit findings on reference states: {dict(r.audit_findings) or 'none'}",
        "",
        "storylines against planted truth (planted variants; metric mechanisms only):",
        f"  expected insights {r.expected_insights}; detected {r.detected}; storylines per "
        f"detected insight {dict(sorted(Counter(r.fragmentation).items()))}",
        f"  expected insights per storyline {dict(sorted(r.purity.items()))}",
        f"  first-detection latency after the window start (s): median "
        f"{_median(r.latency_s)}; max {max(r.latency_s, default=0):.0f}",
        "",
        f"views: stale views flagged {r.stale_views}; current views accepted {r.current_views}",
        f"  Stage 6 feeds: {r.stage6_views} views, {r.stage6_withheld} withheld; compromised "
        f"views warned {r.stage6_compromised_warned}",
        f"integrity: compromised current revisions {r.compromised_current}; noticed on the "
        f"tampered snapshot {r.integrity_noticed}",
    ]
    injected = sum(f.injected for f in r.faults.values())
    missed = sum(f.injected - f.caught for f in r.faults.values())
    lines += [
        "",
        f"lifecycle red team: {injected - missed} of {injected} caught "
        "(injected / caught / caught by the expected check)",
    ]
    for name in LIFECYCLE_FAULTS:
        f = r.faults.get(name, FaultRow())
        lines.append(f"  {name:<30} {f.injected:>4} {f.caught:>4} {f.caught_by_expected:>4}")
    lines.append(f"  audit checks: {', '.join(LIFECYCLE_CHECKS)}")
    return "\n".join(lines)


def _order(item: tuple[str, Rate]) -> tuple[int, str]:
    head = item[0].split(" ", 1)[0]
    return (int(head) if head.isdigit() else 99, item[0])
