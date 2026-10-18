"""Stage 10.3 serving and frontend boundaries (ADR-0016).

The stream gains one event type, `stats`, built only through the statistics contract. The
frontend (step 3) listens to it and reads an allow-listed set of statistics fields: it shows only
the server's `display` strings, uses `value` only to size the bars, and never reads the reason
codes or any other field of the message.
"""

import json
import re
import shutil
import subprocess

import pytest

from matcheyes.broadcast.stats import MEASURES, STATS_VERSION, StatsStatus, Unit, ValueStatus
from tests.support.imports import SRC_ROOT, imported_modules

STATIC = SRC_ROOT / "matcheyes" / "api" / "static"
LIVE = SRC_ROOT / "matcheyes" / "api" / "live.py"
JS = (STATIC / "app.js").read_text("utf-8")
HTML = (STATIC / "index.html").read_text("utf-8")
STREAM_EVENTS = {"edition", "clock", "stats", "cue", "end", "unavailable"}
CONNECTION_EVENTS = {"open", "error"}
# The names the statistics code gives each part of a message, and the fields it may read on each.
STATS_FIELDS = {
    "stats": {
        "edition",
        "snapshot_id",
        "stats_version",
        "minute",
        "period",
        "status",
        "measures",
        "teams",
    },
    "measure": {"key", "label", "unit", "note", "sample_of", "min_sample"},
    "teamStats": {"team_id", "values"},
    "figure": {"display", "status", "sample", "value"},
}
STATS_ONLY_FIELDS = ("stats_version", "measures", "teams", "min_sample", "sample_of")
SECTION = JS.split("// --- team statistics", 1)[1].split("\n// --- ", 1)[0]


def _js_object_keys(name: str) -> list[str]:
    body = re.search(rf"const {name} = \{{(.*?)\}};", JS, re.S)
    assert body, name
    return re.findall(r"([a-z_]+):", body.group(1))


def _js_set(name: str) -> set[str]:
    body = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", JS, re.S)
    assert body, name
    return set(re.findall(r'"([a-z_]+)"', body.group(1)))


def test_the_frontend_listens_to_the_stream_events_including_statistics() -> None:
    listened = re.findall(r'stream\.addEventListener\(\s*"([a-z_]+)"', JS)
    assert set(listened) == STREAM_EVENTS | CONNECTION_EVENTS
    assert len(listened) == len(set(listened))
    assert "onmessage" not in JS


def test_the_frontend_reads_only_allow_listed_statistics_fields() -> None:
    for name, allowed in STATS_FIELDS.items():
        read = set(re.findall(rf"\b{name}\.([a-z_]+)", SECTION))
        assert read, name
        assert read <= allowed, (name, read - allowed)
    for name in ("stats", "teamStats", "figure"):
        assert not re.search(rf"\b{name}\[", JS), name
    assert not re.search(r"\b(?:stats|figure|teamStats)\.(?:reason|watermark|clock_ms|as_of)\b", JS)


def test_statistics_fields_are_read_only_in_the_statistics_section() -> None:
    outside = JS.replace(SECTION, "")
    for name in STATS_ONLY_FIELDS:
        assert not re.search(rf"\b{name}\b", outside), name
    for name in ("stats", "teamStats", "figure"):
        assert not re.search(rf"\b{name}\.", outside), name


def test_values_only_size_the_bars_and_text_is_the_server_display() -> None:
    bar_value = re.search(r"function barValue\(figure\) \{(.*?)\n\}", SECTION, re.S)
    assert bar_value
    assert "figure.value" not in SECTION.replace(bar_value.group(1), "")
    assert re.search(r'el\("span", [^)]*figure\.display\)', SECTION)
    assert not re.search(
        r"toFixed\([^)]*\)\s*\+\s*\"%\"|Math\.round|toLocaleString|Intl\.", SECTION
    )


def test_missing_values_are_never_shown_as_zero() -> None:
    assert not re.search(r"(\|\||\?\?)\s*0\b", SECTION)
    assert '"Not available"' in SECTION
    assert "STATS_NOT_CARRIED" in SECTION


def test_the_frontend_mirrors_the_statistics_contract() -> None:
    assert _js_object_keys("STAT_BARS") == [m.key for m in MEASURES]
    assert set(_js_object_keys("STAT_UNITS")) == {u.value for u in Unit}
    assert _js_set("STAT_STATES") == {s.value for s in StatsStatus}
    assert _js_set("FIGURE_STATES") == {s.value for s in ValueStatus}
    assert f'const STATS_VERSION = "{STATS_VERSION}";' in JS


def test_the_statistics_section_sits_in_the_match_centre_without_markers() -> None:
    chart, stats, main = (HTML.index(s) for s in ('id="chart"', 'id="stats"', "<main"))
    assert chart < stats < main
    section = HTML[stats : HTML.index("</section>", stats)]
    assert 'role="table"' in section and 'aria-labelledby="stats-heading"' in section
    assert "context only" in section and "not linked to match events or insights" in section
    assert "<svg" not in section


def test_the_column_headers_sit_inside_the_statistics_table() -> None:
    section = HTML[HTML.index('id="stats"') : HTML.index("</section>", HTML.index('id="stats"'))]
    table = section[section.index("<table") : section.index("</table>")]
    head = table[table.index("<thead") : table.index("</thead>")]
    assert 'role="rowgroup"' in head and head.count('role="row"') == 1
    headers = re.findall(r'<th\b[^>]*role="columnheader"[^>]*scope="col"', head)
    assert len(headers) == 3
    assert 'id="stats-col-home"' in head and 'id="stats-col-away"' in head
    assert table.index("</thead>") < table.index('id="stats-rows"')
    assert 'textContent = m.home.name + ", home"' in JS
    assert 'textContent = m.away.name + ", away"' in JS


def test_the_hidden_column_headers_are_clipped_inside_the_table() -> None:
    css = (STATIC / "app.css").read_text("utf-8")
    table = re.search(r"\.stats-table \{([^}]*)\}", css)
    assert table and "position: relative" in table.group(1)
    head = re.search(r"\.stats-head, \.stats-head tr, \.stats-head th \{([^}]*)\}", css)
    assert head
    for rule in (
        "position: absolute",
        "width: 1px",
        "height: 1px",
        "overflow: hidden",
        "clip-path: inset(50%)",
        "padding: 0",
    ):
        assert rule in head.group(1), rule
    assert "display: none" not in head.group(1) and "visibility" not in head.group(1)


def test_each_row_header_names_the_measure_and_its_type() -> None:
    row = re.search(r"function statsRow\(row\) \{(.*?)\n\}", SECTION, re.S)
    assert row
    body = row.group(1)
    assert 'setAttribute("role", "rowheader")' in body and 'head.scope = "row"' in body
    assert 'setAttribute("aria-label", measure.label + ", " + unit.toLowerCase())' in body
    assert 'setAttribute("aria-describedby", note.id)' in body
    assert body.count('setAttribute("role", "cell")') == 0
    assert SECTION.count('cell.setAttribute("role", "cell")') == 1


def _run_statistics_states(script: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    parts = [
        re.search(r"const STATS_VERSION = [^;]*;", JS),
        re.search(r"const STAT_STATES = new Set\([^;]*;", JS),
        *(
            re.search(rf"function {name}\(.*?\n\}}\n", JS, re.S)
            for name in ("statsReadable", "statsState", "statsAnnouncement")
        ),
    ]
    assert all(parts)
    source = "\n".join(p.group(0) for p in parts if p) + "\n" + script
    done = subprocess.run(  # noqa: S603 - fixed interpreter; the page's own source on stdin
        [node, "-"], input=source, capture_output=True, text=True, timeout=60, check=True
    )
    return json.loads(done.stdout)


# Feeds the latest statistics through the page's own state and announcement functions: each step
# is [message, rows shown, behind]; the result is [state, announcement] per step.
STATE_RUNNER = """
const msg = (status, extra) => ({ stats_version: "0.1.0", minute: "21'", status, ...extra });
const V = [msg("valid"), [1], false];
const run = (steps, before = "none") => steps.map(([stats, rows, behind]) => {
  const after = statsState(stats, rows, true, behind);
  const heard = statsAnnouncement(before, after, "notice:" + after);
  before = after;
  return [after, heard];
});
"""


def test_each_distinct_statistics_state_is_announced_once() -> None:
    result = _run_statistics_states(
        STATE_RUNNER
        + """console.log(JSON.stringify(run([
          V, [msg("invalid"), null, false], [msg("invalid"), null, false],
          [msg("unavailable"), null, false], [msg("unavailable"), null, false],
          [msg("valid"), null, false], [{ ...msg("valid"), stats_version: "9" }, null, false],
          V, V, [msg("valid"), [1], true], [msg("valid"), [1], true], V,
          [null, null, true],
        ])));"""
    )
    assert result == [
        ["valid", ""],
        ["invalid", "notice:invalid"],
        ["invalid", ""],
        ["unavailable", "notice:unavailable"],
        ["unavailable", ""],
        ["unreadable", "notice:unreadable"],
        ["unreadable", ""],
        ["valid", "Team statistics are available again."],
        ["valid", ""],
        ["stale", "notice:stale"],
        ["stale", ""],
        ["valid", "Team statistics are up to date again."],
        ["missing", "notice:missing"],
    ]


def test_an_edition_restart_starts_the_announcements_afresh() -> None:
    reset = re.search(r"function statsReset\(\) \{(.*?)\n\}", SECTION, re.S)
    assert reset and 'statsAnnounced = "none";' in reset.group(1)
    assert 'let statsAnnounced = "none";' in SECTION
    result = _run_statistics_states(
        STATE_RUNNER
        + """console.log(JSON.stringify([
          run([V, [msg("invalid"), null, false]]),
          run([[null, null, false], V]),
          run([[null, null, false], [msg("invalid"), null, false]]),
        ]));"""
    )
    assert result == [
        [["valid", ""], ["invalid", "notice:invalid"]],
        [["none", ""], ["valid", ""]],
        [["none", ""], ["invalid", "notice:invalid"]],
    ]


def test_announcements_reuse_the_visible_notice_and_name_no_internals() -> None:
    render = re.search(r"function renderStats\(\) \{(.*?)\n\}", SECTION, re.S)
    assert render
    body = render.group(1)
    assert "statsAnnouncement(statsAnnounced, state, notice)" in body
    assert "if (heard && !catchingUp) announce(heard);" in body
    assert "status.textContent = notice;" in body
    assert not re.search(r"announce\([^)]*(reason|snapshot_id|stats_version)", SECTION)


def test_the_live_driver_reaches_analytics_only_through_the_statistics_contract() -> None:
    modules = imported_modules(LIVE)
    assert "matcheyes.broadcast.stats" in modules
    assert not any(m.startswith("matcheyes.analytics") for m in modules)
