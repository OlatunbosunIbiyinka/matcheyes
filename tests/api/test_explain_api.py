"""GET /matches/{id}/storylines/{sid}/revisions/{n}: the read-only work behind a revision."""

from collections.abc import Iterator

import pytest

from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.api.live import Backend
from matcheyes.api.server import CSP
from matcheyes.lifecycle.explain import REFERENCE, Explainer, ReasonerIdentity
from tests.api.support import app, get_json, request, serving, wait_done
from tests.lifecycle.support import evaluator, in_progress, reference

MATCH = in_progress().info.match_id
IDENTITY = ReasonerIdentity(
    kind="recorded-model", name="stand-in", deployment="dep", transcript_sha256="0" * 64
)


@pytest.fixture(scope="module")
def address() -> Iterator[tuple[str, int]]:
    explainer = Explainer(in_progress(), RuleBasedReasoner(), IDENTITY)
    broadcast = app(backends={MATCH: Backend(evaluator(), IDENTITY, explainer)})
    with serving(broadcast) as addr:
        broadcast.live[MATCH].start()
        wait_done(broadcast.live[MATCH])
        yield addr


def test_the_match_list_names_who_reasons(address: tuple[str, int]) -> None:
    _, body = get_json(address, "/matches")
    assert body["matches"][0]["reasoner"] == IDENTITY.model_dump(mode="json")


def test_every_published_revision_shows_its_work(address: tuple[str, int]) -> None:
    for storyline in reference().state.storylines:
        for revision in storyline.revisions:
            path = (
                f"/matches/{MATCH}/storylines/{storyline.storyline_id}/revisions/{revision.number}"
            )
            status, headers, _ = request(address, path)
            assert status == 200 and headers["Content-Security-Policy"] == CSP
            _, body = get_json(address, path)
            assert body["edition"] == 0
            e = body["explanation"]
            assert (e["storyline_id"], e["revision"]) == (storyline.storyline_id, revision.number)
            assert e["reasoner"]["kind"] == "recorded-model"
            assert body["rows"] and {"section", "label", "text", "tone"} == set(body["rows"][0])
            if revision.final is not None:
                assert e["reproduced"] is True and e["agrees_with_reference"] is True


@pytest.mark.parametrize(
    ("suffix", "status"),
    [
        ("storylines/sl-unknown/revisions/1", 404),
        ("storylines/sl-x/revisions/0", 400),
        ("storylines/sl-x/revisions/01", 400),
        ("storylines/sl-x/revisions/99999", 400),
        ("storylines/sl-x/revisions/-1", 400),
        ("storylines/..%2f..%2fetc/revisions/1", 400),
        ("storylines/not-a-storyline/revisions/1", 400),
        ("storylines/sl-x/revisions/1?raw=1", 400),
        ("storylines/sl-x/revision/1", 404),
    ],
)
def test_invalid_revision_requests_are_refused(
    address: tuple[str, int], suffix: str, status: int
) -> None:
    got, body = get_json(address, f"/matches/{MATCH}/{suffix}")
    assert got == status and set(body) == {"error"}


def test_an_unpublished_revision_is_not_found(address: tuple[str, int]) -> None:
    storyline = reference().state.storylines[0]
    path = f"/matches/{MATCH}/storylines/{storyline.storyline_id}/revisions/"
    assert get_json(address, path + str(len(storyline.revisions) + 1))[0] == 404
    assert (
        get_json(address, f"/matches/unknown/storylines/{storyline.storyline_id}/revisions/1")[0]
        == 404
    )


def test_a_match_without_an_explainer_explains_nothing() -> None:
    broadcast = app()
    storyline = reference().state.storylines[0]
    with serving(broadcast) as addr:
        broadcast.live[MATCH].start()
        wait_done(broadcast.live[MATCH])
        status, body = get_json(
            addr, f"/matches/{MATCH}/storylines/{storyline.storyline_id}/revisions/1"
        )
        _, listing = get_json(addr, "/matches")
    assert status == 404 and body == {"error": "revision not published"}
    assert listing["matches"][0]["reasoner"] == REFERENCE.model_dump(mode="json")
