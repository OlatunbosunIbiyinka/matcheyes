"""Known issue: edition numbers do not identify a server process (docs/known-issues.md).

A browser that loses its stream reconnects with the `Last-Event-ID` of the last message it
received, `edition:index`. If the server restarted in between with different content for the same
match (here, one event removed), its edition 0 has the same number, so the stream resumes after
`index` of the new content without any signal to rebuild. The client is left with the old messages
up to `index` followed by the new ones: a mixture of two replays. This test states the correct
outcome and is expected to fail until edition identity covers the server process.
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


def _client_view(received: list[dict[str, str]], resumed: list[dict[str, str]]) -> list[str]:
    """What a page holds after resuming: it rebuilds only when the edition number changes."""
    view = [m["data"] for m in received if "id" in m]
    edition = next(m["data"] for m in received if m.get("event") == "edition")
    for m in resumed:
        if m.get("event") == "edition" and m["data"] != edition:
            view, edition = [], m["data"]
        elif "id" in m:
            view.append(m["data"])
    return view


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="known issue: edition numbers restart with the server",
)
def test_a_restarted_server_makes_a_resuming_client_rebuild() -> None:
    before = app()
    with serving(before) as address:
        before.live[MATCH].start()
        wait_done(before.live[MATCH])
        old = sse(request(address, PATH)[2])
    after = BroadcastApp({MATCH: _revised()}, speed=0, loop=False)
    with serving(after) as address:
        after.live[MATCH].start()
        wait_done(after.live[MATCH])
        new = sse(request(address, PATH)[2])
        old_ids = [m for m in old if "id" in m]
        new_ids = [m for m in new if "id" in m]
        differs = next(i for i, (a, b) in enumerate(zip(old_ids, new_ids, strict=False)) if a != b)
        received = old[: old.index(old_ids[differs]) + 1]
        resumed = sse(request(address, PATH, headers={"Last-Event-ID": old_ids[differs]["id"]})[2])
    assert _client_view(received, resumed) == [m["data"] for m in new_ids]
