"""Show your work: the structured record behind each revision, and what it refuses to show."""

import json
from functools import cache, partial

from matcheyes.agents.reasoning import AgentTask, RuleBasedReasoner, Step
from matcheyes.lifecycle.contracts import Revision
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.evaluate import evaluate_snapshot
from matcheyes.lifecycle.explain import (
    CACHE_LIMIT,
    REFERENCE,
    UNKNOWN,
    Explainer,
    ReasonerIdentity,
    work_rows,
)
from tests.lifecycle.support import in_progress, reference

MARK = "MARKER-free-text-from-the-model"


class Hostile(RuleBasedReasoner):
    """Reasons like the reference, but writes a marker into every free-text field it controls,
    asks for evidence with a marker argument, and asserts a marker fact and value."""

    @property
    def name(self) -> str:
        return "hostile"

    def respond(self, task: AgentTask) -> str:
        out = json.loads(super().respond(task))
        requests = out.get("requests", [])
        for r in requests:
            r["purpose"] = MARK
        if requests:
            bad = dict(requests[0], request_id="zz-marker", arguments={"team_id": MARK[:80]})
            requests.append(bad)
        if task.step is Step.CHALLENGE:
            out["objections"] = [MARK]
        for h in out.get("hypotheses", []):
            if isinstance(h, dict):
                h["statement"] = MARK
                if task.evidence:
                    eid = task.evidence[0].evidence_id
                    h["supporting"] = [
                        *h.get("supporting", []),
                        {"evidence_id": eid, "fact": MARK[:60], "comparator": "eq", "value": MARK},
                    ]
        if "summary" in out:
            out["summary"] = MARK
        return json.dumps(out)


@cache
def revisions() -> tuple[Revision, ...]:
    return tuple(r for s in reference().state.storylines for r in s.revisions)


@cache
def explainer() -> Explainer:
    return Explainer(in_progress(), RuleBasedReasoner(), REFERENCE)


def test_every_revision_is_reproduced_and_agrees_with_the_reference() -> None:
    assert revisions()
    for revision in revisions():
        e = explainer()(revision)
        assert (e.storyline_id, e.revision, e.snapshot_id) == (
            revision.storyline_id,
            revision.number,
            revision.snapshot_id,
        )
        assert e.reasoner == REFERENCE and e.audit_findings == revision.audit_findings
        if revision.final is None:
            assert e.outcome is None and e.reproduced is None
            continue
        assert e.reproduced is True and e.agrees_with_reference is True
        assert e.narrative == revision.final.narrative
        assert e.outcome is not None and e.outcome.verdict == revision.final.verdict.value
        assert {v.evidence_id for v in e.evidence} == {
            i.evidence_id for i in revision.final.evidence
        }
        ok = {c.evidence_id for c in e.tool_calls if c.status == "ok"}
        assert ok >= {v.evidence_id for v in e.evidence}
        assert all(c.requested_by in ("investigator", "challenger") for c in e.tool_calls)
        if revision.verification is not None:
            assert e.eligible_strength == revision.verification.eligible_strength.value
            assert e.gates == revision.verification.gates
            assert [h.kind for h in e.hypotheses] == [
                h.kind.value for h in revision.verification.hypotheses
            ]


def test_explanations_are_cached_and_bounded() -> None:
    first = revisions()[0]
    assert explainer()(first) is explainer()(first)
    assert len(explainer()._cache) <= CACHE_LIMIT


def test_model_written_text_never_reaches_the_explanation() -> None:
    match = in_progress()
    published = replay(match.info, match.events, partial(evaluate_snapshot, model=Hostile()))
    identity = ReasonerIdentity(kind="recorded-model", name="hostile", transcript_sha256="0" * 64)
    hostile = Explainer(match, Hostile(), identity)
    explained = [
        hostile(r) for s in published.state.storylines for r in s.revisions if r.final is not None
    ]
    assert explained and all(e.reproduced for e in explained)
    text = "".join(e.model_dump_json() for e in explained)
    assert MARK not in text and MARK[:60] not in text
    checks = [c for e in explained for h in e.hypotheses for c in h.checks]
    planted = [c for c in checks if c.fact == UNKNOWN]
    assert planted and all(not c.accepted and c.value is None for c in planted)
    refused = [c for e in explained for c in e.tool_calls if c.status != "ok"]
    assert all(c.inputs == {} for c in refused)
    assert all(e.reasoner == identity for e in explained)


def test_rows_put_the_verifier_above_every_proposal() -> None:
    for revision in revisions():
        rows = work_rows(explainer()(revision))
        sections = list(dict.fromkeys(r.section for r in rows))
        assert sections[0] == "Lifecycle" and sections[-1] == "Reasoner"
        if revision.final is None:
            assert "Verified result" not in sections
            continue
        assert sections.index("Verified result") < sections.index("Explanations considered")
        for i, row in enumerate(rows):
            if row.tone == "proposed" and row.label == "proposed":
                assert rows[i - 1].tone == "verified" and rows[i - 1].text.startswith("verified")


def test_hostile_text_never_reaches_the_rows() -> None:
    match = in_progress()
    published = replay(match.info, match.events, partial(evaluate_snapshot, model=Hostile()))
    hostile = Explainer(match, Hostile(), REFERENCE)
    rows = [
        row
        for s in published.state.storylines
        for r in s.revisions
        for row in work_rows(hostile(r))
    ]
    assert rows and not any(MARK[:40] in row.label + row.text for row in rows)


def test_a_revision_without_an_insight_or_snapshot_is_explained_from_the_lifecycle() -> None:
    revision = revisions()[0].model_copy(update={"snapshot_id": "snap-unknown"})
    e = Explainer(in_progress(), RuleBasedReasoner(), REFERENCE)(revision)
    assert e.outcome is None and e.tool_calls == () and e.reproduced is None
    assert e.state == revision.state.value
