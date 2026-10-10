"""Stage 8 evaluation: the broadcast cue contract and the live presentation surface (ADR-0014).

Each case is replayed through `LifecycleEngine` (the reference). Its snapshot evaluations are
folded snapshot by snapshot (`stage7_attribution.fold`) to obtain the lifecycle state after every
snapshot independently of the compiler; cue timelines are compiled for six surfaces (fan and
broadcaster, each with no favourite club or either club) and checked against those states, the
raw observable events and the Stage 6 views. Display state is re-derived from the cue contract
alone (show from, expire, supersede). Measures, by property:

 1 traceability    - every insight or revision cue names a revision current on its snapshot
                     (per the folded state), and its sections are that snapshot's Stage 6 view
                     verbatim (plus the lifecycle notice when it interrupts); every moment cue is
                     an observable event, shown at the first snapshot containing it, with Stage 3's
                     fact text; every retraction and status cue uses the fixed templates.
 2 retraction      - every card whose revision stops being current is superseded (revised or
                     retracted) on that very snapshot, for the right reason.
 3 stale minutes   - snapshot-minutes on which a card on screen is not current, or the status on
                     screen disagrees with the lifecycle status.
 4 moments         - goals, dismissals, substitutions and period starts and ends, found directly
                     in the events, each shown exactly once, within one snapshot.
 5 first output    - match minute of the first cue, first moment and first insight, against the
                     first current lifecycle revision (the Stage 7 baseline).
 6 tautology       - lifecycle notices naming an unchanged value ("X -> X"), on every revision,
                     and the count the pre-fix template would have produced on the same revisions.
 7 overlay load    - cues on screen per snapshot, per audience; interrupting cues per snapshot.
 8 offline == live - the server's shared replay publishes exactly the offline timelines, and the
                     HTTP stream and timeline endpoint deliver them byte for byte (first N cases).
 9 shuffled        - a fully shuffled delivery compiles to the canonical timeline; compiled live
                     after every event, it yields the same cues except data-incomplete status.
10 security        - no scenario ID, planted-truth or generator vocabulary in any timeline or
                     HTTP response.

Also: selection (card limit, no idle card while a primary insight waits, silent re-anchor-only
and evidence-only revisions), determinism, and a pipeline failure on a snapshot with cards on
screen (every card retracted, status unavailable).

No targets are set: results are reported as measured.
"""

import hashlib
import http.client
import json
import re
import statistics
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.moments import key_event_evidence
from matcheyes.api.live import LiveMatch
from matcheyes.api.server import BroadcastApp, BroadcastServer
from matcheyes.broadcast.compiler import CueCompiler, WorkspaceCache, compile_timeline
from matcheyes.broadcast.contracts import (
    MAX_ON_SCREEN,
    RETRACTION_TEXT,
    STATUS_TEXT,
    BroadcastStatus,
    Cue,
    CueKind,
    CueTimeline,
    InsightSource,
    MomentKind,
    MomentSource,
    RetractionReason,
    RetractionSource,
    StatusSource,
)
from matcheyes.broadcast.facts import PERIOD_TEXT, scoreline
from matcheyes.domain.events import (
    Card,
    CardType,
    MatchEvent,
    OwnGoal,
    PeriodEnd,
    PeriodStart,
    Shot,
    ShotOutcome,
    Substitution,
)
from matcheyes.domain.match import ObservableMatch
from matcheyes.domain.time import MatchInstant
from matcheyes.ingestion.log import DataStatus
from matcheyes.lifecycle.contracts import (
    MATERIAL,
    ChangeKind,
    LifecycleState,
    Revision,
    SnapshotOutcome,
    StorylineState,
    WithdrawalReason,
)
from matcheyes.lifecycle.engine import LifecycleEngine, replay
from matcheyes.lifecycle.evaluate import Evaluator, evaluate_snapshot
from matcheyes.lifecycle.feed import audience_feed, current_revisions, notice_text
from matcheyes.personalization.contracts import (
    Audience,
    AudienceFeed,
    PersonalizationProfile,
)
from matcheyes.personalization.policy import Placement, placement
from matcheyes_eval import transport
from matcheyes_eval.stage2 import Case, Rate
from matcheyes_eval.stage7 import TRUTH_VOCABULARY, CachedEvaluator, failing_on
from matcheyes_eval.stage7_attribution import fold

TAUTOLOGY = re.compile(r"(?<![\w-])([\w'+-]+) -> \1(?![\w-])")
SILENT_KINDS = frozenset({ChangeKind.REANCHORED, ChangeKind.EVIDENCE_CHANGED})
GENERATOR_VOCABULARY = (
    "intervention",
    "generator_version",
    "supporting_mechanisms",
    "ground truth",
)
CARD_KINDS = (CueKind.INSIGHT, CueKind.REVISION)
HEADLINES = {
    MomentKind.GOAL: "Goal",
    MomentKind.RED_CARD: "Red card",
    MomentKind.SUBSTITUTION: "Substitution",
}
CONCURRENT_STREAMS = 16

Key = tuple[Audience, str | None]


def _minutes(at: MatchInstant) -> float:
    return (45.0 if at.period == 2 else 0.0) + at.clock_ms / 60_000


def on_screen(cues: Sequence[Cue], at: MatchInstant) -> list[Cue]:
    """Cues displayed at `at`, by the contract's display semantics alone."""
    shown = [c for c in cues if c.show_from.sort_key <= at.sort_key]
    superseded = {s for c in shown for s in c.supersedes}
    return [
        c
        for c in shown
        if c.cue_id not in superseded
        and (c.expires_at is None or at.sort_key < c.expires_at.sort_key)
    ]


def expected_moments(events: Sequence[MatchEvent]) -> list[str]:
    """Event IDs of goals, dismissals, substitutions and period markers, from the raw events."""
    found = []
    for e in events:
        if (
            (isinstance(e, Shot) and e.outcome is ShotOutcome.GOAL)
            or isinstance(e, OwnGoal | Substitution | PeriodStart | PeriodEnd)
            or (isinstance(e, Card) and e.card in (CardType.RED, CardType.SECOND_YELLOW))
        ):
            found.append(e.event_id)
    return found


def lifecycle_status(state: LifecycleState) -> BroadcastStatus:
    last = state.last_snapshot
    if last is None:
        return BroadcastStatus.AWAITING_SNAPSHOT
    if last.outcome is not SnapshotOutcome.EVALUATED:
        return BroadcastStatus.UNAVAILABLE
    return BroadcastStatus.CURRENT if current_revisions(state) else BroadcastStatus.NO_CANDIDATE


def profiles(case: Case) -> dict[Key, PersonalizationProfile]:
    clubs = (None, case.match.info.home.team_id, case.match.info.away.team_id)
    return {
        (a, c): PersonalizationProfile(audience=a, favourite_club_id=c)
        for a in (Audience.FAN, Audience.BROADCASTER)
        for c in clubs
    }


# --- results -------------------------------------------------------------------------------------


@dataclass
class Stage8Results:
    split: str
    seeds: int
    matches: int = 0
    surfaces: int = 0
    snapshots: int = 0
    cues: Counter[str] = field(default_factory=Counter)
    properties: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))
    stale_card_minutes: int = 0
    stale_status_minutes: int = 0
    moments_expected: int = 0
    moment_delay_s: list[float] = field(default_factory=list)
    first_cue: list[float] = field(default_factory=list)
    first_moment: list[float] = field(default_factory=list)
    first_insight: list[float] = field(default_factory=list)
    first_current: list[float] = field(default_factory=list)
    first_key_moment: list[float] = field(default_factory=list)
    notices: int = 0
    tautological: int = 0
    tautological_before_fix: int = 0
    displayed_notices: int = 0
    displayed_tautological: int = 0
    load: dict[str, Counter[int]] = field(default_factory=lambda: defaultdict(Counter))
    cards: dict[str, Counter[int]] = field(default_factory=lambda: defaultdict(Counter))
    interrupts: dict[str, Counter[int]] = field(default_factory=lambda: defaultdict(Counter))
    retractions: Counter[str] = field(default_factory=Counter)
    revisions_loud: int = 0
    revisions_silent: int = 0
    replay_seconds: list[float] = field(default_factory=list)
    compile_seconds: list[float] = field(default_factory=list)
    live_seconds: list[float] = field(default_factory=list)
    first_byte_ms: list[float] = field(default_factory=list)
    stream_ms: list[float] = field(default_factory=list)
    concurrent_ms: list[float] = field(default_factory=list)
    live_cases: int = 0
    leaks: int = 0

    def check(self, name: str, ok: bool) -> None:
        self.properties[name].add(ok)


# --- reference -----------------------------------------------------------------------------------


@dataclass
class Reference:
    case: Case
    cached: CachedEvaluator
    engine: LifecycleEngine
    events: tuple[MatchEvent, ...]
    states: list[LifecycleState]
    workspaces: WorkspaceCache
    feeds: dict[tuple[str, Key], AudienceFeed] = field(default_factory=dict)

    @property
    def state(self) -> LifecycleState:
        return self.engine.state

    def workspace(self, k: int) -> MatchWorkspace:
        header = self.state.snapshots[k].header
        if header.snapshot_id not in self.workspaces:
            prefix = self.events[: header.watermark]
            self.workspaces[header.snapshot_id] = MatchWorkspace.build(
                ObservableMatch(info=self.case.match.info, events=prefix)
            )
        return self.workspaces[header.snapshot_id]

    def feed(self, k: int, key: Key, profile: PersonalizationProfile) -> AudienceFeed:
        sid = self.state.snapshots[k].header.snapshot_id
        if (sid, key) not in self.feeds:
            self.feeds[sid, key] = audience_feed(self.states[k], profile, self.workspace(k))
        return self.feeds[sid, key]


def build_reference(case: Case, r: Stage8Results, base: Evaluator = evaluate_snapshot) -> Reference:
    cached = CachedEvaluator(base)
    started = time.perf_counter()
    engine = replay(case.match.info, case.match.events, cached)
    r.replay_seconds.append(time.perf_counter() - started)
    evaluations = [cached.cache[s.header.snapshot_id] for s in engine.state.snapshots]
    states = fold(case.match.info.match_id, evaluations).states
    r.check(
        "0 reference: snapshot fold reproduces the engine state",
        bool(states) and states[-1].model_dump_json() == engine.state.model_dump_json(),
    )
    return Reference(case, cached, engine, engine.log.events(), states, {})


# --- measures ------------------------------------------------------------------------------------


def _revision(state: LifecycleState, source: InsightSource) -> Revision | None:
    s = next((x for x in state.storylines if x.storyline_id == source.storyline_id), None)
    if s is None or source.revision > len(s.revisions):
        return None
    return s.revisions[source.revision - 1]


def _is_current(state: LifecycleState, source: InsightSource) -> bool:
    return any(
        (r.storyline_id, r.number, r.insight_fingerprint)
        == (source.storyline_id, source.revision, source.insight_fingerprint)
        for r in current_revisions(state)
    )


def _card_traced(
    ref: Reference, cue: Cue, k: int, key: Key, profile: PersonalizationProfile
) -> bool:
    source = cue.source
    if not isinstance(source, InsightSource) or not _is_current(ref.states[k], source):
        return False
    rev = _revision(ref.states[k], source)
    view = next(
        (
            v
            for v in ref.feed(k, key, profile).primary
            if v.source_fingerprint == source.insight_fingerprint
        ),
        None,
    )
    if rev is None or view is None or rev.final is None:
        return False
    sections = [(s.kind.value, s.text, s.mandatory) for s in view.sections]
    if cue.interrupt and cue.kind is CueKind.REVISION:
        sections.insert(0, ("notice", notice_text(ref.states[k], rev), True))
    return (
        [(s.kind, s.text, s.mandatory) for s in cue.sections] == sections
        and source.view_fingerprint == hashlib.sha256(view.model_dump_json().encode()).hexdigest()
        and source.verdict is rev.final.verdict
        and source.evidence_integrity is rev.final.evidence_integrity
        and source.change_kinds == rev.change_kinds
        and cue.audience is profile.audience
    )


def _moment_traced(ref: Reference, cue: Cue, k: int, facts: dict[str, str]) -> bool:
    source = cue.source
    if not isinstance(source, MomentSource) or len(source.event_ids) != 1:
        return False
    index = {e.event_id: i for i, e in enumerate(ref.events)}
    i = index.get(source.event_ids[0])
    if i is None:
        return False
    first = next(n for n, s in enumerate(ref.state.snapshots) if i < s.header.watermark)
    event = ref.events[i]
    if source.moment in (MomentKind.PERIOD_START, MomentKind.PERIOD_END):
        headline, text = PERIOD_TEXT[(source.moment, event.period)]
    else:
        headline, text = HEADLINES[source.moment], facts.get(event.event_id, "")
    expected = [headline, text]
    if source.moment is MomentKind.GOAL:
        expected.append(scoreline(ref.case.match.info, source.score))
    return (
        first == k
        and source.at == event.instant
        and [s.text for s in cue.sections] == expected
        and cue.show_from == ref.state.snapshots[k].header.as_of
    )


def _templated(cue: Cue, by_id: dict[str, Cue]) -> bool:
    source = cue.source
    if isinstance(source, RetractionSource):
        retracted = by_id.get(source.retracts)
        if retracted is None or retracted.kind not in CARD_KINDS:
            return False
        fact = [s.text for s in retracted.sections if s.kind == "fact"][:1]
        return [s.text for s in cue.sections] == [RETRACTION_TEXT[source.reason], *fact]
    if isinstance(source, StatusSource):
        detail = [source.detail] if source.detail else []
        return [s.text for s in cue.sections] == [STATUS_TEXT[source.status], *detail]
    return False


def _expected_reason(ref: Reference, k: int, source: InsightSource) -> set[RetractionReason]:
    record = ref.state.snapshots[k]
    if record.outcome is not SnapshotOutcome.EVALUATED:
        return {RetractionReason.UNAVAILABLE}
    s = ref.states[k].storyline(source.storyline_id)
    if s.state is StorylineState.WITHDRAWN:
        if s.current.withdrawal_reason is WithdrawalReason.OPPOSITE_DIRECTION:
            return {RetractionReason.REPLACED}
        return {RetractionReason.WITHDRAWN}
    return {RetractionReason.NO_VERIFIED_EXPLANATION, RetractionReason.WITHHELD}


def evaluate_surface(
    ref: Reference, key: Key, profile: PersonalizationProfile, tl: CueTimeline, r: Stage8Results
) -> None:
    audience = profile.audience.value
    cues = tl.cues
    by_id = {c.cue_id: c for c in cues}
    snapshots = ref.state.snapshots
    k_of = {s.header.snapshot_id: k for k, s in enumerate(snapshots)}
    facts = {
        f.event_ids[0]: f.statement for f in key_event_evidence(ref.events, ref.case.match.info)
    }
    r.surfaces += 1
    r.cues.update(c.kind.value for c in cues)
    r.check(
        "1 traceability: timeline re-validates from JSON (IDs, order, links)",
        CueTimeline.model_validate_json(tl.model_dump_json()) == tl,
    )
    for cue in cues:
        k = k_of.get(cue.snapshot_id or "")
        if cue.kind in CARD_KINDS:
            ok = k is not None and _card_traced(ref, cue, k, key, profile)
            r.check("1 traceability: insight/revision cues", ok)
        elif cue.kind is CueKind.MOMENT:
            r.check(
                "1 traceability: moment cues", k is not None and _moment_traced(ref, cue, k, facts)
            )
        else:
            awaiting = cue.snapshot_id is None and isinstance(cue.source, StatusSource)
            r.check(
                "1 traceability: retraction/status cues",
                (k is not None or awaiting) and _templated(cue, by_id),
            )
        if cue.kind is CueKind.REVISION and isinstance(cue.source, InsightSource):
            silent = set(cue.source.change_kinds) <= SILENT_KINDS
            r.check(
                "selection: re-anchor/evidence-only revisions are silent", cue.interrupt != silent
            )
            if cue.interrupt:
                r.revisions_loud += 1
                r.displayed_notices += 1
                r.displayed_tautological += bool(TAUTOLOGY.search(cue.sections[0].text))
            else:
                r.revisions_silent += 1
        if isinstance(cue.source, RetractionSource):
            r.retractions[cue.source.reason.value] += 1

    previous: list[Cue] = []
    for k, record in enumerate(snapshots):
        at = record.header.as_of
        state = ref.states[k]
        screen = on_screen(cues, at)
        cards = [c for c in screen if c.kind in CARD_KINDS]
        stale = [
            c
            for c in cards
            if not (isinstance(c.source, InsightSource) and _is_current(state, c.source))
        ]
        r.stale_card_minutes += len(stale)
        status = [c.source for c in screen if isinstance(c.source, StatusSource)]
        if len(status) != 1 or status[0].status is not lifecycle_status(state):
            r.stale_status_minutes += 1
        r.check("selection: at most MAX_ON_SCREEN cards", len(cards) <= MAX_ON_SCREEN)
        r.load[audience][len([c for c in screen if c.kind is not CueKind.STATUS])] += 1
        r.cards[audience][len(cards)] += 1
        r.interrupts[audience][sum(c.interrupt for c in cues if c.show_from == at)] += 1

        here = [c for c in cues if c.snapshot_id == record.header.snapshot_id]
        superseding = {t: c for c in here for t in c.supersedes}
        for card in previous:
            source = card.source
            if not isinstance(source, InsightSource) or _is_current(state, source):
                continue
            ended = superseding.get(card.cue_id)
            r.check(
                "2 retraction: a card that stops being current is superseded on that snapshot",
                ended is not None and ended.kind in (CueKind.RETRACTION, CueKind.REVISION),
            )
            if ended is not None and isinstance(ended.source, RetractionSource):
                r.check(
                    "2 retraction: for the right reason",
                    ended.source.reason in _expected_reason(ref, k, source),
                )
        if record.outcome is SnapshotOutcome.EVALUATED and len(cards) < MAX_ON_SCREEN:
            shown = {c.source.storyline_id for c in cards if isinstance(c.source, InsightSource)}
            waiting = [
                rev
                for rev in current_revisions(state)
                if rev.storyline_id not in shown
                and rev.final is not None
                and placement(rev.final, profile.audience) is Placement.PRIMARY
            ]
            if waiting:
                primary = {v.source_fingerprint for v in ref.feed(k, key, profile).primary}
                idle = any(rev.insight_fingerprint in primary for rev in waiting)
                r.check("selection: no idle card while a primary insight waits", not idle)
        previous = cards


def _notices(ref: Reference, r: Stage8Results) -> None:
    state = ref.state
    for s in state.storylines:
        for rev in s.revisions:
            if not MATERIAL & set(rev.change_kinds):
                continue
            r.notices += 1
            r.tautological += bool(TAUTOLOGY.search(notice_text(state, rev)))
            if ChangeKind.EXPLANATION_CHANGED in rev.change_kinds and rev.final is not None:
                old = next(
                    p.final for p in reversed(s.revisions[: rev.number - 1]) if p.final is not None
                )
                r.tautological_before_fix += old.leading == rev.final.leading


def _moments(ref: Reference, tl: CueTimeline, r: Stage8Results) -> None:
    expected = expected_moments(ref.events[: ref.state.snapshots[-1].header.watermark])
    r.moments_expected += len(expected)
    shown = Counter(
        eid for c in tl.cues if isinstance(c.source, MomentSource) for eid in c.source.event_ids
    )
    index = {e.event_id: i for i, e in enumerate(ref.events)}
    by_event = {
        eid: c for c in tl.cues if isinstance(c.source, MomentSource) for eid in c.source.event_ids
    }
    for eid in expected:
        cue = by_event.get(eid)
        first = next((s for s in ref.state.snapshots if index[eid] < s.header.watermark), None)
        within = cue is not None and first is not None and cue.show_from == first.header.as_of
        r.check("4 moments: shown within one snapshot", within)
        if cue is not None:
            event = ref.events[index[eid]]
            r.moment_delay_s.append((_minutes(cue.show_from) - _minutes(event.instant)) * 60)
    r.check(
        "4 moments: no duplicate or unexpected moment",
        set(shown) == set(expected) and all(n == 1 for n in shown.values()),
    )


def _first_output(ref: Reference, tl: CueTimeline, r: Stage8Results) -> None:
    content = [c for c in tl.cues if c.kind is not CueKind.STATUS]
    moments = [c for c in tl.cues if c.kind is CueKind.MOMENT]
    key = [
        c
        for c in moments
        if isinstance(c.source, MomentSource)
        and c.source.moment in (MomentKind.GOAL, MomentKind.RED_CARD)
    ]
    insights = [c for c in tl.cues if c.kind is CueKind.INSIGHT]
    for cues, sink in (
        (content, r.first_cue),
        (moments, r.first_moment),
        (key, r.first_key_moment),
        (insights, r.first_insight),
    ):
        if cues:
            sink.append(_minutes(cues[0].show_from))
    current = next(
        (
            ref.state.snapshots[k].header.as_of
            for k, s in enumerate(ref.states)
            if current_revisions(s)
        ),
        None,
    )
    if current is not None:
        r.first_current.append(_minutes(current))


def _shuffled(ref: Reference, canonical: CueTimeline, r: Stage8Results) -> None:
    info, profile = ref.case.match.info, canonical.profile
    delivery = transport.shuffled(ref.case.match.events, ref.case.seed)
    engine = replay(info, delivery, ref.cached)
    tl = compile_timeline(
        info, engine.log.events(), engine.state, profile, workspaces=ref.workspaces
    )
    r.check(
        "9 shuffled: final timeline equals the canonical one",
        tl.timeline_id == canonical.timeline_id,
    )
    live = LifecycleEngine(info, ref.cached)
    compiler = CueCompiler(info, profile, ref.workspaces)
    for event in delivery:
        live.ingest(event)
        compiler.advance(live.state, live.log.events(), live.status().data_status)
    content = [c.cue_id for c in compiler.cues if c.kind is not CueKind.STATUS]
    r.check(
        "9 shuffled: live compilation gives the same non-status cues",
        content == [c.cue_id for c in canonical.cues if c.kind is not CueKind.STATUS],
    )
    final = [c.source for c in compiler.cues if isinstance(c.source, StatusSource)][-1]
    expected = [c.source for c in canonical.cues if isinstance(c.source, StatusSource)][-1]
    r.check(
        "9 shuffled: live compilation ends on the canonical status",
        final.status is expected.status and live.status().data_status is DataStatus.CONTIGUOUS,
    )


def _unavailable(ref: Reference, canonical: CueTimeline, r: Stage8Results) -> None:
    snapshots = ref.state.snapshots
    target = next(
        (
            k
            for k in range(1, len(snapshots))
            if any(
                c.kind in CARD_KINDS
                for c in on_screen(canonical.cues, snapshots[k - 1].header.as_of)
            )
        ),
        None,
    )
    if target is None:
        return
    header = snapshots[target].header
    before = {
        c.cue_id
        for c in on_screen(canonical.cues, snapshots[target - 1].header.as_of)
        if c.kind in CARD_KINDS
    }
    info = ref.case.match.info
    engine = replay(info, ref.case.match.events, failing_on(header.watermark, ref.cached))
    tl = compile_timeline(info, engine.log.events(), engine.state, canonical.profile)
    here = [c for c in tl.cues if c.snapshot_id == header.snapshot_id]
    retracted = {
        c.source.retracts
        for c in here
        if isinstance(c.source, RetractionSource)
        and c.source.reason is RetractionReason.UNAVAILABLE
    }
    screen = on_screen(tl.cues, header.as_of)
    status = [c.source for c in screen if isinstance(c.source, StatusSource)]
    r.check(
        "unavailable: every card retracted and status unavailable on the failed snapshot",
        engine.state.snapshots[target].outcome is SnapshotOutcome.FAILED
        and retracted == before
        and not any(c.kind in CARD_KINDS for c in screen)
        and [s.status for s in status] == [BroadcastStatus.UNAVAILABLE],
    )


def _leaked(case: Case, texts: Sequence[str]) -> int:
    tokens = (case.spec.scenario_id, *TRUTH_VOCABULARY, *GENERATOR_VOCABULARY)
    return sum(1 for text in texts for t in tokens if t.lower() in text.lower())


def _get(address: tuple[str, int], path: str) -> tuple[float, float, bytes]:
    connection = http.client.HTTPConnection(*address, timeout=300)
    started = time.perf_counter()
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        first = response.read(1)
        first_byte = time.perf_counter() - started
        body = first + response.read()
        return first_byte, time.perf_counter() - started, body
    finally:
        connection.close()


def _quiet(*_: object) -> None:
    """The evaluation's own server does not log requests."""


def _sse_cues(body: bytes) -> list[str]:
    cues = []
    for block in body.decode("utf-8").split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if lines.get("event") == "cue":
            cues.append(lines["data"])
    return cues


def _live(ref: Reference, offline: dict[Key, CueTimeline], r: Stage8Results) -> list[str]:
    """The server's replay and HTTP delivery against the offline timelines; returns bodies."""
    case = ref.case
    mid = case.match.info.match_id
    live = LiveMatch(case.match, speed=0, loop=False, evaluator=ref.cached)
    started = time.perf_counter()
    live.start()
    with live.condition:
        live.condition.wait_for(lambda: live.edition.done, timeout=3600)
    r.live_seconds.append(time.perf_counter() - started)
    live.stop()
    for key, tl in offline.items():
        r.check(
            "8 offline == live: server replay publishes the offline timeline",
            [c.cue_id for c in live.timeline(key)[2].cues] == [c.cue_id for c in tl.cues],
        )
    app = BroadcastApp({mid: case.match}, speed=0, loop=False, evaluator=ref.cached)
    server = BroadcastServer(("127.0.0.1", 0), app)
    server.RequestHandlerClass.log_message = _quiet  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bodies: list[str] = []
    try:
        address = (str(server.server_address[0]), int(server.server_address[1]))
        served = app.live[mid]
        served.start()
        with served.condition:
            served.condition.wait_for(lambda: served.edition.done, timeout=3600)
        for (audience, club), tl in offline.items():
            query = f"audience={audience.value}" + (f"&club={club}" if club else "")
            first_byte, total, body = _get(address, f"/matches/{mid}/stream?{query}")
            r.first_byte_ms.append(first_byte * 1000)
            r.stream_ms.append(total * 1000)
            r.check(
                "8 offline == live: SSE stream delivers the offline cues byte for byte",
                _sse_cues(body) == [c.model_dump_json() for c in tl.cues],
            )
            _, _, timeline = _get(address, f"/matches/{mid}/timeline?{query}")
            payload = json.loads(timeline)
            r.check(
                "8 offline == live: timeline endpoint equals the offline timeline",
                payload["complete"] is True
                and CueTimeline.model_validate(payload["timeline"]) == tl,
            )
            bodies += [body.decode("utf-8"), timeline.decode("utf-8")]
        bodies.append(_get(address, "/matches")[2].decode("utf-8"))
        fan = offline[(Audience.FAN, None)]
        results: list[list[str]] = []
        lock = threading.Lock()

        def client() -> None:
            got = _sse_cues(_get(address, f"/matches/{mid}/stream?audience=fan")[2])
            with lock:
                results.append(got)

        started = time.perf_counter()
        clients = [threading.Thread(target=client) for _ in range(CONCURRENT_STREAMS)]
        for c in clients:
            c.start()
        for c in clients:
            c.join(timeout=600)
        r.concurrent_ms.append((time.perf_counter() - started) * 1000)
        expected = [c.model_dump_json() for c in fan.cues]
        r.check(
            f"8 offline == live: {CONCURRENT_STREAMS} concurrent streams receive identical cues",
            len(results) == CONCURRENT_STREAMS and all(got == expected for got in results),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=30)
    r.live_cases += 1
    return bodies


def evaluate_case(
    case: Case, r: Stage8Results, live: bool, base: Evaluator = evaluate_snapshot
) -> None:
    """`base` evaluates snapshots: the reference pipeline, or (Stage 9) a recorded model."""
    ref = build_reference(case, r, base)
    r.matches += 1
    r.snapshots += len(ref.state.snapshots)
    offline: dict[Key, CueTimeline] = {}
    for key, profile in profiles(case).items():
        started = time.perf_counter()
        offline[key] = compile_timeline(
            case.match.info, ref.events, ref.state, profile, workspaces=ref.workspaces
        )
        r.compile_seconds.append(time.perf_counter() - started)
        evaluate_surface(ref, key, profile, offline[key], r)
    fan = offline[(Audience.FAN, None)]
    again = compile_timeline(case.match.info, ref.events, ref.state, fan.profile)
    r.check("determinism: recompiling gives the same timeline", again == fan)
    _notices(ref, r)
    _moments(ref, fan, r)
    _first_output(ref, fan, r)
    _shuffled(ref, fan, r)
    _unavailable(ref, fan, r)
    texts = [tl.model_dump_json() for tl in offline.values()]
    if live:
        texts += _live(ref, offline, r)
    leaks = _leaked(case, texts)
    r.leaks += leaks
    r.check("10 security: no scenario, planted-truth or generator vocabulary", leaks == 0)


def evaluate_stage8(
    cases: Sequence[Case], split: str, seeds: int, live_matches: int = 1
) -> Stage8Results:
    """Every case; the live server and HTTP checks on the first `live_matches`."""
    results = Stage8Results(split=split, seeds=seeds)
    for index, case in enumerate(cases):
        evaluate_case(case, results, live=index < live_matches)
    return results


# --- report --------------------------------------------------------------------------------------


def _stats(values: list[float], unit: str = "") -> str:
    if not values:
        return "n/a"
    return (
        f"median {statistics.median(values):.1f}{unit}; min {min(values):.1f}{unit}; "
        f"max {max(values):.1f}{unit}"
    )


def _order(item: tuple[str, Rate]) -> tuple[int, str]:
    head = item[0].split(" ", 1)[0]
    return (int(head) if head.isdigit() else 99, item[0])


def format_stage8(r: Stage8Results) -> str:
    lines = [
        f"Stage 8 broadcast cues - {r.split} split, {r.seeds} seed(s) per scenario, {r.matches} "
        f"matches, {r.surfaces} surfaces, {r.snapshots} snapshots (deterministic replay)",
        f"  cues: {dict(sorted(r.cues.items()))}",
        f"  revisions shown: {r.revisions_loud} interrupting, {r.revisions_silent} silent; "
        f"retractions by reason: {dict(sorted(r.retractions.items()))}",
        "",
        "properties (passed / checked):",
    ]
    lines += [f"  {name:<78} {rate}" for name, rate in sorted(r.properties.items(), key=_order)]
    lines += [
        "",
        f"3 stale cue-minutes: cards {r.stale_card_minutes}; status {r.stale_status_minutes}",
        f"4 moments expected {r.moments_expected}; delay event -> cue (s): "
        f"{_stats(r.moment_delay_s)}",
        "5 first output (match minute, fan surface): "
        f"first cue {_stats(r.first_cue)}; first moment {_stats(r.first_moment)}",
        f"  first goal or red card {_stats(r.first_key_moment)}; first insight card "
        f"{_stats(r.first_insight)}",
        f"  baseline - first current lifecycle revision {_stats(r.first_current)}",
        f"6 notices on material revisions {r.notices}; naming an unchanged value: "
        f"{r.tautological} (pre-fix template on the same revisions: {r.tautological_before_fix})",
        f"  interrupting revision notices on screen {r.displayed_notices}; tautological "
        f"{r.displayed_tautological}",
        "7 overlay load per snapshot (cues on screen excluding status: snapshots):",
    ]
    for audience in sorted(r.load):
        load, cards = r.load[audience], r.cards[audience]
        lines.append(
            f"  {audience:<12} max {max(load)}; {dict(sorted(load.items()))}; cards max "
            f"{max(cards)} {dict(sorted(cards.items()))}; interrupting cues per snapshot "
            f"{dict(sorted(r.interrupts[audience].items()))}"
        )
    lines += [
        "",
        f"10 security: leaked tokens {r.leaks}",
        f"timing: reference replay s {_stats(r.replay_seconds)}; compile per surface s "
        f"{_stats(r.compile_seconds)}",
        f"  live server replay (6 surfaces, speed 0) s {_stats(r.live_seconds)} over "
        f"{r.live_cases} case(s); SSE first byte ms {_stats(r.first_byte_ms)}; full stream ms "
        f"{_stats(r.stream_ms)}; {CONCURRENT_STREAMS} concurrent streams ms "
        f"{_stats(r.concurrent_ms)}",
    ]
    return "\n".join(lines)
