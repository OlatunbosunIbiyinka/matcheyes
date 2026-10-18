"""Pause and resume of the replay page: held messages are applied later, exactly as received.

Static checks pin the page to one dispatch path and the existing listeners. The hold functions are
run under Node from the page's own source.
"""

import json
import re
import shutil
import subprocess

import pytest

from tests.support.imports import SRC_ROOT

STATIC = SRC_ROOT / "matcheyes" / "api" / "static"
JS = (STATIC / "app.js").read_text("utf-8")
HTML = (STATIC / "index.html").read_text("utf-8")
STREAM_EVENTS = ("edition", "clock", "stats", "cue", "end")
HANDLERS = ("onEdition", "onClock", "onStats", "onCue", "onEnd")
SECTION = JS.split("// --- pause and resume", 1)[1].split("\n// --- ", 1)[0]
HOLD_FUNCTIONS = (
    "newHold",
    "parsedField",
    "holdMessage",
    "counted",
    "heldParts",
    "holdText",
    "resumeSummary",
)


def _function(name: str) -> str:
    found = re.search(rf"function {name}\(.*?\n\}}\n", JS, re.S)
    assert found, name
    return found.group(0)


def test_every_stream_event_is_received_through_one_path() -> None:
    for event in STREAM_EVENTS:
        assert (
            f'stream.addEventListener("{event}", (e) => '
            f'{{ if (stream === source) receive("{event}", e.data); }});'
        ) in JS
    listened = re.findall(r'stream\.addEventListener\(\s*"([a-z_]+)"', JS)
    assert sorted(listened) == sorted((*STREAM_EVENTS, "open", "error", "unavailable"))
    table = re.search(r"const STREAM_HANDLERS = \{(.*?)\};", JS)
    assert table
    assert dict(re.findall(r"(\w+): (\w+)", table.group(1))) == dict(
        zip(STREAM_EVENTS, HANDLERS, strict=True)
    )
    for handler in HANDLERS:
        assert len(re.findall(rf"\b{handler}\(", JS)) == 1, handler


def test_each_applied_message_is_isolated_and_reported() -> None:
    body = _function("apply")
    assert re.search(r"try \{\s*STREAM_HANDLERS\[type\]\(", body)
    assert "catch (error) {\n    reportError(error);" in body
    receive = _function("receive")
    assert "if (held) holdReceived(type, data);" in receive and "else apply(type, data);" in receive
    resume = _function("resume")
    assert "for (const [type, data] of done.items) apply(type, data);" in resume


def test_held_messages_are_silent_and_resume_recomputes_the_status() -> None:
    for name in ("holdMessage", "holdReceived"):
        body = _function(name)
        assert "announce(" not in body and "alertNow(" not in body, name
    resume = _function("resume")
    assert resume.index("catchingUp = true;") < resume.index("apply(type, data)")
    assert resume.index("apply(type, data)") < resume.index("catchingUp = history;")
    assert resume.index("catchingUp = history;") < resume.index("syncReplay();")
    sync = _function("syncReplay")
    assert "EventSource.CONNECTING && !ended" in sync and '"reconnecting"' in sync


def test_a_held_end_keeps_the_closures_after_it_quiet() -> None:
    assert "return held ? held.ended : ended;" in _function("streamEnded")
    stream = _function("openStream")
    assert '} else if (!streamEnded()) {\n      if (replay !== "reconnecting")' in stream
    assert 'streamEnded() ? "complete" : "replaying"' in stream
    assert not re.search(r"[!(]\s*ended\b", stream)


def test_a_replaced_stream_changes_nothing_and_an_unavailable_replay_stops_the_stream() -> None:
    stream = _function("openStream")
    for event in ("open", "unavailable", "error"):
        start = stream.index(f'stream.addEventListener("{event}"')
        assert "if (stream !== source) return;" in stream[start : stream.index("\n", start) + 40]
    start = stream.index('stream.addEventListener("unavailable"')
    unavailable = stream[start : stream.index("});", start)]
    assert "stream.close();" in unavailable and "held = null;" in unavailable
    assert 'setReplay("unavailable", text);' in unavailable and "alertNow(text);" in unavailable
    assert "receive(" not in unavailable


def test_a_new_run_resets_the_page_and_stale_work_is_ignored() -> None:
    edition = _function("onEdition")
    assert "const otherRun = message.run !== run;" in edition
    assert "if (reloading || otherRun || number !== edition) {" in edition
    assert edition.index("reset();") < edition.index("run = message.run;")
    work = _function("showWork")
    assert work.index("const requestedRun = run;") < work.index("await fetch(workUrl)")
    assert 'if (run !== requestedRun || body.run !== run) throw new Error("another replay");' in (
        work
    )
    assert work.index("another replay") < work.index("renderRows(")


def test_overflow_recovery_uses_a_new_stream_and_no_automatic_reconnect() -> None:
    received = _function("holdReceived")
    assert "held.overflow" in received and "source.close()" in received
    resume = _function("resume")
    start = resume.index("if (done.overflow)")
    overflow = resume[start : resume.index("return;", start)]
    assert "source.close()" in overflow and "openStream();" in overflow
    assert JS.count('new EventSource("/matches/"') == 1
    assert "new EventSource" in _function("openStream")
    failure = JS[JS.index('stream.addEventListener("error"') :]
    assert "could not be reloaded" in failure and 'setReplay("unavailable", text);' in failure
    assert "alertNow(text);" in failure


def test_the_hold_limits_are_fixed_constants() -> None:
    assert "const HOLD_MAX_MESSAGES = 5000;" in SECTION
    assert "const HOLD_MAX_UNITS = 4000000;" in SECTION
    assert not re.search(r"\b(?:setTimeout|setInterval|localStorage|sessionStorage)\b", JS)


def test_the_pause_control_is_a_described_button_without_a_toggle_state() -> None:
    button = re.search(r"<button id=\"hold\"[^>]*>(.*?)</button>", HTML, re.S)
    assert button and "Pause updates" in button.group(1)
    assert 'class="hold-label"' in button.group(1)
    assert 'href="#me-pause"' in button.group(1)
    markup = button.group(0)
    assert 'type="button"' in markup and 'aria-describedby="hold-status"' in markup
    assert 'aria-disabled="true"' in markup and "aria-pressed" not in markup
    assert " disabled" not in markup
    status = re.search(r"<p id=\"hold-status\"[^>]*>", HTML)
    assert status and "aria-live" not in status.group(0) and "role=" not in status.group(0)
    assert '"Resume updates" : "Pause updates"' in JS
    assert 'href", held ? "#me-play" : "#me-pause"' in JS or "#me-play" in JS


def test_hold_wording_names_no_internals_or_causes() -> None:
    text = " ".join(re.findall(r'"([^"]*)"', SECTION))
    assert not re.search(r"because|caused|due to|led to|hypothes|metric|claim", text, re.I)
    assert not re.search(r"\b(?:queue|buffer|EventSource|SSE|message)\b", text)


def _run_hold(script: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    constants = [
        re.search(rf"const {name} = [^;]*;", JS)
        for name in ("HOLD_MAX_MESSAGES", "HOLD_MAX_UNITS", "HELD_ANALYSIS")
    ]
    assert all(constants)
    source = "\n".join(
        [*(c.group(0) for c in constants if c), *(_function(n) for n in HOLD_FUNCTIONS), script]
    )
    done = subprocess.run(  # noqa: S603 - fixed interpreter; the page's own source on stdin
        [node, "-"], input=source, capture_output=True, text=True, timeout=60, check=True
    )
    return json.loads(done.stdout)


HOLD_RUNNER = """
const cue = (kind) => JSON.stringify({ kind, cue_id: "c" + Math.random() });
const ed = (n) => JSON.stringify({ edition: n });
const fill = (steps, edition = 0, ended = false) => {
  const hold = newHold(edition, ended, "12'");
  for (const [type, data] of steps) holdMessage(hold, type, data);
  return hold;
};
const view = (h) => ({ ...h, items: h.items.map(([t]) => t) });
"""


def test_a_hold_keeps_messages_in_order_and_counts_what_waits() -> None:
    result = _run_hold(
        HOLD_RUNNER
        + """
        const h = fill([["clock", "{}"], ["cue", cue("moment")], ["stats", "{}"],
          ["cue", cue("insight")], ["cue", cue("retraction")], ["cue", cue("status")]]);
        console.log(JSON.stringify([view(h), holdText(h), resumeSummary(h, "14'"),
          holdText(fill([])), resumeSummary(fill([]), "12'")]));"""
    )
    assert isinstance(result, list)
    hold, text, summary, empty_text, empty_summary = result
    assert hold["items"] == ["clock", "cue", "stats", "cue", "cue", "cue"]
    assert (hold["moments"], hold["analysis"]) == (1, 2)
    assert not (hold["restarted"] or hold["finished"] or hold["overflow"])
    assert text == "Paused at 12'. Waiting: 1 match event, 2 analysis updates."
    assert summary == "Resumed. 1 match event and 2 analysis updates applied; now at 14'."
    assert empty_text == "Paused at 12'. No new match events or analysis updates yet."
    assert empty_summary == "Resumed. Now at 12'."


def test_a_new_edition_discards_what_it_resets_and_repeats_are_dropped() -> None:
    result = _run_hold(
        HOLD_RUNNER
        + """
        const restart = fill([["cue", cue("moment")], ["end", ""], ["edition", ed(1)],
          ["clock", "{}"], ["edition", ed(1)], ["cue", cue("insight")]]);
        const same = fill([["edition", ed(0)], ["clock", "{}"], ["edition", ed(0)]]);
        const ends = fill([["end", ""], ["edition", ed(0)], ["end", ""], ["end", ""]]);
        const wasEnded = fill([["edition", ed(0)], ["end", ""]], 0, true);
        const reEnded = fill([["end", ""], ["edition", ed(2)], ["end", ""], ["end", ""]]);
        console.log(JSON.stringify([view(restart), holdText(restart),
          resumeSummary(restart, "3'"), view(same), view(ends), holdText(ends),
          view(wasEnded), view(reEnded)]));"""
    )
    assert isinstance(result, list)
    restart, text, summary, same, ends, ends_text, was_ended, re_ended = result
    assert restart["items"] == ["edition", "clock", "cue"]
    assert (restart["edition"], restart["restarted"], restart["finished"]) == (1, True, False)
    assert (restart["moments"], restart["analysis"]) == (0, 1)
    assert text == "Paused at 12'. Waiting: 1 analysis update. The replay has restarted."
    assert summary == "Resumed. The replay restarted while paused; now at 3'."
    assert same["items"] == ["clock"] and not same["restarted"]
    assert ends["items"] == ["end"] and ends["finished"]
    assert ends_text.endswith("The replay has finished.")
    assert was_ended["items"] == [] and not was_ended["finished"]
    assert re_ended["items"] == ["edition", "end"] and re_ended["finished"]


def test_an_edition_of_another_run_restarts_the_hold() -> None:
    result = _run_hold(
        HOLD_RUNNER
        + """
        const run = (r, n) => JSON.stringify({ edition: n, run: r });
        const hold = newHold(0, true, "12'", "a");
        for (const [t, d] of [["cue", cue("moment")], ["edition", run("a", 0)], ["end", ""],
          ["edition", run("b", 0)], ["clock", "{}"], ["edition", run("b", 0)]]) {
          holdMessage(hold, t, d);
        }
        console.log(JSON.stringify(view(hold)));"""
    )
    assert isinstance(result, dict)
    assert result["items"] == ["edition", "clock"]
    assert (result["edition"], result["run"], result["restarted"]) == (0, "b", True)
    assert (result["ended"], result["finished"], result["moments"]) == (False, False, 0)


def test_malformed_edition_events_are_reduced_as_the_handler_reads_them() -> None:
    result = _run_hold(
        HOLD_RUNNER
        + """
        const bad = fill([["clock", "{}"], ["edition", "{oops"], ["edition", ed(0)],
          ["end", ""]], 0, true);
        const nothing = fill([["clock", "{}"], ["edition", "null"], ["edition", ed(0)]]);
        const scalar = fill([["clock", "{}"], ["edition", "5"], ["edition", "{}"]]);
        console.log(JSON.stringify([view(bad), view(nothing), view(scalar)]));"""
    )
    assert isinstance(result, list)
    bad, nothing, scalar = result
    assert bad["items"] == ["clock", "edition"] and not bad["restarted"]
    assert nothing["items"] == ["clock", "edition"] and not nothing["restarted"]
    assert scalar["items"] == ["edition"] and scalar["restarted"]


def test_either_limit_overflows_the_hold_and_frees_it() -> None:
    result = _run_hold(
        HOLD_RUNNER
        + """
        const count = newHold(0, false, "12'");
        for (let i = 0; i < HOLD_MAX_MESSAGES; i++) holdMessage(count, "clock", "{}");
        const atLimit = [count.items.length, count.overflow];
        holdMessage(count, "clock", "{}");
        holdMessage(count, "edition", ed(1));
        const size = newHold(0, false, "12'");
        holdMessage(size, "cue", "x".repeat(HOLD_MAX_UNITS));
        const sizeAtLimit = [size.items.length, size.overflow];
        holdMessage(size, "clock", "{}");
        console.log(JSON.stringify([atLimit, view(count), holdText(count), sizeAtLimit,
          view(size)]));"""
    )
    assert isinstance(result, list)
    at_limit, count, text, size_at_limit, size = result
    assert at_limit == [5000, False]
    assert count["overflow"] and count["items"] == [] and count["units"] == 0
    assert text == (
        "Paused at 12'. Too many updates to hold; the replay will reload when you resume."
    )
    assert size_at_limit == [1, False]
    assert size["overflow"] and size["items"] == [] and size["units"] == 0


# The page's handlers as far as edition and end are concerned (onEdition resets on a new number,
# a repeated edition or end changes nothing). Random streams, applied whole and through a hold,
# must leave the same state, with a restarted hold forcing the reset as resume() does.
EQUIVALENCE = """
const model = () => ({ edition: 0, ended: false, log: [] });
const step = (s, type, data) => {
  if (type === "edition") {
    let m;
    try { m = JSON.parse(data); } catch { return; }
    if (m === null) return;
    if (m.edition !== s.edition) { s.log = []; s.ended = false; }
    s.edition = m.edition;
  } else if (type === "end") {
    s.ended = true;
  } else {
    s.log.push(data);
  }
};
let seed = 7;
const rand = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % n; };
const pick = () => {
  const r = rand(12);
  if (r < 3) return ["edition", r === 0 ? ["{oops", "null", "5", "{}"][rand(4)] : ed(rand(3))];
  if (r < 5) return ["end", ""];
  return ["clock", "m" + rand(1000)];
};
const failures = [];
for (let run = 0; run < 3000; run++) {
  const before = [...Array(rand(5))].map(pick);
  const during = [...Array(rand(12))].map(pick);
  const whole = model();
  for (const [t, d] of before) step(whole, t, d);
  const paused = JSON.parse(JSON.stringify(whole));
  const hold = newHold(paused.edition, paused.ended, "1'");
  for (const [t, d] of during) { step(whole, t, d); holdMessage(hold, t, d); }
  if (hold.restarted) paused.edition = null;
  for (const [t, d] of hold.items) step(paused, t, d);
  if (JSON.stringify(whole) !== JSON.stringify(paused)) failures.push([before, during]);
}
console.log(JSON.stringify(failures.slice(0, 3)));
"""


def test_reduced_holds_leave_the_same_state_as_applying_every_message() -> None:
    assert _run_hold(HOLD_RUNNER + EQUIVALENCE) == []
