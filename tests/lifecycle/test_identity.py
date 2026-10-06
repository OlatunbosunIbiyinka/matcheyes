"""Storyline identity: deterministic IDs and the matching rule (key, suppression, tie-breaks)."""

from collections.abc import Sequence

from matcheyes.lifecycle.contracts import LifecycleState, StorylineState
from matcheyes.lifecycle.evaluate import InsightOutcome
from matcheyes.lifecycle.identity import match_storylines, opposite_storyline, storyline_id
from matcheyes.lifecycle.reconcile import reconcile
from tests.lifecycle.support import evaluated, in_progress, insights, moved, reversed_direction


def _state(*batches: Sequence[InsightOutcome]) -> LifecycleState:
    state = LifecycleState.empty(in_progress().info.match_id)
    for watermark, batch in enumerate(batches, start=1):
        state = reconcile(state, evaluated(watermark * 10, *batch))
    return state


def test_ids_derive_from_the_key_and_first_anchor_only() -> None:
    assert storyline_id("m", "t", "shots", "up", 12) == "sl-m-t-shots-up-12"
    assert (
        storyline_id("m", "t", "shots", "up", 12, {"sl-m-t-shots-up-12"}) == "sl-m-t-shots-up-12-2"
    )
    taken = {"sl-m-t-shots-up-12", "sl-m-t-shots-up-12-2"}
    assert storyline_id("m", "t", "shots", "up", 12, taken) == "sl-m-t-shots-up-12-3"


def test_a_new_storyline_at_a_used_first_anchor_gets_a_suffix() -> None:
    a = insights()[0]
    state = _state((a,), (moved(a, 15),), (moved(a, 30),), (moved(a, 30), a))
    first, second = state.storylines
    assert (first.first_anchor, first.anchor) == (a.anchor_bin, a.anchor_bin + 30)
    assert second.first_anchor == a.anchor_bin
    assert second.storyline_id == f"{first.storyline_id}-2"


def test_ties_go_to_the_nearest_then_the_earliest_storyline() -> None:
    a = insights()[0]
    state = _state((a, moved(a, 10)))
    first, second = state.storylines
    assert match_storylines(state.storylines, [moved(a, 7)], 15) == {0: second.storyline_id}
    assert match_storylines(state.storylines, [moved(a, 5)], 15) == {0: first.storyline_id}
    assert match_storylines(state.storylines, [a], 15) == {0: first.storyline_id}


def test_matching_is_one_to_one_and_keyed() -> None:
    a = insights()[0]
    state = _state((a,))
    near, far = moved(a, 1), moved(a, 3)
    assert match_storylines(state.storylines, [far, near], 15) == {
        1: state.storylines[0].storyline_id
    }
    assert match_storylines(state.storylines, [reversed_direction(a)], 15) == {}
    assert match_storylines(state.storylines, [moved(a, 16)], 15) == {}
    assert match_storylines(state.storylines, [moved(a, 15)], 15) == {
        0: state.storylines[0].storyline_id
    }


def test_withdrawn_storylines_can_be_matched() -> None:
    a = insights()[0]
    state = _state((a,), ())
    assert state.storylines[0].state is StorylineState.WITHDRAWN
    assert match_storylines(state.storylines, [moved(a, 2)], 15) == {
        0: state.storylines[0].storyline_id
    }


def test_the_opposite_storyline_is_the_nearest_open_one_not_yet_touched() -> None:
    a = insights()[0]
    state = _state((a, moved(a, 12)))
    first, second = state.storylines
    flipped = reversed_direction(moved(a, 9))
    assert opposite_storyline(state.storylines, flipped, 15, ()) == second
    assert opposite_storyline(state.storylines, flipped, 15, {second.storyline_id}) == first
    assert opposite_storyline(state.storylines, reversed_direction(moved(a, 40)), 15, ()) is None
    assert opposite_storyline(state.storylines, a, 15, ()) is None
