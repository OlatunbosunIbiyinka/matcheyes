"""Cue and timeline contracts: content addressing and the display-semantics invariants."""

from typing import Any

import pytest
from pydantic import ValidationError

from matcheyes.agents.contracts import EvidenceIntegrity, Verdict
from matcheyes.broadcast.contracts import (
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
from matcheyes.domain.time import MatchInstant
from matcheyes.lifecycle.contracts import ChangeKind
from matcheyes.personalization.contracts import Audience, PersonalizationProfile

AT = MatchInstant(period=1, clock_ms=60_000)
LATER = MatchInstant(period=1, clock_ms=180_000)
SNAP = "snap-1"
FAN = PersonalizationProfile(audience=Audience.FAN)
SECTIONS = (CueSection(kind="moment", text="A fact."),)


def moment(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "match_id": "m-1",
        "kind": CueKind.MOMENT,
        "audience": None,
        "show_from": AT,
        "expires_at": LATER,
        "priority": 40,
        "interrupt": False,
        "sections": SECTIONS,
        "snapshot_id": SNAP,
        "source": MomentSource(
            moment=MomentKind.GOAL,
            event_ids=("e-1",),
            at=AT,
            snapshot_id=SNAP,
            team_id="home",
            score=Score(home=1, away=0),
        ),
    }
    return {**fields, **over}


def insight(**over: Any) -> dict[str, Any]:
    source = InsightSource(
        storyline_id="st-1",
        revision=1,
        insight_fingerprint="a" * 64,
        snapshot_id=SNAP,
        view_fingerprint="b" * 64,
        verdict=Verdict.EXPLAINED,
        evidence_integrity=EvidenceIntegrity.INTACT,
        change_kinds=(ChangeKind.CREATED,),
    )
    return (
        moment(
            kind=CueKind.INSIGHT, audience=Audience.FAN, expires_at=None, priority=60, source=source
        )
        | over
    )


def retraction(target: Cue, **over: Any) -> dict[str, Any]:
    source = RetractionSource(
        retracts=target.cue_id,
        storyline_id="st-1",
        revision=1,
        cause_revision=2,
        reason=RetractionReason.WITHDRAWN,
        snapshot_id=SNAP,
    )
    return moment(kind=CueKind.RETRACTION, supersedes=(target.cue_id,), source=source) | over


def status(**over: Any) -> dict[str, Any]:
    source = StatusSource(status=BroadcastStatus.CURRENT, snapshot_id=SNAP, watermark=10)
    return moment(kind=CueKind.STATUS, expires_at=None, source=source) | over


def test_cue_ids_are_content_addresses() -> None:
    a, b = Cue.build(**moment()), Cue.build(**moment())
    assert a.cue_id == b.cue_id and a.cue_id.startswith("cue-")
    assert Cue.build(**moment(priority=41)).cue_id != a.cue_id
    assert Cue.model_validate_json(a.model_dump_json()) == a


def test_a_cue_whose_content_differs_from_its_id_is_refused() -> None:
    payload = Cue.build(**moment()).model_dump(mode="json")
    payload["sections"][0]["text"] = "Another fact."
    with pytest.raises(ValidationError, match="content address"):
        Cue.model_validate(payload)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (moment(kind=CueKind.INSIGHT, audience=Audience.FAN, expires_at=None), "needs a"),
        (moment(snapshot_id="snap-2"), "different snapshots"),
        (moment(expires_at=None), "only moments and retractions expire"),
        (moment(expires_at=AT), "expire after"),
        (moment(audience=Audience.FAN), "one audience"),
        (insight(audience=None), "one audience"),
        (insight(audience=Audience.ANALYST), "fans and broadcasters"),
        (insight(supersedes=("cue-" + "0" * 24,)), "supersede nothing"),
        (insight(kind=CueKind.REVISION), "exactly the cue it revises"),
        (status(expires_at=LATER), "only moments and retractions expire"),
    ],
)
def test_cue_invariants(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Cue.build(**fields)


def test_a_retraction_supersedes_exactly_what_it_retracts() -> None:
    shown = Cue.build(**insight())
    assert Cue.build(**retraction(shown)).supersedes == (shown.cue_id,)
    with pytest.raises(ValidationError, match="exactly the cue it retracts"):
        Cue.build(**retraction(shown, supersedes=()))


def timeline(*cues: Cue, profile: PersonalizationProfile = FAN) -> CueTimeline:
    return CueTimeline.build(
        match_id="m-1",
        home=TeamRef(team_id="home", name="Home FC", short_name="HOM"),
        away=TeamRef(team_id="away", name="Away FC", short_name="AWA"),
        profile=profile,
        watermark=10,
        snapshots=1,
        cues=cues,
    )


def test_a_timeline_is_content_addressed_and_replayable() -> None:
    shown = Cue.build(**insight())
    tl = timeline(Cue.build(**status()), shown, Cue.build(**retraction(shown)))
    assert tl.timeline_id == timeline(*tl.cues).timeline_id
    assert CueTimeline.model_validate_json(tl.model_dump_json()) == tl
    payload = tl.model_dump(mode="json")
    payload["watermark"] = 11
    with pytest.raises(ValidationError, match="content address"):
        CueTimeline.model_validate(payload)


def test_timeline_invariants() -> None:
    shown = Cue.build(**insight())
    late = Cue.build(**moment(show_from=LATER, expires_at=MatchInstant(period=2, clock_ms=0)))
    with pytest.raises(ValidationError, match="duplicate"):
        timeline(shown, shown)
    with pytest.raises(ValidationError, match="ordered by match time"):
        timeline(late, shown)
    with pytest.raises(ValidationError, match="another match"):
        timeline(Cue.build(**moment(match_id="m-2")))
    with pytest.raises(ValidationError, match="another audience"):
        timeline(Cue.build(**insight(audience=Audience.BROADCASTER)))
    with pytest.raises(ValidationError, match="not earlier"):
        timeline(Cue.build(**retraction(shown)), shown)
    with pytest.raises(ValidationError, match="fans and broadcasters"):
        timeline(profile=PersonalizationProfile(audience=Audience.ANALYST))
