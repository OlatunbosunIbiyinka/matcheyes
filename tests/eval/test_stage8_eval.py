"""Stage 8 evaluation on a match in progress: every property holds, and the measures detect a
stale card, a missing retraction and an altered card."""

from functools import cache
from typing import Any

import pytest

from matcheyes.broadcast.contracts import Cue, CueKind, CueSection, CueTimeline
from matcheyes.personalization.contracts import Audience
from matcheyes_eval.__main__ import main
from matcheyes_eval.stage2 import Case
from matcheyes_eval.stage8 import (
    Stage8Results,
    build_reference,
    evaluate_stage8,
    evaluate_surface,
    format_stage8,
    profiles,
)
from matcheyes_synth.scenarios import scenario
from tests.broadcast.support import timeline
from tests.lifecycle.support import SCENARIO, in_progress

pytestmark = pytest.mark.integration


def case() -> Case:
    spec = scenario(SCENARIO)
    return Case(spec, spec.default_seed, "planted", in_progress())


@cache
def results() -> Stage8Results:
    return evaluate_stage8([case()], "development", 1, live_matches=1)


def test_every_broadcast_property_holds() -> None:
    r = results()
    assert r.matches == 1 and r.surfaces == 6 and r.live_cases == 1
    numbered = {name.split(" ", 1)[0] for name in r.properties}
    assert {"0", "1", "2", "4", "8", "9", "10"} <= numbered
    for name, rate in r.properties.items():
        assert rate.total > 0 and rate.hits == rate.total, name
    assert r.stale_card_minutes == r.stale_status_minutes == 0
    assert r.tautological == r.displayed_tautological == r.leaks == 0
    assert r.moments_expected > 0 and max(r.moment_delay_s) <= 60
    assert r.first_cue and r.first_cue[0] <= 1.0
    assert set(r.load) == {"fan", "broadcaster"}


def test_the_report_states_actual_results() -> None:
    text = format_stage8(results())
    assert "properties (passed / checked):" in text
    assert "3 stale cue-minutes: cards 0; status 0" in text
    assert "baseline - first current lifecycle revision" in text


def _rerun(cues: tuple[Cue, ...]) -> Stage8Results:
    c = case()
    r = Stage8Results(split="test", seeds=1)
    ref = build_reference(c, r)
    key = (Audience.FAN, None)
    canonical = timeline()
    fields = {k: getattr(canonical, k) for k in CueTimeline.model_fields if k != "timeline_id"}
    tl = CueTimeline.build(**{**fields, "cues": cues})
    evaluate_surface(ref, key, profiles(c)[key], tl, r)
    return r


def _failed(r: Stage8Results, prefix: str) -> bool:
    return any(
        rate.hits < rate.total for name, rate in r.properties.items() if name.startswith(prefix)
    )


def test_a_missing_retraction_is_a_stale_card() -> None:
    cues = tuple(c for c in timeline().cues if c.kind is not CueKind.RETRACTION)
    r = _rerun(cues)
    assert r.stale_card_minutes > 0
    assert _failed(r, "2 retraction")


def test_an_altered_card_is_not_traceable() -> None:
    cues = list(timeline().cues)
    i = next(i for i, c in enumerate(cues) if c.kind is CueKind.INSIGHT)
    fields: dict[str, Any] = {
        k: getattr(cues[i], k) for k in type(cues[i]).model_fields if k != "cue_id"
    }
    fields["sections"] = (*fields["sections"], CueSection(kind="caveat", text="Added later."))
    original, cues[i] = cues[i].cue_id, Cue.build(**fields)
    r = _rerun(tuple(c for c in cues if original not in c.supersedes))
    assert _failed(r, "1 traceability: insight")


@pytest.mark.slow
def test_stage8_command_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stage8", "--seeds", "1", "--matches", "1", "--live-matches", "0"]) == 0
    assert "Stage 8 broadcast cues - development split, 1 seed(s)" in capsys.readouterr().out
