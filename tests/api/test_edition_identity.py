"""Edition identity covers the server process: another run's stream is never resumed (ADR-0017).

A browser that loses its stream reconnects with the `Last-Event-ID` of the last message it
received, `run:edition:index`. Every server process gives each match a new random run, so after a
restart, with the same content or a data correction (here, one event removed), the resume names
another run: the server answers 204, the browser's stream closes, and a new stream (which sends no
`Last-Event-ID`) rebuilds the page from the new run's start. No client can hold a mixture of two
replays.
"""

import pytest

from matcheyes.api.server import BroadcastApp
from matcheyes.domain.match import ObservableMatch
from tests.api.support import app, request, serving, sse, wait_done
from tests.lifecycle.support import in_progress

MATCH = in_progress().info.match_id
PATH = f"/matches/{MATCH}/stream?audience=fan"


def _revised() -> ObservableMatch:
    """The same match with one event removed halfway through, as after a data correction."""
    match = in_progress()
    half = len(match.events) // 2
    return match.model_copy(update={"events": match.events[:half] + match.events[half + 1 :]})


@pytest.mark.parametrize("revised", [False, True], ids=["same-content", "corrected-content"])
def test_a_restarted_server_makes_a_resuming_client_rebuild(revised: bool) -> None:
    before = app()
    with serving(before) as address:
        before.live[MATCH].start()
        wait_done(before.live[MATCH])
        old = sse(request(address, PATH)[2])
    after = BroadcastApp({MATCH: _revised() if revised else in_progress()}, speed=0, loop=False)
    with serving(after) as address:
        after.live[MATCH].start()
        wait_done(after.live[MATCH])
        new = sse(request(address, PATH)[2])
        old_ids = [m for m in old if "id" in m]
        for last in (old_ids[0], old_ids[len(old_ids) // 2], old_ids[-1]):
            status, _, body = request(address, PATH, headers={"Last-Event-ID": last["id"]})
            assert (status, body) == (204, b"")
        assert after.streams.active == 0
        rebuilt = sse(request(address, PATH)[2])
    assert rebuilt == new
    runs = {m["id"].split(":")[0] for m in rebuilt if "id" in m}
    assert runs == {after.live[MATCH].run}
    assert after.live[MATCH].run != before.live[MATCH].run
