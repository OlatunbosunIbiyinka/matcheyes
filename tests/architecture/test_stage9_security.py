"""Stage 9 boundaries: the hosted model stays an optional, isolated, untrusted reasoner.

* Only `matcheyes.agents.entra` imports a cloud SDK, and only the model factory loads it.
* The engine's runtime dependency is still pydantic alone; importing the engine (both CLIs
  included) loads no cloud SDK.
* The public serving path (lifecycle, broadcast, api) never imports the model adapter, the wire
  format or the Entra module, so public web traffic cannot reach a live model; `serve` never
  builds one, even with a model configured in its environment.
* The container image carries no credentials, no cloud SDK, only the engine wheel, observable
  match data and transcripts that match their pins.
"""

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.support.imports import SRC_ROOT, imported_modules, python_files

REPO = SRC_ROOT.parent
TRUTH = re.compile(r"hidden|manifest|answer|truth|scenario|intervention", re.I)
CLOUD_PREFIXES = ("azure", "openai", "agent_framework", "anthropic", "langchain")
ENTRA = "matcheyes.agents.entra"
MODEL_MODULES = ("matcheyes.agents.llm", "matcheyes.agents.wire", ENTRA)
PUBLIC_LAYERS = ("lifecycle", "broadcast", "api")


def _rel(path: object) -> str:
    return str(path).replace("\\", "/").split("/src/", 1)[1]


def test_only_the_entra_module_imports_a_cloud_sdk() -> None:
    importers = {
        _rel(path)
        for package in ("matcheyes", "matcheyes_eval", "matcheyes_synth")
        for path in python_files(package)
        if any(m.startswith(CLOUD_PREFIXES) for m in imported_modules(path))
    }
    assert importers == {"matcheyes/agents/entra.py"}


def test_only_the_model_factory_imports_the_entra_module() -> None:
    importers = {
        _rel(path)
        for package in ("matcheyes", "matcheyes_eval", "matcheyes_synth")
        for path in python_files(package)
        if any(m == ENTRA or m.startswith(ENTRA + ".") for m in imported_modules(path))
    }
    assert importers == {"matcheyes/agents/llm.py"}


def test_the_runtime_dependency_is_still_pydantic_alone() -> None:
    pyproject = tomllib.loads((SRC_ROOT.parent / "pyproject.toml").read_text("utf-8"))
    assert [d.split(">")[0] for d in pyproject["project"]["dependencies"]] == ["pydantic"]
    assert pyproject["project"]["optional-dependencies"]["azure"][0].startswith("azure-identity")


def test_importing_the_engine_and_both_clis_loads_no_cloud_sdk() -> None:
    probe = (
        "import sys, matcheyes.__main__, matcheyes_eval.__main__, matcheyes.agents.llm;"
        f"print(sorted(m for m in sys.modules if m.startswith({CLOUD_PREFIXES!r})))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and argument list
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=SRC_ROOT.parent,
        env={"PYTHONPATH": str(SRC_ROOT), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
    )
    assert result.stdout.strip() == "[]"


def test_the_public_serving_path_never_imports_the_model_adapter() -> None:
    for layer in PUBLIC_LAYERS:
        for path in python_files(f"matcheyes/{layer}"):
            for module in imported_modules(path):
                assert not module.startswith(MODEL_MODULES), f"{_rel(path)} imports {module}"


def test_serving_never_builds_a_live_model_even_when_one_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import matcheyes.__main__ as cli
    from tests.lifecycle.support import in_progress

    for name, value in {
        "MATCHEYES_LLM_ENDPOINT": "https://example.invalid/openai/v1/",
        "MATCHEYES_LLM_MODEL": "dep",
        "MATCHEYES_LLM_API_KEY": "sk-not-real",
        "MATCHEYES_LLM_AUTH": "api-key",
    }.items():
        monkeypatch.setenv(name, value)

    def forbidden(*_: object) -> None:
        raise AssertionError("serve touched the live model configuration")

    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    match = in_progress()
    monkeypatch.setattr(cli, "live_model", forbidden)
    monkeypatch.setattr(cli.LLMSettings, "from_env", staticmethod(forbidden))
    monkeypatch.setattr(cli, "load_catalog", lambda _: {match.info.match_id: match})
    monkeypatch.setattr(cli.BroadcastServer, "serve_forever", interrupted)
    argv = ["serve", "x", "--port", "0", "--speed", "0", "--no-loop"]
    assert cli.main([*argv, "--recordings", str(tmp_path)]) == 0
    assert cli.main(argv) == 0


DOCKERFILE = REPO / "Dockerfile"
DEPLOY = REPO / "deploy"


def test_the_image_gets_no_credentials_no_cloud_sdk_and_only_the_engine() -> None:
    text = DOCKERFILE.read_text("utf-8")
    instructions = [line for line in text.splitlines() if line and not line.startswith("#")]
    body = "\n".join(instructions)
    assert not re.search(r"MATCHEYES_LLM|API_KEY|TOKEN|SECRET|PASSWORD|ENDPOINT|AZURE_", body)
    assert "--extra" not in body and "azure" not in body.lower()
    assert [line for line in instructions if line.startswith("ENV ")] == [
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1"
    ]
    copies = [line.split()[1:] for line in instructions if line.startswith("COPY ")]
    sources = {src for parts in copies for src in parts[:-1] if not src.startswith("--")}
    assert sources == {
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "src/matcheyes",
        "deploy/requirements.txt",
        "/wheels",
        "deploy/matches",
        "deploy/recordings",
    }
    assert "USER 10001" in instructions
    assert '"serve"' in body and '"--recordings"' in body
    assert "--require-hashes" in body


def test_the_docker_context_is_an_allow_list() -> None:
    lines = (REPO / ".dockerignore").read_text("utf-8").split()
    assert lines[0] == "*"
    allowed = [line for line in lines if line.startswith("!")]
    assert not any(re.search(r"synth|eval|tests|data|\.env", line) for line in allowed)


def test_the_pinned_runtime_requirements_have_no_cloud_or_network_client() -> None:
    text = (DEPLOY / "requirements.txt").read_text("utf-8")
    names = set(re.findall(r"^([a-z0-9][a-z0-9._-]*)==", text, re.M))
    assert "pydantic" in names
    assert not any(n.startswith(("azure", "msal", "openai", "requests", "httpx")) for n in names)
    blocks = [b for b in re.split(r"\n(?=[a-z])", text) if re.match(r"[a-z0-9._-]+==", b)]
    assert len(blocks) == len(names)
    assert all("--hash=sha256:" in b for b in blocks)


def test_bundled_matches_are_observable_only_and_recordings_match_their_pins() -> None:
    matches, recordings = DEPLOY / "matches", DEPLOY / "recordings"
    if not matches.is_dir():
        pytest.skip("no bundled demo matches")
    from matcheyes.agents.recorded import RecordedModel
    from matcheyes.api.catalog import load_catalog

    names = [p.relative_to(matches).as_posix() for p in matches.rglob("*") if p.is_file()]
    assert not any(TRUTH.search(n) for n in names), names
    catalog = load_catalog(matches)
    pins = json.loads((recordings / "pins.json").read_text("utf-8"))
    assert pins and set(pins) <= set(catalog)
    for match_id, sha in pins.items():
        model = RecordedModel.load(recordings / f"{match_id}.transcript.json.gz", sha)
        assert model.usable, (match_id, model.problems)
        assert model.transcript_sha256 == sha
