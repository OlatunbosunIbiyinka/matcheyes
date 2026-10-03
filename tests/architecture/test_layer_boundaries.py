"""Enforces the FACTS / ANALYTICS / AI REASONING / PRESENTATION separation (ADR-0002).

Statically parses imports so a violation fails CI before it can reach a demo.
"""

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "matcheyes"

ALLOWED_INTERNAL_DEPENDENCIES: dict[str, set[str]] = {
    "domain": set(),
    "ingestion": {"domain"},
    "analytics": {"domain"},
    "agents": {"domain", "analytics"},
    "orchestration": {"domain", "ingestion", "analytics", "agents"},
    "api": {"domain", "ingestion", "analytics", "agents", "orchestration"},
}

AI_AND_CLOUD_SDK_PREFIXES = ("agent_framework", "openai", "azure", "anthropic", "langchain")
LAYERS_WITHOUT_AI = ("domain", "ingestion", "analytics")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            # `from matcheyes import agents` names the layer in the alias, not the module.
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _layer_files(layer: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


def test_every_layer_package_exists() -> None:
    for layer in ALLOWED_INTERNAL_DEPENDENCIES:
        assert (PACKAGE_ROOT / layer / "__init__.py").is_file(), layer


@pytest.mark.parametrize("layer", sorted(ALLOWED_INTERNAL_DEPENDENCIES))
def test_layer_only_imports_allowed_layers(layer: str) -> None:
    allowed = ALLOWED_INTERNAL_DEPENDENCIES[layer] | {layer}
    for path in _layer_files(layer):
        for module in _imported_modules(path):
            parts = module.split(".")
            if parts[0] != "matcheyes" or len(parts) < 2:
                continue
            target = parts[1]
            if target in ALLOWED_INTERNAL_DEPENDENCIES:
                assert target in allowed, f"{path.name}: '{layer}' must not import '{target}'"


@pytest.mark.parametrize("layer", LAYERS_WITHOUT_AI)
def test_deterministic_layers_do_not_import_ai_or_cloud_sdks(layer: str) -> None:
    for path in _layer_files(layer):
        for module in _imported_modules(path):
            assert not module.startswith(AI_AND_CLOUD_SDK_PREFIXES), (
                f"{path.name}: deterministic layer '{layer}' imports '{module}'"
            )
