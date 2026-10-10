"""Stage 8 security: the broadcast surface presents canonical cues and nothing else (ADR-0014).

* Broadcast compilation is pure: no persistence, clock, randomness, network or process modules,
  and no reasoning model; its contracts carry no hidden-truth vocabulary or insight internals.
* The API uses a small allow-list of standard-library modules, writes nothing, reads no
  environment, never names the generator, the evaluator or their files, and has no write route.
* The static surface has no inline script or style, no HTML injection sinks, no dynamic code,
  no storage or cookies and no third-party origin; it reads only cue fields it renders, so it
  cannot compute a claim. The CSP forbids inline and dynamic code.
* Runtime probe: serving a match from disk, in a clean interpreter, loads neither the generator
  nor the evaluator, and the cues it serves name no scenario.

Scenario words and club names are also scanned for these layers by test_no_scenario_decoding.
"""

import json
import re
from pathlib import Path

import pytest

from matcheyes.api.server import CSP, STATIC_DIR, STATIC_FILES
from matcheyes.broadcast.contracts import (
    Cue,
    CueTimeline,
    InsightSource,
    MomentSource,
    RetractionSource,
    StatusSource,
)
from tests.architecture.test_agent_isolation import _hidden_vocabulary
from tests.architecture.test_stage7_security import PERSISTENCE
from tests.support.imports import imported_modules, python_files

BROADCAST = python_files("matcheyes/broadcast")
API = python_files("matcheyes/api")
STATIC = {name: (STATIC_DIR / name).read_text("utf-8") for name, _ in STATIC_FILES.values()}
HTML, JS = STATIC["index.html"], STATIC["app.js"]

IMPURE = ("time", "datetime", "random", "secrets", "uuid", "os", "socket", "http", "urllib")
IMPURE += ("threading", "asyncio", "multiprocessing", "concurrent", "sqlite3", "tempfile")
REASONING = ("matcheyes.agents.reasoning", "matcheyes.agents.llm")
REASONING_NAMES = ("ReasoningModel", "OpenAICompatibleModel", "LLMSettings", "RuleBasedReasoner")
API_STDLIB = {
    "json",
    "re",
    "sys",
    "time",
    "threading",
    "collections.abc",
    "dataclasses",
    "pathlib",
    "http.server",
    "urllib.parse",
}
"""Modules, or modules whose names are imported; `from http import HTTPStatus` is the only other."""
API_WRITES = re.compile(
    r"write_text|write_bytes|\bopen\(|mkdir|unlink|rmdir|\.rename\(|\.replace\(|sqlite3"
    r"|tempfile|shelve|\bdbm\b|environ|getenv"
)
TRUTH_NAMES = ("matcheyes_synth", "matcheyes_eval", "answer_key", "manifest", "scenario")
TRUTH_WORDS = re.compile(r"scenario|answer|truth|seed|intervention|planted|manifest|S\d{2}_", re.I)


@pytest.mark.parametrize("path", BROADCAST, ids=lambda p: p.name)
def test_broadcast_compilation_is_pure(path: Path) -> None:
    hit = PERSISTENCE.search(path.read_text(encoding="utf-8"))
    assert hit is None, f"{path.name} uses {hit.group(0) if hit else ''}"
    for module in imported_modules(path):
        assert module.split(".")[0] not in IMPURE, f"{path.name} imports {module}"


@pytest.mark.parametrize("path", [*BROADCAST, *API], ids=lambda p: f"{p.parent.name}/{p.name}")
def test_presentation_uses_no_reasoning_model_and_names_no_truth(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for module in imported_modules(path):
        assert module not in REASONING, f"{path.name} imports {module}"
    for name in (*REASONING_NAMES, *TRUTH_NAMES):
        assert name not in text, f"{path.name} names {name}"


@pytest.mark.parametrize("path", API, ids=lambda p: p.name)
def test_the_api_uses_allow_listed_modules_and_writes_nothing(path: Path) -> None:
    for module in imported_modules(path):
        if not module.startswith("matcheyes."):
            allowed = module in API_STDLIB or module.rsplit(".", 1)[0] in API_STDLIB
            allowed = allowed or module in ("http", "http.HTTPStatus")
            assert allowed, f"{path.name} imports {module}"
    hit = API_WRITES.search(path.read_text(encoding="utf-8"))
    assert hit is None, f"{path.name} uses {hit.group(0) if hit else ''}"


def test_cue_contracts_carry_no_hidden_truth_or_insight_internals() -> None:
    hidden = _hidden_vocabulary()
    for model in (Cue, CueTimeline):
        schema = json.dumps(model.model_json_schema()).lower()
        leaked = sorted(n for n in hidden if f'"{n.lower()}"' in schema)
        assert leaked == [], (model.__name__, leaked)
        for internal in ("narrative", "claims", "evidence_ids", "trace", "verification"):
            assert f'"{internal}"' not in schema, (model.__name__, internal)


def test_the_csp_forbids_inline_and_dynamic_code() -> None:
    directives = dict(d.strip().split(" ", 1) for d in CSP.split(";"))
    assert directives["default-src"] == "'none'"
    assert directives["script-src"] == directives["style-src"] == "'self'"
    assert directives["connect-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert "unsafe" not in CSP and "*" not in CSP and "http" not in CSP


def test_the_page_has_no_inline_script_style_or_handlers() -> None:
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", HTML, re.S)
    assert scripts == [(' src="/static/app.js"', "")]
    assert "<style" not in HTML.lower()
    assert not re.search(r"\sstyle\s*=", HTML, re.I)
    assert not re.search(r"\son[a-z]+\s*=", HTML, re.I)
    assert not re.search(r"javascript:", HTML, re.I)


@pytest.mark.parametrize("name", sorted(STATIC))
def test_static_files_load_nothing_from_another_origin_and_name_no_truth(name: str) -> None:
    text = STATIC[name]
    assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", text, re.I), name
    assert not TRUTH_WORDS.search(text), name


def test_the_script_has_no_injection_sink_dynamic_code_or_storage() -> None:
    for sink in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "Function(",
        "setTimeout(",
        "setInterval(",
        "localStorage",
        "sessionStorage",
        "cookie",
        "XMLHttpRequest",
        "WebSocket",
        "import(",
        "postMessage",
    ):
        assert sink not in JS, sink
    assert JS.startswith('"use strict";')
    assert sorted(re.findall(r"fetch\(([^)]*)\)", JS)) == ['"/matches"', "workUrl"]
    assert re.findall(r"new EventSource\(\"([^\"]*)\"", JS) == ["/matches/"]
    work_url = re.findall(r"const workUrl = ([^;]*);", JS)
    assert work_url == [
        '"/matches/" + encodeURIComponent($("match").value) + "/storylines/" +\n'
        '    encodeURIComponent(storyline) + "/revisions/" + encodeURIComponent(String(revision))'
    ]


def test_the_script_renders_cue_sections_and_computes_no_claim() -> None:
    cue_fields = set(re.findall(r"\bcue\.([a-z_]+)", JS))
    assert cue_fields <= {
        "kind",
        "cue_id",
        "sections",
        "source",
        "supersedes",
        "expires_at",
        "interrupt",
        "snapshot_id",
    }, cue_fields
    source_fields = set(re.findall(r"\b(?:src|cue\.source)\.([a-z_]+)", JS))
    assert source_fields <= {
        "verdict",
        "evidence_integrity",
        "score",
        "status",
        "storyline_id",
        "revision",
        "moment",
        "team_id",
        "reason",
    }, source_fields
    assert "s.text" in JS and "sections" in JS
    sources = (MomentSource, InsightSource, RetractionSource, StatusSource)
    emitted = set().union(*(set(model.model_fields) for model in sources))
    assert source_fields <= emitted, source_fields - emitted
    assert cue_fields <= set(Cue.model_fields), cue_fields - set(Cue.model_fields)
    lowered = JS.lower()
    for reasoning in ("because", "caused", "due to", "led to", "hypothes", "metric", "claim"):
        assert reasoning not in lowered, reasoning


_SERVE_PROBE = """
import json, sys, threading
from pathlib import Path
from matcheyes.api.catalog import load_catalog
from matcheyes.api.server import BroadcastApp

catalog = load_catalog(Path(sys.argv[1]))
app = BroadcastApp(catalog, speed=0, loop=False)
(match_id,) = catalog
live, key = app.resolve(match_id, "audience=fan")
with live.condition:
    live.condition.wait_for(lambda: live.edition.done, timeout=600)
_, done, timeline = live.timeline(key)
app.close()
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("matcheyes_synth", "matcheyes_eval"))
print(json.dumps({"loaded": loaded, "done": done, "timeline": timeline.model_dump_json()}))
"""


@pytest.mark.integration
def test_serving_never_loads_truth(tmp_path: Path) -> None:
    import subprocess
    import sys

    from matcheyes.ingestion.io import write_observable_match
    from tests.lifecycle.support import in_progress

    match = in_progress()
    write_observable_match(match, tmp_path / match.info.match_id)
    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", _SERVE_PROBE, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["loaded"] == [] and report["done"] is True
    served = report["timeline"]
    assert CueTimeline.model_validate_json(served).cues
    assert not re.search(r"S\d{2}_|scenario|answer_key|planted|intervention", served)
