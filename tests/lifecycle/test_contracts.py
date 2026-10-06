"""Lifecycle contracts refuse inconsistent revisions and storylines at construction."""

from typing import Any

import pytest
from pydantic import ValidationError

from matcheyes.agents.contracts import EvidenceIntegrity
from matcheyes.lifecycle.contracts import (
    GENESIS,
    ChangeKind,
    LifecycleState,
    Revision,
    Storyline,
    StorylineState,
    WithdrawalReason,
    chain_fingerprint,
)
from tests.lifecycle.support import reference


def _storyline() -> Storyline:
    return next(s for s in reference().state.storylines if len(s.revisions) > 1)


def _revision(**update: Any) -> dict[str, Any]:
    return {**dict(_storyline().revisions[0]), **update}


def _flipped(state: StorylineState) -> StorylineState:
    return StorylineState.WITHDRAWN if state is StorylineState.OPEN else StorylineState.OPEN


def _rechain(data: dict[str, Any]) -> dict[str, Any]:
    state = StorylineState(data["state"])
    data["chain_fingerprint"] = chain_fingerprint(
        data["previous_chain_fingerprint"], data["snapshot_id"], data["insight_fingerprint"], state
    )
    return data


def test_a_genuine_revision_round_trips() -> None:
    r = _storyline().revisions[0]
    assert Revision.model_validate(_revision()) == r
    assert r.previous_chain_fingerprint == GENESIS


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"final": None}, "verified insight"),
        ({"change_kinds": (ChangeKind.WITHDRAWN,)}, "not a withdrawal"),
        ({"candidate_id": "ctx-shift-other-shots-1"}, "identifiers"),
        ({"insight_fingerprint": "0" * 64}, "fingerprint does not match the insight"),
        ({"evidence_integrity": EvidenceIntegrity.COMPROMISED}, "integrity"),
    ],
)
def test_an_inconsistent_open_revision_is_refused(update: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Revision.model_validate(_rechain(_revision(**update)))


def test_a_broken_chain_is_refused() -> None:
    with pytest.raises(ValidationError, match="chain fingerprint"):
        Revision.model_validate(_revision(chain_fingerprint="1" * 64))


def _withdrawal(**update: Any) -> dict[str, Any]:
    base = _revision(
        state=StorylineState.WITHDRAWN,
        change_kinds=(ChangeKind.WITHDRAWN,),
        candidate_id=None,
        investigation_id=None,
        anchor_bin=None,
        final=None,
        verification=None,
        insight_fingerprint=None,
        evidence_integrity=None,
        audit_findings=(),
        withdrawal_reason=WithdrawalReason.NOT_DETECTED,
    )
    return _rechain({**base, **update})


def test_a_withdrawal_carries_only_its_reason() -> None:
    assert Revision.model_validate(_withdrawal()).final is None
    with pytest.raises(ValidationError, match="carries no insight"):
        Revision.model_validate(_withdrawal(anchor_bin=3))
    with pytest.raises(ValidationError, match="only its reason"):
        Revision.model_validate(_withdrawal(withdrawal_reason=None))
    with pytest.raises(ValidationError, match="only its reason"):
        Revision.model_validate(
            _withdrawal(change_kinds=(ChangeKind.WITHDRAWN, ChangeKind.CREATED))
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda s: {"revisions": s.revisions[1:]}, "starts with the creation"),
        (lambda s: {"state": _flipped(s.state)}, "latest revision"),
        (
            lambda s: {"verified_through": None if s.state is StorylineState.OPEN else "snap-x"},
            "only an open storyline",
        ),
        (
            lambda s: {
                "revisions": (s.revisions[0], s.revisions[1].model_copy(update={"number": 3}))
            },
            "without gaps",
        ),
        (
            lambda s: {
                "revisions": (
                    s.revisions[0],
                    s.revisions[1].model_copy(update={"storyline_id": "sl-other"}),
                )
            },
            "another storyline",
        ),
    ],
)
def test_an_inconsistent_storyline_is_refused(mutate: Any, message: str) -> None:
    s = _storyline()
    data = {**dict(s), **mutate(s)}
    with pytest.raises(ValidationError, match=message):
        Storyline.model_validate(data)


def test_state_lookup_and_empty() -> None:
    state = reference().state
    s = state.storylines[0]
    assert state.storyline(s.storyline_id) == s
    empty = LifecycleState.empty(state.match_id)
    assert empty.last_snapshot is None and empty.storylines == ()
    assert state.snapshots[0].header.label.endswith("'")
