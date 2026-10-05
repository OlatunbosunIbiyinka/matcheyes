"""Enforces the FACTS / ANALYTICS / AI REASONING / PRESENTATION separation (ADR-0002).

Statically parses imports so a violation fails CI before it can reach a demo.
"""

import pytest

from tests.support.imports import SRC_ROOT, imported_modules, python_files

ALLOWED_INTERNAL_DEPENDENCIES: dict[str, set[str]] = {
    "domain": set(),
    "ingestion": {"domain"},
    "analytics": {"domain"},
    "agents": {"domain", "analytics"},
    "orchestration": {"domain", "ingestion", "analytics", "agents"},
    "personalization": {"domain", "analytics", "agents", "orchestration"},
    "api": {"domain", "ingestion", "analytics", "agents", "orchestration", "personalization"},
}

AI_AND_CLOUD_SDK_PREFIXES = ("agent_framework", "openai", "azure", "anthropic", "langchain")
LAYERS_WITHOUT_AI = ("domain", "ingestion", "analytics")


def test_every_layer_package_exists() -> None:
    for layer in ALLOWED_INTERNAL_DEPENDENCIES:
        assert (SRC_ROOT / "matcheyes" / layer / "__init__.py").is_file(), layer


@pytest.mark.parametrize("layer", sorted(ALLOWED_INTERNAL_DEPENDENCIES))
def test_layer_only_imports_allowed_layers(layer: str) -> None:
    allowed = ALLOWED_INTERNAL_DEPENDENCIES[layer] | {layer}
    for path in python_files(f"matcheyes/{layer}"):
        for module in imported_modules(path):
            parts = module.split(".")
            if parts[0] != "matcheyes" or len(parts) < 2:
                continue
            target = parts[1]
            if target in ALLOWED_INTERNAL_DEPENDENCIES:
                assert target in allowed, f"{path.name}: '{layer}' must not import '{target}'"


@pytest.mark.parametrize("layer", LAYERS_WITHOUT_AI)
def test_deterministic_layers_do_not_import_ai_or_cloud_sdks(layer: str) -> None:
    for path in python_files(f"matcheyes/{layer}"):
        for module in imported_modules(path):
            assert not module.startswith(AI_AND_CLOUD_SDK_PREFIXES), (
                f"{path.name}: deterministic layer '{layer}' imports '{module}'"
            )
