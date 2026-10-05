"""Stage 5 security and hidden-truth audit.

* No credentials in the repository; secrets stay in an ignored .env.
* No dynamic code execution anywhere in the engine.
* Whitelist audit: every key a model is shown is a contract field, a tool fact name produced by
  code on the control match, an argument name or a hypothesis label; no value is a scenario ID,
  intervention ID or generator label.
* Runtime probe: a full investigation in a clean interpreter, from an observable match on disk,
  loads neither the generator nor the evaluator, and its model payloads carry no truth.
"""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from matcheyes.agents.contracts import HypothesisKind
from matcheyes.agents.llm import fence
from matcheyes.agents.reasoning import AgentTask, RuleBasedReasoner
from matcheyes.agents.tools import TOOL_SPECS
from matcheyes.orchestration.investigation import investigate_match
from matcheyes_synth.truth import InterventionKind
from tests.support.imports import SRC_ROOT, python_files
from tests.synth.generated import SCENARIO_IDS, generated

REPO = SRC_ROOT.parent
SCANNED = ("src", "tests", "docs", "scripts", ".github")
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(
        r"(?i:api[_-]?key|secret|password|token)\w*\s*[:=]\s*['\"]"
        r"(?=[^'\"]*[a-z])(?=[^'\"]*\d)[A-Za-z0-9+/_=-]{16,}['\"]"
    ),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{24,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)AccountKey=[A-Za-z0-9+/=]{20,}"),
    re.compile(r"\b[0-9a-f]{32}\b"),
)
ALLOWED_MARKERS = ("not-a-real", "example", "placeholder")


def _repo_files() -> list[Path]:
    files = [REPO / name for name in ("README.md", "pyproject.toml", ".env.example")]
    for folder in SCANNED:
        root = REPO / folder
        if root.exists():
            files += [
                p
                for p in root.rglob("*")
                if p.is_file()
                and p.suffix
                in {".py", ".md", ".toml", ".yml", ".yaml", ".ps1", ".json", ".txt", ".example"}
                and "__pycache__" not in p.parts
            ]
    return [f for f in files if f.exists()]


def test_no_credentials_in_the_repository() -> None:
    found = []
    for path in _repo_files():
        for number, line in enumerate(path.read_text("utf-8", errors="ignore").splitlines(), 1):
            if any(m in line.lower() for m in ALLOWED_MARKERS):
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(line) and "re.compile" not in line:
                    found.append(f"{path.relative_to(REPO)}:{number}")
    assert not found, found


def test_secrets_file_is_ignored() -> None:
    ignored = (REPO / ".gitignore").read_text("utf-8").splitlines()
    assert ".env" in ignored and ".env.*" in ignored


DYNAMIC_CODE = re.compile(
    r"(?<![.\w])(eval|exec|compile|__import__)\(|\b(pickle|subprocess|importlib|marshal)\b"
    r"|os\.system"
)


@pytest.mark.parametrize("path", python_files("matcheyes"), ids=lambda p: p.name)
def test_engine_has_no_dynamic_code_execution(path: Path) -> None:
    hit = DYNAMIC_CODE.search(path.read_text(encoding="utf-8"))
    assert hit is None, f"{path.name} uses {hit.group(0) if hit else ''}"


# --- model payload whitelist ------------------------------------------------------------------


class Capture:
    """Reference reasoner that records exactly what a model would be sent."""

    def __init__(self) -> None:
        self.base = RuleBasedReasoner()
        self.name = "capture"
        self.payloads: list[str] = []

    def respond(self, task: AgentTask) -> str:
        self.payloads.append(fence(task))
        return self.base.respond(task)


def _schema_keys(schema: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            keys |= set(properties)
        for value in schema.values():
            keys |= _schema_keys(value)
    elif isinstance(schema, list):
        for value in schema:
            keys |= _schema_keys(value)
    return keys


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def _values(node: Any) -> set[str]:
    if isinstance(node, dict):
        return {v for x in node.values() for v in _values(x)}
    if isinstance(node, list):
        return {v for x in node for v in _values(x)}
    return {node.lower()} if isinstance(node, str) else set()


def _payloads(scenario: str) -> list[Any]:
    capture = Capture()
    investigate_match(generated(scenario).observable, capture)
    return [json.loads(p.split("\n")[1]) for p in capture.payloads]


JSON_SCHEMA_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "default",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
        "$ref",
        "$defs",
        "const",
        "format",
        "propertyNames",
        "prefixItems",
    }
)
"""The case file lists each tool with its argument JSON schema; these are schema keywords."""


@cache
def _allowed_keys() -> frozenset[str]:
    allowed = _schema_keys(AgentTask.model_json_schema()) | JSON_SCHEMA_KEYWORDS
    for spec in TOOL_SPECS.values():
        allowed |= _schema_keys(spec.arguments.model_json_schema())
    allowed |= {k.value for k in HypothesisKind}
    tools_source = (SRC_ROOT / "matcheyes" / "agents" / "tools.py").read_text("utf-8")
    allowed |= set(re.findall(r'"([a-z][a-z0-9_]*)":', tools_source))
    return frozenset(allowed)


@pytest.mark.integration
@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_model_payloads_contain_only_observable_information(scenario: str) -> None:
    spec = generated(scenario).truth.scenario
    forbidden_values = {spec.scenario_id.lower(), *(k.value for k in InterventionKind)}
    forbidden_values |= {i.intervention_id.lower() for i in spec.interventions}
    forbidden_words = ("planted", "twin", "decoy", "scenario", "intervention", "counterfactual")
    allowed = _allowed_keys()
    for payload in _payloads(scenario):
        unknown = _keys(payload) - allowed
        assert not unknown, f"keys not in the observable whitelist: {sorted(unknown)}"
        values = _values(payload)
        assert not values & forbidden_values
        text = json.dumps(payload).lower()
        assert not [w for w in forbidden_words if re.search(rf"\b{w}", text)]


_INVESTIGATION_PROBE = """
import json, sys
from pathlib import Path
from matcheyes.agents.llm import fence
from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.ingestion.io import load_observable_match
from matcheyes.orchestration.investigation import investigate_match

class Capture:
    name = "capture"
    def __init__(self):
        self.base, self.payloads = RuleBasedReasoner(), []
    def respond(self, task):
        self.payloads.append(fence(task))
        return self.base.respond(task)

capture = Capture()
result = investigate_match(load_observable_match(Path(sys.argv[1])), capture)
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("matcheyes_synth", "matcheyes_eval"))
text = "\\n".join(capture.payloads).lower()
print(json.dumps({"loaded": loaded, "records": len(result.records),
                  "hits": [w for w in sys.argv[2:] if w.lower() in text]}))
"""


@pytest.mark.integration
def test_investigation_runtime_never_loads_truth(tmp_path: Path) -> None:
    import subprocess
    import sys

    from matcheyes.ingestion.io import write_observable_match

    match = generated("S06_impact_substitution")
    write_observable_match(match.observable, tmp_path)
    spec = match.truth.scenario
    probes = [spec.scenario_id, *(k.value for k in InterventionKind), "planted", "twin", "decoy"]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", _INVESTIGATION_PROBE, str(tmp_path), *probes],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["loaded"] == []
    assert report["records"] > 0
    assert report["hits"] == []
