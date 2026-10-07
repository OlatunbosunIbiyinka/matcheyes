"""The broadcast cue compiler: lifecycle record + observable moments -> one surface's cues.

For each canonical snapshot, in schedule order:

1. moments - every goal, dismissal, substitution and period start or end among the events the
   snapshot adds becomes one factual moment cue, shown when the snapshot closes (there is no
   second, event-time truth path). An invalid prefix's events are not presented.
2. insights - a displayed card whose storyline is still current on the snapshot stays; one
   whose revision changed is revised (or retracted when the new revision is off the primary
   surface or withheld); one whose storyline was withdrawn is retracted; on a failed or invalid
   snapshot every card is retracted. Free cards are then filled from the Stage 6 primary feed.
3. status - a status cue whenever the status changes.

The compiler presents; it decides no truth. Current revisions come from the lifecycle record
(`history.current_at`), card text is the Stage 6 view verbatim, notices are the lifecycle's
templated notices. The events must be the log the lifecycle was built from: the compiler
recomputes the prefix digest and refuses a mismatch. Output depends only on the lifecycle state
and the events, so compiling a state at once or snapshot by snapshot gives the same cues.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from matcheyes.agents.tools import MatchWorkspace
from matcheyes.broadcast.contracts import (
    BROADCAST_AUDIENCES,
    KICK_OFF,
    MAX_ON_SCREEN,
    MOMENT_DWELL_MS,
    RETRACTION_DWELL_MS,
    RETRACTION_TEXT,
    STATUS_TEXT,
    BroadcastStatus,
    Cue,
    CueKind,
    CueSection,
    CueTimeline,
    InsightSource,
    MomentKind,
    MomentSource,
    RetractionReason,
    RetractionSource,
    Score,
    StatusSource,
    TeamRef,
)
from matcheyes.broadcast.facts import Moment, extract_moments, scoreline
from matcheyes.broadcast.history import current_at, revision_at
from matcheyes.broadcast.selection import (
    INTERRUPTING_MOMENTS,
    MOMENT_PRIORITY,
    PRIORITY,
    SILENT_REVISION_PRIORITY,
    interrupts,
)
from matcheyes.domain.entities import MatchInfo, TeamSheet
from matcheyes.domain.events import MatchEvent
from matcheyes.domain.match import EVENT_ADAPTER, ObservableMatch
from matcheyes.domain.time import MatchInstant
from matcheyes.ingestion.log import EMPTY_PREFIX_DIGEST, DataStatus, chain_digest
from matcheyes.lifecycle.contracts import (
    LifecycleState,
    Revision,
    SnapshotOutcome,
    SnapshotRecord,
    WithdrawalReason,
)
from matcheyes.lifecycle.feed import notice_text
from matcheyes.orchestration.investigation import InvestigationRecord
from matcheyes.personalization.contracts import (
    PersonalizationProfile,
    PersonalizedInsight,
    SectionKind,
)
from matcheyes.personalization.feed import build_feed
from matcheyes.personalization.policy import Placement, placement

WorkspaceCache = dict[str, MatchWorkspace]


def view_fingerprint(view: PersonalizedInsight) -> str:
    return hashlib.sha256(view.model_dump_json().encode("utf-8")).hexdigest()


def view_sections(view: PersonalizedInsight) -> tuple[CueSection, ...]:
    return tuple(
        CueSection(kind=s.kind.value, text=s.text, mandatory=s.mandatory) for s in view.sections
    )


def _later(at: MatchInstant, ms: int) -> MatchInstant:
    return MatchInstant(period=at.period, clock_ms=at.clock_ms + ms)


def _team(sheet: TeamSheet) -> TeamRef:
    return TeamRef(team_id=sheet.team_id, name=sheet.club.name, short_name=sheet.club.short_name)


@dataclass(frozen=True)
class _Feed:
    primary: tuple[PersonalizedInsight, ...]
    withheld: frozenset[str]


class CueCompiler:
    """Compiles one surface (audience and favourite club). Call `advance` with the lifecycle
    state and the log's contiguous prefix whenever they grow; it returns the new cues.

    `workspaces` lets several surfaces of one match share each snapshot's workspace (a pure
    function of the snapshot); the caller then decides when to drop them. Without it, each
    workspace is dropped once its snapshot is compiled."""

    def __init__(
        self,
        info: MatchInfo,
        profile: PersonalizationProfile,
        workspaces: WorkspaceCache | None = None,
    ) -> None:
        if profile.audience not in BROADCAST_AUDIENCES:
            raise ValueError("the broadcast surface serves fans and broadcasters only")
        self.info = info
        self.profile = profile
        self._shared = workspaces is not None
        self.workspaces: WorkspaceCache = {} if workspaces is None else workspaces
        self.cues: list[Cue] = []
        self.snapshots = 0
        self.watermark = 0
        self._digest = EMPTY_PREFIX_DIGEST
        self._score = Score(home=0, away=0)
        self._displayed: dict[str, Cue] = {}
        self._status: Cue | None = None
        self._lifecycle: tuple[BroadcastStatus, str | None, MatchInstant, str | None] = (
            BroadcastStatus.AWAITING_SNAPSHOT,
            None,
            KICK_OFF,
            None,
        )

    @property
    def displayed(self) -> tuple[Cue, ...]:
        return tuple(self._displayed.values())

    def advance(
        self,
        state: LifecycleState,
        events: Sequence[MatchEvent],
        data_status: DataStatus = DataStatus.CONTIGUOUS,
    ) -> tuple[Cue, ...]:
        if state.match_id != self.info.match_id:
            raise ValueError("the lifecycle state belongs to another match")
        start = len(self.cues)
        if self._status is None:
            self._set_status(*self._lifecycle)
        for record in state.snapshots[self.snapshots :]:
            self._snapshot(state, events, record)
            self.snapshots += 1
            if not self._shared:
                self.workspaces.clear()
        status, detail, at, snapshot_id = self._lifecycle
        if data_status is DataStatus.DATA_INCOMPLETE:
            status, detail = BroadcastStatus.DATA_INCOMPLETE, None
        self._set_status(status, detail, at, snapshot_id)
        return tuple(self.cues[start:])

    def timeline(self) -> CueTimeline:
        return CueTimeline.build(
            match_id=self.info.match_id,
            home=_team(self.info.home),
            away=_team(self.info.away),
            profile=self.profile,
            watermark=self.watermark,
            snapshots=self.snapshots,
            cues=tuple(self.cues),
        )

    # --- one snapshot -------------------------------------------------------------------------

    def _snapshot(
        self, state: LifecycleState, events: Sequence[MatchEvent], record: SnapshotRecord
    ) -> None:
        header = record.header
        if len(events) < header.watermark:
            raise ValueError("the events are shorter than the lifecycle's snapshots")
        added = events[self.watermark : header.watermark]
        digest = self._digest
        for event in added:
            digest = chain_digest(digest, EVENT_ADAPTER.dump_json(event))
        if digest != header.log_digest:
            raise ValueError("the events are not the log this lifecycle was built from")
        self._digest, self.watermark = digest, header.watermark
        if record.outcome is not SnapshotOutcome.INVALID:
            for moment in extract_moments(self.info, added, self._score):
                self._moment(moment, record)
                self._score = moment.score
        at, sid = header.as_of, header.snapshot_id
        if record.outcome is not SnapshotOutcome.EVALUATED:
            for cue in self.displayed:
                self._retract(cue, RetractionReason.UNAVAILABLE, None, record)
            detail = f"{record.outcome.value}: {record.failure}" if record.failure else None
            self._lifecycle = (BroadcastStatus.UNAVAILABLE, detail, at, sid)
            self._set_status(*self._lifecycle)
            return
        current = {r.storyline_id: r for r in current_at(state, record)}
        feed: _Feed | None = None
        for cue in self.displayed:
            source = cue.source
            if not isinstance(source, InsightSource):
                raise TypeError("only insight cards are displayed")
            rev = current.get(source.storyline_id)
            if rev is None:
                ended = revision_at(state, source.storyline_id, header.watermark)
                replaced = (
                    ended is not None
                    and ended.withdrawal_reason is WithdrawalReason.OPPOSITE_DIRECTION
                )
                reason = RetractionReason.REPLACED if replaced else RetractionReason.WITHDRAWN
                self._retract(cue, reason, ended.number if ended else None, record)
            elif rev.number != source.revision:
                feed = feed or self._feed(state, events, record, current)
                view = next(
                    (v for v in feed.primary if v.source_fingerprint == rev.insight_fingerprint),
                    None,
                )
                if view is not None:
                    self._revise(cue, rev, view, state, record)
                else:
                    withheld = rev.investigation_id in feed.withheld
                    reason = (
                        RetractionReason.WITHHELD
                        if withheld
                        else RetractionReason.NO_VERIFIED_EXPLANATION
                    )
                    self._retract(cue, reason, rev.number, record)
        waiting = [
            r
            for r in current.values()
            if r.storyline_id not in self._displayed
            and r.final is not None
            and placement(r.final, self.profile.audience) is Placement.PRIMARY
        ]
        if waiting and len(self._displayed) < MAX_ON_SCREEN:
            feed = feed or self._feed(state, events, record, current)
            by_fingerprint = {r.insight_fingerprint: r for r in waiting}
            for view in feed.primary:
                if len(self._displayed) >= MAX_ON_SCREEN:
                    break
                rev = by_fingerprint.get(view.source_fingerprint)
                if rev is not None:
                    self._show(rev, view, record)
        status = BroadcastStatus.CURRENT if current else BroadcastStatus.NO_CANDIDATE
        self._lifecycle = (status, None, at, sid)
        self._set_status(*self._lifecycle)

    def _feed(
        self,
        state: LifecycleState,
        events: Sequence[MatchEvent],
        record: SnapshotRecord,
        current: dict[str, Revision],
    ) -> _Feed:
        """The Stage 6 audience feed of the current revisions, against this snapshot's own
        workspace (exactly `lifecycle.feed.audience_feed` on the state after the snapshot)."""
        sid = record.header.snapshot_id
        if sid not in self.workspaces:
            prefix = tuple(events[: record.header.watermark])
            self.workspaces[sid] = MatchWorkspace.build(
                ObservableMatch(info=self.info, events=prefix)
            )
        records = [
            InvestigationRecord(final=r.final, verification=r.verification, trace=())
            for r in current.values()
            if r.final is not None
        ]
        feed = build_feed(records, self.profile, self.workspaces[sid])
        return _Feed(primary=feed.primary, withheld=frozenset(feed.withheld))

    # --- cue builders -------------------------------------------------------------------------

    def _emit(self, **fields: object) -> Cue:
        cue = Cue.build(match_id=self.info.match_id, **fields)
        self.cues.append(cue)
        return cue

    def _moment(self, moment: Moment, record: SnapshotRecord) -> None:
        at = record.header.as_of
        sections = [
            CueSection(kind="headline", text=moment.headline),
            CueSection(kind="moment", text=moment.statement, mandatory=True),
        ]
        if moment.kind is MomentKind.GOAL:
            sections.append(CueSection(kind="score", text=scoreline(self.info, moment.score)))
        self._emit(
            kind=CueKind.MOMENT,
            audience=None,
            show_from=at,
            expires_at=_later(at, MOMENT_DWELL_MS),
            priority=MOMENT_PRIORITY[moment.kind],
            interrupt=moment.kind in INTERRUPTING_MOMENTS,
            sections=tuple(sections),
            snapshot_id=record.header.snapshot_id,
            source=MomentSource(
                moment=moment.kind,
                event_ids=(moment.event_id,),
                at=moment.at,
                snapshot_id=record.header.snapshot_id,
                team_id=moment.team_id,
                score=moment.score,
            ),
        )

    def _source(
        self, rev: Revision, view: PersonalizedInsight, record: SnapshotRecord
    ) -> InsightSource:
        if rev.final is None or rev.insight_fingerprint is None:
            raise ValueError("only an open revision is shown")
        return InsightSource(
            storyline_id=rev.storyline_id,
            revision=rev.number,
            insight_fingerprint=rev.insight_fingerprint,
            snapshot_id=record.header.snapshot_id,
            view_fingerprint=view_fingerprint(view),
            verdict=rev.final.verdict,
            evidence_integrity=rev.final.evidence_integrity,
            change_kinds=rev.change_kinds,
        )

    def _show(self, rev: Revision, view: PersonalizedInsight, record: SnapshotRecord) -> None:
        cue = self._emit(
            kind=CueKind.INSIGHT,
            audience=self.profile.audience,
            show_from=record.header.as_of,
            expires_at=None,
            priority=PRIORITY[CueKind.INSIGHT],
            interrupt=True,
            sections=view_sections(view),
            snapshot_id=record.header.snapshot_id,
            source=self._source(rev, view, record),
        )
        self._displayed[rev.storyline_id] = cue

    def _revise(
        self,
        previous: Cue,
        rev: Revision,
        view: PersonalizedInsight,
        state: LifecycleState,
        record: SnapshotRecord,
    ) -> None:
        loud = interrupts(rev.change_kinds)
        notice = (CueSection(kind="notice", text=notice_text(state, rev), mandatory=True),)
        cue = self._emit(
            kind=CueKind.REVISION,
            audience=self.profile.audience,
            show_from=record.header.as_of,
            expires_at=None,
            supersedes=(previous.cue_id,),
            priority=PRIORITY[CueKind.REVISION] if loud else SILENT_REVISION_PRIORITY,
            interrupt=loud,
            sections=(notice if loud else ()) + view_sections(view),
            snapshot_id=record.header.snapshot_id,
            source=self._source(rev, view, record),
        )
        self._displayed[rev.storyline_id] = cue

    def _retract(
        self,
        cue: Cue,
        reason: RetractionReason,
        cause: int | None,
        record: SnapshotRecord,
    ) -> None:
        source = cue.source
        if not isinstance(source, InsightSource):
            raise TypeError("only insight cards are retracted")
        at = record.header.as_of
        fact = next((s.text for s in cue.sections if s.kind == SectionKind.FACT.value), None)
        sections = [CueSection(kind="retraction", text=RETRACTION_TEXT[reason], mandatory=True)]
        if fact is not None:
            sections.append(CueSection(kind="retracted", text=fact))
        self._emit(
            kind=CueKind.RETRACTION,
            audience=None,
            show_from=at,
            expires_at=_later(at, RETRACTION_DWELL_MS),
            supersedes=(cue.cue_id,),
            priority=PRIORITY[CueKind.RETRACTION],
            interrupt=True,
            sections=tuple(sections),
            snapshot_id=record.header.snapshot_id,
            source=RetractionSource(
                retracts=cue.cue_id,
                storyline_id=source.storyline_id,
                revision=source.revision,
                cause_revision=cause,
                reason=reason,
                snapshot_id=record.header.snapshot_id,
            ),
        )
        del self._displayed[source.storyline_id]

    def _set_status(
        self,
        status: BroadcastStatus,
        detail: str | None,
        at: MatchInstant,
        snapshot_id: str | None,
    ) -> None:
        previous = self._status
        if (
            previous is not None
            and isinstance(previous.source, StatusSource)
            and (previous.source.status, previous.source.detail) == (status, detail)
        ):
            return
        sections = [CueSection(kind="status", text=STATUS_TEXT[status], mandatory=True)]
        if detail:
            sections.append(CueSection(kind="detail", text=detail))
        if self.cues and self.cues[-1].show_from.sort_key > at.sort_key:
            at = self.cues[-1].show_from
        self._status = self._emit(
            kind=CueKind.STATUS,
            audience=None,
            show_from=at,
            expires_at=None,
            supersedes=(previous.cue_id,) if previous is not None else (),
            priority=PRIORITY[CueKind.STATUS],
            interrupt=status is BroadcastStatus.UNAVAILABLE,
            sections=tuple(sections),
            snapshot_id=snapshot_id,
            source=StatusSource(
                status=status, snapshot_id=snapshot_id, watermark=self.watermark, detail=detail
            ),
        )


def compile_timeline(
    info: MatchInfo,
    events: Sequence[MatchEvent],
    state: LifecycleState,
    profile: PersonalizationProfile,
    data_status: DataStatus = DataStatus.CONTIGUOUS,
    workspaces: WorkspaceCache | None = None,
) -> CueTimeline:
    """The canonical timeline of one surface for a lifecycle state and its log prefix."""
    compiler = CueCompiler(info, profile, workspaces)
    compiler.advance(state, events, data_status)
    return compiler.timeline()
